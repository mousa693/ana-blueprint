"""Security hardening of the web backend (ana/app): model budgets, the
delete/create race, argon2 memory, client-IP derivation, atomic caps,
log-in lockout, sign-up timing, plus turn idempotency (expected_tick),
input validation, never-show redaction and gentle turns.

Concurrency tests drive one TestClient from several threads (FastAPI runs
the sync handlers in its threadpool, so requests really overlap).
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ana.app import auth as auth_mod
from ana.app import shards as shard_store
from ana.app.main import create_app
from ana.app.models import Cycle, GlobalUsage, User
from ana.app.quotas import today
from ana.app.security import RateLimiter, client_ip
from ana.runtime import JourneyRuntime

from tests.test_app import (ANSWERS, CONCERN_TEXT, CRISIS_TEXT, PASSWORD, act, client, ctx,
                            db_session, err_code, identity_id_of, make_settings, new_journey,
                            signup)
from tests.test_app import app, c, settings  # noqa: F401  (pytest fixtures)
from tests.test_llm import make_gateway


def _global(app) -> int:
    with db_session(app) as db:
        g = db.get(GlobalUsage, today())
        return g.llm_cycles if g else 0


def _usage(c) -> int:
    return c.get("/api/me").json()["usage"]["today"]


# ─────────────────────────────────────────────────────────────
# 1. every model call is budgeted
# ─────────────────────────────────────────────────────────────

def test_global_counter_equals_model_requests(tmp_path):
    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="auto", max_cycles=8), gateway=gw)
    with client(app) as c:
        signup(c)                                   # onboarding: safety screens + seed
        jid, _ = new_journey(c)
        act(c, jid, choice_index=0)
        act(c, jid, text="I walk toward the light")
        c.patch("/api/identity", json={"name": "Mara"})
        c.post(f"/api/journeys/{jid}/end")
        assert len(rec.requests) > 5
        assert _global(app) == len(rec.requests)


def test_create_and_onboarding_count_against_the_daily_cap(tmp_path):
    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="auto", daily_cycles_per_user=3,
                                   max_active_journeys=1), gateway=gw)
    with client(app) as c:
        signup(c)                                   # 1
        assert _usage(c) == 1
        jid, _ = new_journey(c)                     # 2
        assert _usage(c) == 2
        c.post(f"/api/journeys/{jid}/end")
        jid, _ = new_journey(c)                     # 3
        c.post(f"/api/journeys/{jid}/end")
        before = len(rec.requests)
        codes = Counter(c.post("/api/journeys", json={}).status_code for _ in range(5))
        assert codes == {429: 5}
        r = c.post("/api/onboarding", json={"answers": ANSWERS})
        assert r.status_code == 429 and err_code(r) == "quota_exceeded"
        assert len(rec.requests) == before          # refused before any model call
        assert _global(app) == len(rec.requests)


def test_create_identity_and_onboarding_are_rate_limited(tmp_path):
    app = create_app(make_settings(tmp_path, create_per_minute=3, identity_per_minute=4,
                                   onboarding_per_minute=2, max_active_journeys=50))
    with client(app) as c:
        signup(c)                                   # onboarding #1
        assert c.post("/api/onboarding", json={"answers": ANSWERS}).status_code == 201
        r = c.post("/api/onboarding", json={"answers": ANSWERS})
        assert r.status_code == 429 and err_code(r) == "rate_limited" and "retry-after" in r.headers
        codes = [c.post("/api/journeys", json={}).status_code for _ in range(4)]
        assert codes == [201, 201, 201, 429]
        codes = [c.patch("/api/identity", json={"name": f"M{i}"}).status_code for i in range(5)]
        assert codes == [200, 200, 200, 200, 429]


def test_safety_model_bucket_falls_back_to_deterministic_floor(tmp_path):
    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="auto", safety_model_per_hour=2,
                                   identity_per_minute=100), gateway=gw)
    with client(app) as c:
        signup(c)
        ctx(app).limiter_safety.reset()             # onboarding's screens used some
        base = len(rec.requests)
        for i in range(6):
            assert c.patch("/api/identity", json={"name": f"Mira{i}"}).status_code == 200
        assert len(rec.requests) - base == 2        # only the bucket's 2 model screens
        # the deterministic floor still runs once the bucket is empty
        r = c.patch("/api/identity", json={"name": CRISIS_TEXT})
        assert r.json()["safety"]["level"] == "crisis"
        assert _global(app) == len(rec.requests)


def test_unchanged_name_is_not_rescreened(tmp_path):
    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="auto"), gateway=gw)
    with client(app) as c:
        signup(c)
        c.patch("/api/identity", json={"name": "Ines"})
        base = len(rec.requests)
        c.patch("/api/identity", json={"name": "Ines"})
        assert len(rec.requests) == base


# ─────────────────────────────────────────────────────────────
# 2. account deletion vs concurrent creates
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("delay", [0.0, 0.02, 0.05, 0.1])
def test_delete_races_create_leaves_nothing(tmp_path, delay):
    app = create_app(make_settings(tmp_path, max_active_journeys=50, create_per_minute=100))
    with TestClient(app, base_url="http://testserver", raise_server_exceptions=False) as c:
        signup(c)
        ck = c.cookies.get("ana_session")
        iid = identity_id_of(app, "mira@example.com")

        def create(_):
            return c.post("/api/journeys", json={}, cookies={"ana_session": ck}).status_code

        def delete():
            time.sleep(delay)
            return c.request("DELETE", "/api/me", json={"password": PASSWORD},
                             cookies={"ana_session": ck}).status_code

        with ThreadPoolExecutor(10) as ex:
            fs = [ex.submit(create, i) for i in range(8)]
            fd = ex.submit(delete)
            codes = [f.result() for f in fs]
            assert fd.result() == 204
        assert set(codes) <= {201, 401}             # never a 500
        assert len(ctx(app).cache) == 0
        settings = ctx(app).settings
    assert shard_store.count_shards(settings.shard_db, iid) == 0


# ─────────────────────────────────────────────────────────────
# 3. argon2: parameters, rehash, bounded concurrency
# ─────────────────────────────────────────────────────────────

def test_argon2_parameters_and_rehash_on_login(app, c):
    from argon2 import PasswordHasher
    signup(c, onboard=False)
    with db_session(app) as db:
        u = db.execute(select(User)).scalars().one()
        assert u.password_hash.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
        u.password_hash = PasswordHasher().hash(PASSWORD)     # library-default params
        db.commit()
    c.cookies.clear()
    assert c.post("/api/auth/login", json={"email": "mira@example.com",
                                           "password": PASSWORD}).status_code == 200
    with db_session(app) as db:
        h = db.execute(select(User)).scalars().one().password_hash
    assert "m=19456,t=2,p=1" in h
    assert c.post("/api/auth/login", json={"email": "mira@example.com",
                                           "password": PASSWORD}).status_code == 200


def test_hashing_is_bounded_and_fails_fast(tmp_path):
    app = create_app(make_settings(tmp_path, password_hash_concurrency=2,
                                   password_hash_wait_seconds=0.1))
    with client(app) as c:
        sem = auth_mod._hash_slots
        assert sem.acquire(timeout=1) and sem.acquire(timeout=1)   # all slots busy
        try:
            t0 = time.monotonic()
            r = c.post("/api/auth/login", json={"email": "a@example.com",
                                                "password": "whatever123"})
            assert r.status_code == 503 and err_code(r) == "busy"
            assert r.headers["retry-after"] == "2" and time.monotonic() - t0 < 1.5
        finally:
            sem.release()
            sem.release()
        assert c.post("/api/auth/login", json={"email": "a@example.com",
                                               "password": "whatever123"}).status_code == 401


def test_parallel_hashing_never_exceeds_the_limit(monkeypatch, tmp_path):
    create_app(make_settings(tmp_path, password_hash_concurrency=2,
                             password_hash_wait_seconds=10))
    live, peak, lk = [0], [0], threading.Lock()
    real = auth_mod._hasher.verify

    def counting(*a, **k):
        with lk:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        try:
            time.sleep(0.02)
            return real(*a, **k)
        finally:
            with lk:
                live[0] -= 1

    class _Hasher:
        verify = staticmethod(counting)

    monkeypatch.setattr(auth_mod, "_hasher", _Hasher())
    with ThreadPoolExecutor(12) as ex:
        list(ex.map(lambda _: auth_mod.verify_password(None, "x" * 12), range(24)))
    assert peak[0] == 2


# ─────────────────────────────────────────────────────────────
# 4. client IP
# ─────────────────────────────────────────────────────────────

def test_client_ip_rules():
    xff = [("x-forwarded-for", "6.6.6.6, 203.0.113.9")]
    assert client_ip(xff, "10.0.0.1") == "10.0.0.1"                       # nothing trusted
    assert client_ip(xff, "10.0.0.1", hops=1) == "203.0.113.9"            # rightmost
    assert client_ip(xff, "10.0.0.1", hops=2) == "6.6.6.6"
    assert client_ip(xff, "10.0.0.1", hops=3) == "10.0.0.1"               # too few → peer
    assert client_ip([("x-forwarded-for", "6.6.6.6"), ("x-forwarded-for", "198.51.100.7")],
                     "p", hops=1) == "198.51.100.7"                       # repeated headers
    fly = [("fly-client-ip", "198.51.100.4")] + xff
    assert client_ip(fly, "p", header="fly-client-ip") == "198.51.100.4"
    assert client_ip([("fly-client-ip", "junk")] + xff, "p", header="fly-client-ip",
                     hops=1) == "203.0.113.9"                             # bad value falls through
    assert client_ip([("x-forwarded-for", "not-an-ip")], "p", hops=1) == "p"
    assert client_ip([("x-forwarded-for", "[2001:db8::1]:443")], "p", hops=1) == "2001:db8::1"


def _login_codes(c, spoof, n=12, **extra_headers):
    out = Counter()
    for i in range(n):
        h = dict(extra_headers)
        h["x-forwarded-for"] = (f"10.{i}.0.1, 203.0.113.9" if spoof else "203.0.113.9")
        out[c.post("/api/auth/login", json={"email": f"v{i}@example.com",
                                            "password": "guessguess1"}, headers=h).status_code] += 1
    return out


@pytest.mark.parametrize("kw", [{}, {"trusted_proxy_hops": 1},
                                {"client_ip_header": "fly-client-ip"}])
def test_spoofed_leftmost_xff_does_not_bypass_ip_limit(tmp_path, kw):
    app = create_app(make_settings(tmp_path, auth_per_ip_per_minute=5, **kw))
    extra = {"fly-client-ip": "198.51.100.4"} if kw.get("client_ip_header") else {}
    with client(app) as c:
        assert _login_codes(c, spoof=True, **extra) == {401: 5, 429: 7}


def test_distinct_real_clients_get_distinct_buckets(tmp_path):
    app = create_app(make_settings(tmp_path, auth_per_ip_per_minute=2, trusted_proxy_hops=1))
    with client(app) as c:
        codes = Counter(c.post("/api/auth/login", json={"email": "v@example.com",
                                                        "password": "guessguess1"},
                               headers={"x-forwarded-for": f"198.51.100.{i}"}).status_code
                        for i in range(6))
        assert codes == {401: 6}


def test_from_env_reads_client_ip_settings():
    from ana.app.config import ConfigError, Settings
    s = Settings.from_env({"ANA_DEV": "1", "ANA_CLIENT_IP_HEADER": "Fly-Client-IP",
                           "ANA_TRUSTED_PROXY_HOPS": "1", "ANA_SAFETY_MODEL_PER_HOUR": "10",
                           "ANA_PASSWORD_HASH_CONCURRENCY": "3"})
    assert s.client_ip_header == "fly-client-ip" and s.trusted_proxy_hops == 1
    assert s.safety_model_per_hour == 10 and s.password_hash_concurrency == 3
    with pytest.raises(ConfigError):
        Settings.from_env({"ANA_DEV": "1", "ANA_CLIENT_IP_HEADER": "x: y"})


def test_shipped_configs_do_not_trust_every_forwarder():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    fly = (root / "deploy" / "fly.toml").read_text()
    render = (root / "deploy" / "render.yaml").read_text()
    assert 'FORWARDED_ALLOW_IPS = "*"' not in fly and 'ANA_CLIENT_IP_HEADER = "fly-client-ip"' in fly
    assert "FORWARDED_ALLOW_IPS" not in render.split("envVars:")[1].split("#")[0]
    assert "key: ANA_TRUSTED_PROXY_HOPS" in render


# ─────────────────────────────────────────────────────────────
# 5. caps hold under concurrency
# ─────────────────────────────────────────────────────────────

def _threads(n, fn, items):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))


def test_concurrent_creates_respect_active_cap(tmp_path):
    app = create_app(make_settings(tmp_path, max_active_journeys=1, create_per_minute=100))
    with client(app) as c:
        signup(c)
        ck = c.cookies.get("ana_session")
        codes = Counter(_threads(12, lambda _: c.post("/api/journeys", json={},
                                                      cookies={"ana_session": ck}).status_code,
                                 range(12)))
        assert codes[201] == 1 and codes[409] == 11
        js = c.get("/api/journeys").json()["journeys"]
        assert sum(j["status"] == "active" for j in js) == 1


def test_concurrent_acts_respect_daily_cap(tmp_path):
    # onboarding (1) + 8 first scenes (8) + 3 turns = 12
    app = create_app(make_settings(tmp_path, max_active_journeys=8, daily_cycles_per_user=12,
                                   create_per_minute=100))
    with client(app) as c:
        signup(c)
        ck = c.cookies.get("ana_session")
        jids = [new_journey(c)[0] for _ in range(8)]
        assert _usage(c) == 9
        codes = Counter(_threads(8, lambda j: c.post(f"/api/journeys/{j}/act",
                                                     json={"choice_index": 0},
                                                     cookies={"ana_session": ck}).status_code,
                                 jids))
        assert codes == {200: 3, 429: 5}
        assert _usage(c) == 12
        with db_session(app) as db:
            assert len(db.execute(select(Cycle)).scalars().all()) == 3


def test_concurrent_turns_respect_global_budget(tmp_path):
    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="auto", daily_llm_cycles_total=1000,
                                   max_active_journeys=8, create_per_minute=100), gateway=gw)
    with client(app) as c:
        signup(c)
        ck = c.cookies.get("ana_session")
        jids = [new_journey(c)[0] for _ in range(8)]
        with db_session(app) as db:
            db.get(GlobalUsage, today()).llm_cycles = 999
            db.commit()
        base = len(rec.requests)
        res = _threads(8, lambda j: c.post(f"/api/journeys/{j}/act", json={"choice_index": 0},
                                           cookies={"ana_session": ck}).json(), jids)
        made = len(rec.requests) - base
        dreamed = sum(r["pending"]["scene"]["source"] == "llm" for r in res)
        assert dreamed == 1                          # exactly one reservation granted
        assert _global(app) == 999 + made            # and counted exactly


def test_quota_reservation_is_given_back_when_the_turn_fails(app, c):
    signup(c)
    jid, pending = new_journey(c)
    before = _usage(c)
    assert act(c, jid, choice_index=len(pending["scene"]["choices"])).status_code == 422
    r = act(c, jid, text=CRISIS_TEXT)
    assert r.json()["safety"]["level"] == "crisis"
    assert _usage(c) == before


# ─────────────────────────────────────────────────────────────
# 6. failed log-ins cannot lock the owner out
# ─────────────────────────────────────────────────────────────

def _login(app, ip, password):
    with TestClient(app, base_url="http://testserver", client=(ip, 40000)) as c:
        return c.post("/api/auth/login", json={"email": "vic@example.com", "password": password})


def test_attacker_failures_do_not_lock_out_the_owner(tmp_path):
    app = create_app(make_settings(tmp_path))
    with client(app) as c:
        signup(c, email="vic@example.com", onboard=False)
    for _ in range(8):
        assert _login(app, "198.51.100.66", "wrong-password-1").status_code == 401
    r = _login(app, "198.51.100.66", "wrong-password-1")
    assert r.status_code == 429                     # the attacker is throttled ...
    assert _login(app, "203.0.113.5", PASSWORD).status_code == 200   # ... the owner is not


def test_successful_logins_are_not_throttled_per_email(tmp_path):
    app = create_app(make_settings(tmp_path, auth_per_email_per_minute=2,
                                   auth_per_ip_per_minute=100))
    with client(app) as c:
        signup(c, email="vic@example.com", onboard=False)
        for _ in range(6):
            c.cookies.clear()
            assert c.post("/api/auth/login", json={"email": "vic@example.com",
                                                   "password": PASSWORD}).status_code == 200


def test_global_per_email_failure_ceiling(tmp_path):
    app = create_app(make_settings(tmp_path, auth_email_global_burst=5,
                                   auth_email_global_per_minute=1))
    with client(app) as c:
        signup(c, email="vic@example.com", onboard=False)
    for i in range(5):
        assert _login(app, f"198.51.100.{i}", "wrong-password-1").status_code == 401
    assert _login(app, "198.51.100.99", "wrong-password-1").status_code == 429
    # a distributed attack above the ceiling does block the owner, by design
    assert _login(app, "203.0.113.5", PASSWORD).status_code == 429


def test_rate_limiter_peek_does_not_consume():
    rl = RateLimiter(per_minute=1, capacity=1)
    assert rl.peek("k") and rl.peek("k") and rl.allow("k")
    assert not rl.peek("k") and not rl.allow("k")


# ─────────────────────────────────────────────────────────────
# 7. sign-up timing
# ─────────────────────────────────────────────────────────────

def test_signup_taken_email_costs_a_hash(monkeypatch, app, c):
    import ana.app.api as api
    signup(c, email="vic@example.com", onboard=False)
    c.cookies.clear()
    calls = []
    real = api.burn_hash
    monkeypatch.setattr(api, "burn_hash", lambda p: (calls.append(1), real(p)))
    r = c.post("/api/auth/signup", json={"email": "VIC@example.com", "password": PASSWORD,
                                         "age_confirmed": True})
    assert r.status_code == 409 and err_code(r) == "email_taken" and calls == [1]


def test_signup_timing_is_equalised(app, c):
    signup(c, email="vic@example.com", onboard=False)

    def t(email):
        ctx(app).limiter_auth_ip.reset()
        ctx(app).limiter_auth_email.reset()
        c.cookies.clear()
        t0 = time.perf_counter()
        c.post("/api/auth/signup", json={"email": email, "password": PASSWORD,
                                         "age_confirmed": True})
        return time.perf_counter() - t0

    taken = sorted(t("vic@example.com") for _ in range(5))[2]
    new = sorted(t(f"n{i}@example.com") for i in range(5))[2]
    assert taken > new * 0.5                        # was ~4 ms vs ~100 ms


# ─────────────────────────────────────────────────────────────
# A. turns are tied to the scene they answer
# ─────────────────────────────────────────────────────────────

def test_stale_tab_gets_scene_changed(app, c):
    signup(c)
    jid, p0 = new_journey(c)
    tick0 = p0["journey_tick"]
    r = act(c, jid, choice_index=0, expected_tick=tick0)          # tab A
    assert r.status_code == 200
    p1 = r.json()["pending"]
    used = _usage(c)
    r = act(c, jid, choice_index=1, expected_tick=tick0)          # stale tab B
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["code"] == "scene_changed" and body["error"]["message"]
    assert body["pending"] == p1
    assert _usage(c) == used
    with db_session(app) as db:
        assert len(db.execute(select(Cycle)).scalars().all()) == 1
    # B re-renders and answers the new scene
    assert act(c, jid, choice_index=1, expected_tick=p1["journey_tick"]).status_code == 200


def test_retried_turn_is_replayed_not_played_twice(app, c):
    signup(c)
    jid, p0 = new_journey(c)
    first = act(c, jid, text="I walk toward the light", expected_tick=p0["journey_tick"])
    assert first.status_code == 200
    used = _usage(c)
    again = act(c, jid, text="I walk toward the light", expected_tick=p0["journey_tick"])
    assert again.status_code == 200
    assert again.json()["replayed"] is True and again.headers["x-ana-replayed"] == "1"
    assert again.json()["record"] == first.json()["record"]
    assert again.json()["pending"] == first.json()["pending"]
    assert _usage(c) == used
    with db_session(app) as db:
        assert len(db.execute(select(Cycle)).scalars().all()) == 1
    # a DIFFERENT action for that old tick is not a replay
    r = act(c, jid, text="I turn back", expected_tick=p0["journey_tick"])
    assert r.status_code == 409 and err_code(r) == "scene_changed"
    # choice replay too
    p1 = first.json()["pending"]
    a1 = act(c, jid, choice_index=2, expected_tick=p1["journey_tick"]).json()
    a2 = act(c, jid, choice_index=2, expected_tick=p1["journey_tick"]).json()
    assert a2["replayed"] is True and a2["record"] == a1["record"]
    assert act(c, jid, choice_index=0, expected_tick=p1["journey_tick"]).status_code == 409


def test_replay_of_the_final_turn(tmp_path):
    app = create_app(make_settings(tmp_path, max_cycles=2))
    with client(app) as c:
        signup(c)
        jid, p = new_journey(c)
        p = act(c, jid, choice_index=0, expected_tick=p["journey_tick"]).json()["pending"]
        last = act(c, jid, choice_index=0, expected_tick=p["journey_tick"]).json()
        assert last["closed"] is True
        again = act(c, jid, choice_index=0, expected_tick=p["journey_tick"])
        assert again.status_code == 200 and again.json()["replayed"] is True
        assert again.json()["closed"] is True and "pending" not in again.json()


def test_missing_expected_tick_is_still_accepted(c):
    signup(c)
    jid, _ = new_journey(c)
    assert act(c, jid, choice_index=0).status_code == 200


def test_stale_crisis_message_still_pauses(c):
    signup(c)
    jid, p0 = new_journey(c)
    act(c, jid, choice_index=0, expected_tick=p0["journey_tick"])
    r = act(c, jid, text=CRISIS_TEXT, expected_tick=p0["journey_tick"])
    assert r.status_code == 200 and r.json()["safety"]["level"] == "crisis"
    assert r.json()["journey"]["status"] == "paused_safety"


# ─────────────────────────────────────────────────────────────
# B. validation
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pw", [" " * 10, " " * 30, "  abcdefg  ", "\t\n" * 6])
def test_whitespace_padded_short_passwords_are_refused(c, pw):
    r = c.post("/api/auth/signup", json={"email": "m@example.com", "password": pw,
                                         "age_confirmed": True})
    assert r.status_code == 422 and err_code(r) == "weak_password"
    assert "not counting spaces" in r.json()["error"]["message"]


@pytest.mark.parametrize("email", ["qa😀@example.com", "a​@example.com", "a@exa mple.com",
                                   "a@@example.com", "a@localhost", ".a@example.com",
                                   "a..b@example.com", "a@-x.com", "vic@example.com​",
                                   "a@😀.com"])
def test_bad_emails_are_refused(c, email):
    r = c.post("/api/auth/signup", json={"email": email, "password": PASSWORD,
                                         "age_confirmed": True})
    assert r.status_code == 422 and err_code(r) == "invalid_email"
    assert r.json()["error"]["message"] == "That doesn't look like an email address."


def test_idn_domain_is_normalised(app, c):
    r = c.post("/api/auth/signup", json={"email": "Ana@Exämple.com", "password": PASSWORD,
                                         "age_confirmed": True})
    assert r.status_code == 201 and r.json()["user"]["email"] == "ana@xn--exmple-cua.com"
    c.cookies.clear()
    assert c.post("/api/auth/login", json={"email": "ana@exämple.com",
                                           "password": PASSWORD}).status_code == 200


# ─────────────────────────────────────────────────────────────
# C. never-show words redacted at read time
# ─────────────────────────────────────────────────────────────

def test_new_never_show_word_is_redacted_from_titles_and_debriefs(tmp_path):
    from ana.subsystems.alignment import mentions_forbidden
    app = create_app(make_settings(tmp_path, max_cycles=2))
    with client(app) as c:
        signup(c)
        jid, _ = new_journey(c)
        act(c, jid, choice_index=0)
        act(c, jid, choice_index=0)
        title = c.get("/api/journeys").json()["journeys"][0]["title"]
        d = c.get(f"/api/journeys/{jid}/debrief").json()
        word = next(w for w in ("grief", "leadership", "courage", "fear")
                    if mentions_forbidden(title, [w]))
        assert c.patch("/api/identity", json={"forbidden_themes": ["shame", word]}).status_code == 200

        new_title = c.get("/api/journeys").json()["journeys"][0]["title"]
        assert new_title and not mentions_forbidden(new_title, [word], synonyms=True)
        j = c.get(f"/api/journeys/{jid}").json()["journey"]
        assert j["title"] == new_title
        d2 = c.get(f"/api/journeys/{jid}/debrief").json()
        assert mentions_forbidden(d["markdown"], [word])
        assert not mentions_forbidden(d2["markdown"], [word], synonyms=True)
        import json as _json
        assert not mentions_forbidden(_json.dumps(d2["debrief"]), [word], synonyms=True)
        assert d2["markdown"].startswith("# ")
        # removing the word restores the original wording
        c.patch("/api/identity", json={"forbidden_themes": ["shame"]})
        assert c.get("/api/journeys").json()["journeys"][0]["title"] == title


# ─────────────────────────────────────────────────────────────
# D. concern turns are played gently
# ─────────────────────────────────────────────────────────────

def test_concern_turn_is_played_gently(monkeypatch, c):
    import inspect as _inspect
    seen = []
    real = JourneyRuntime.act
    supports = "gentle" in _inspect.signature(real).parameters

    def act_(self, action, *, gentle=False):
        seen.append(gentle)
        return real(self, action, gentle=gentle) if supports else real(self, action)

    monkeypatch.setattr(JourneyRuntime, "act", act_)
    signup(c)
    jid, _ = new_journey(c)
    assert act(c, jid, text="I walk toward the light").status_code == 200
    r = act(c, jid, text=CONCERN_TEXT)
    assert r.status_code == 200 and r.json()["safety"]["level"] == "concern"
    assert seen == [False, True]
