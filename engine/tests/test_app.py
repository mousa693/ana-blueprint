"""The public web product backend (ana/app, docs/PRODUCT_SPEC.md §3).

Hermetic: every test gets its own temp dir for the SQLite DB and the shard
store, ANA_DEV-style settings, and a TestClient whose Host is 'testserver'.
"""
from __future__ import annotations

import json
import sqlite3
import warnings

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

warnings.filterwarnings("ignore")

from ana.app import shards as shard_store
from ana.app.config import ConfigError, Settings
from ana.app.main import create_app
from ana.app.models import Journey, SafetyEvent, Usage
from ana.contracts import UserAction
from ana.identity.memory import ShardMemory
from ana.runtime import IdentitySeed, JourneyRuntime

PASSWORD = "correct horse battery"
ANSWERS = {"name": "Mira", "carrying": ["grief", "leadership"], "first_move": "face_it",
           "become": "someone who can lead", "place": "a lighthouse on the north shore",
           "mood": 2, "never_show": ["shame"]}
CRISIS_TEXT = "I want to kill myself"
CONCERN_TEXT = "I feel hopeless about all of it"


# ─────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────

def make_settings(tmp_path, **kw) -> Settings:
    base = dict(database_url=f"sqlite:///{tmp_path}/ana.db", shard_db=str(tmp_path / "shards.db"),
                secret_key="test-secret-key-0123456789", dev=True, use_llm="off",
                allowed_hosts=("testserver", "localhost", "127.0.0.1"), max_cycles=5)
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def app(settings):
    return create_app(settings)


def client(app) -> TestClient:
    return TestClient(app, base_url="http://testserver")


@pytest.fixture
def c(app):
    with client(app) as cl:
        yield cl


def signup(cl, email="mira@example.com", password=PASSWORD, onboard=True, **extra):
    r = cl.post("/api/auth/signup", json={"email": email, "password": password,
                                          "age_confirmed": True, **extra})
    assert r.status_code == 201, r.text
    if onboard:
        r = cl.post("/api/onboarding", json={"answers": ANSWERS})
        assert r.status_code == 201, r.text
    return r.json()


def new_journey(cl) -> tuple[str, dict]:
    r = cl.post("/api/journeys", json={})
    assert r.status_code == 201, r.text
    return r.json()["journey"]["id"], r.json()["pending"]


def act(cl, jid, **body):
    return cl.post(f"/api/journeys/{jid}/act", json=body)


def ctx(app):
    return app.state.ctx


def db_session(app):
    return ctx(app).sessionmaker()


def identity_id_of(app, email):
    from ana.app.models import Identity, User
    with db_session(app) as db:
        u = db.execute(select(User).where(User.email == email)).scalars().one()
        return db.execute(select(Identity).where(Identity.user_id == u.id)).scalars().one().identity_id


def err_code(r):
    return r.json()["error"]["code"]


# ─────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────

def test_full_happy_path(app, c, settings):
    r = c.get("/api/onboarding")
    assert r.status_code == 200
    q = r.json()
    assert q["questions"] and q["disclaimer"]
    assert {"id", "prompt", "kind"} <= set(q["questions"][0])

    signup(c, onboard=False)
    me = c.get("/api/me").json()
    assert me["has_identity"] is False and me["llm_available"] is False
    assert me["usage"] == {"today": 0, "limit": settings.daily_cycles_per_user}

    r = c.post("/api/onboarding", json={"answers": ANSWERS})
    assert r.status_code == 201
    ident = r.json()["identity"]
    assert ident["name"] == "Mira" and "shame" in ident["forbidden_themes"]
    assert ident["goals"] and {"description", "progress", "realised"} <= set(ident["goals"][0])
    assert c.get("/api/me").json()["has_identity"] is True

    jid, pending = new_journey(c)
    assert pending["journey_tick"] == 0 and pending["scene"]["choices"]
    assert pending["chapter"].startswith("Chapter 1")
    assert c.get(f"/api/journeys/{jid}/pending").json()["pending"] == pending   # idempotent

    records = []
    for i in range(settings.max_cycles):
        r = act(c, jid, choice_index=0) if i % 2 == 0 else act(c, jid, text="I walk toward the light")
        assert r.status_code == 200, r.text
        body = r.json()
        rec = body["record"]
        records.append(rec)
        assert set(rec) == {"tick", "stage", "chapter", "scene", "action", "npc_events", "lesson",
                            "convergence", "success"}
        assert set(rec["scene"]) == {"title", "summary", "beats", "choices", "symbols",
                                     "is_vision", "source"}
        if body["closed"]:
            break
        assert body["pending"]["journey_tick"] == rec["tick"] + 1
    assert body["closed"] is True and body["debrief_ready"] is True and "pending" not in body
    assert records[1]["action"]["raw"] == "I walk toward the light"

    assert err_code(act(c, jid, choice_index=0)) == "journey_closed"
    assert c.get(f"/api/journeys/{jid}/pending").json() == {"closed": True}

    j = c.get(f"/api/journeys/{jid}").json()
    assert j["journey"]["status"] == "closed" and j["records"] == records and "pending" not in j
    assert j["journey"]["cycles"] == len(records)

    d = c.get(f"/api/journeys/{jid}/debrief").json()
    assert d["debrief"]["title"] and d["markdown"].startswith("# ")
    lst = c.get("/api/journeys").json()["journeys"]
    assert [x["id"] for x in lst] == [jid] and lst[0]["title"] == d["debrief"]["title"]
    assert c.get("/api/me").json()["usage"]["today"] == len(records)

    # never leak internals
    blob = json.dumps(j)
    for leak in ("alignment", "harmonizer", "identity_before", "snapshot", "nudge"):
        assert leak not in blob

    r = c.get("/api/me/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    ex = r.json()
    assert ex["user"]["email"] == "mira@example.com"
    assert ex["identity"]["seed"]["name"] == "Mira" and ex["identity"]["state"]["goals"]
    assert ex["journeys"][0]["records"] == records and ex["journeys"][0]["debrief"]["markdown"]
    assert ex["shards"] and all(set(s) == {"content", "tags"} for s in ex["shards"])
    assert "snapshot" not in r.text

    iid = identity_id_of(app, "mira@example.com")
    assert shard_store.count_shards(settings.shard_db, iid) > 0

    assert err_code(c.request("DELETE", "/api/me", json={"password": "wrong password!"})) == "invalid_password"
    r = c.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert r.status_code == 204
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/auth/login", json={"email": "mira@example.com",
                                           "password": PASSWORD}).status_code == 401
    assert shard_store.count_shards(settings.shard_db, iid) == 0
    with db_session(app) as db:
        assert db.execute(select(Journey)).first() is None
        assert db.execute(select(Usage)).first() is None


def test_end_early_and_debrief(c):
    signup(c)
    jid, _ = new_journey(c)
    assert err_code(c.get(f"/api/journeys/{jid}/debrief")) == "not_ready"
    assert act(c, jid, choice_index=1).status_code == 200
    r = c.post(f"/api/journeys/{jid}/end", json={})
    assert r.status_code == 200 and r.json()["debrief_ready"] is True
    assert r.json()["journey"]["status"] == "ended"
    assert c.post(f"/api/journeys/{jid}/end", json={}).status_code == 200   # idempotent
    assert c.get(f"/api/journeys/{jid}/debrief").json()["markdown"]
    assert err_code(act(c, jid, choice_index=0)) == "journey_closed"


def test_end_with_no_turns(c):
    signup(c)
    jid, _ = new_journey(c)
    r = c.post(f"/api/journeys/{jid}/end")
    assert r.status_code == 200
    assert c.get(f"/api/journeys/{jid}/debrief").status_code == 200


# ─────────────────────────────────────────────────────────────
# Turn rules, quotas, limits
# ─────────────────────────────────────────────────────────────

def test_turn_rules(app, c):
    signup(c)
    jid, pending = new_journey(c)
    assert err_code(act(c, jid)) == "empty_action"
    assert err_code(act(c, jid, text="   ")) == "empty_action"
    r = act(c, jid, text="x" * 401)
    assert r.status_code == 422 and err_code(r) == "action_too_long"
    assert act(c, jid, choice_index=len(pending["scene"]["choices"])).status_code == 422
    assert act(c, jid, choice_index=-1).status_code == 422
    assert act(c, jid, choice_index=True).status_code == 422
    assert act(c, jid, choice_index="0").status_code == 422
    # one request per journey at a time
    lk = ctx(app).cache.lock_for(jid)
    lk.acquire()
    try:
        r = act(c, jid, choice_index=0)
        assert r.status_code == 409 and err_code(r) == "busy"
    finally:
        lk.release()
    assert act(c, jid, text="x" * 400).status_code == 200
    # nothing was consumed by the rejected attempts
    assert c.get("/api/me").json()["usage"]["today"] == 1


def test_daily_quota(tmp_path):
    app = create_app(make_settings(tmp_path, daily_cycles_per_user=2))
    with client(app) as c:
        signup(c)
        jid, _ = new_journey(c)
        assert act(c, jid, choice_index=0).status_code == 200
        assert act(c, jid, choice_index=0).status_code == 200
        r = act(c, jid, choice_index=0)
        assert r.status_code == 429 and err_code(r) == "quota_exceeded"
        assert r.json()["error"]["message"]
        assert c.post(f"/api/journeys/{jid}/end").status_code == 200
        r = c.post("/api/journeys", json={})
        assert r.status_code == 429 and err_code(r) == "quota_exceeded"


def test_active_journey_cap_and_no_identity(tmp_path):
    app = create_app(make_settings(tmp_path, max_active_journeys=2))
    with client(app) as c:
        signup(c, onboard=False)
        assert err_code(c.post("/api/journeys", json={})) == "no_identity"
        assert c.get("/api/identity").status_code == 404
        c.post("/api/onboarding", json={"answers": ANSWERS})
        a, _ = new_journey(c)
        new_journey(c)
        r = c.post("/api/journeys", json={})
        assert r.status_code == 409 and err_code(r) == "too_many_active"
        c.post(f"/api/journeys/{a}/end")
        new_journey(c)


def test_act_rate_limit(tmp_path):
    app = create_app(make_settings(tmp_path, act_per_minute=3, max_cycles=30))
    with client(app) as c:
        signup(c)            # onboarding takes one token
        jid, _ = new_journey(c)
        assert act(c, jid, choice_index=0).status_code == 200
        assert act(c, jid, choice_index=0).status_code == 200
        r = act(c, jid, choice_index=0)
        assert r.status_code == 429 and err_code(r) == "rate_limited" and "retry-after" in r.headers


def test_body_size_and_json_only(c):
    signup(c)
    jid, _ = new_journey(c)
    big = json.dumps({"text": "a", "pad": "x" * 40_000})
    r = c.post(f"/api/journeys/{jid}/act", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413 and err_code(r) == "too_large"
    r = c.post(f"/api/journeys/{jid}/act", content="text=hi",
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    r = c.post(f"/api/journeys/{jid}/act", content="text=hi", headers={"content-type": "text/plain"})
    assert r.status_code == 415
    r = c.post(f"/api/journeys/{jid}/act", content="{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = c.post(f"/api/journeys/{jid}/act", content="[1,2]", headers={"content-type": "application/json"})
    assert r.status_code == 422


# ─────────────────────────────────────────────────────────────
# Isolation (IDOR)
# ─────────────────────────────────────────────────────────────

def test_other_users_journeys_are_404(app):
    with client(app) as a, client(app) as b:
        signup(a, "a@example.com")
        jid, _ = new_journey(a)
        act(a, jid, choice_index=0)
        signup(b, "b@example.com")
        for method, path, body in [
            ("GET", f"/api/journeys/{jid}", None),
            ("GET", f"/api/journeys/{jid}/pending", None),
            ("POST", f"/api/journeys/{jid}/act", {"choice_index": 0}),
            ("POST", f"/api/journeys/{jid}/resume", {"acknowledged": True}),
            ("POST", f"/api/journeys/{jid}/end", {}),
            ("GET", f"/api/journeys/{jid}/debrief", None),
        ]:
            r = b.request(method, path, json=body)
            assert r.status_code == 404, (method, path, r.status_code)
            assert err_code(r) == "not_found"
        assert b.get("/api/journeys").json() == {"journeys": []}
        ex = b.get("/api/me/export").json()
        assert ex["journeys"] == [] and jid not in json.dumps(ex)
        assert ex["user"]["email"] == "b@example.com"
        # A's journey is untouched by B's attempts
        aj = a.get(f"/api/journeys/{jid}").json()
        assert aj["journey"]["status"] == "active" and len(aj["records"]) == 1
        # unauthenticated
        with client(app) as anon:
            assert anon.get(f"/api/journeys/{jid}").status_code == 401
            assert anon.get("/api/me/export").status_code == 401


def test_delete_account_removes_only_own_data(app, settings):
    with client(app) as a, client(app) as b:
        signup(a, "a@example.com")
        signup(b, "b@example.com")
        ja, _ = new_journey(a)
        jb, _ = new_journey(b)
        for _ in range(3):
            act(a, ja, choice_index=0)
            act(b, jb, choice_index=0)
        a.post(f"/api/journeys/{ja}/end")      # flushes A's journey; B's stays cached
        ja2, _ = new_journey(a)                # A has a live, cached runtime too
        act(a, ja2, choice_index=0)
        assert ctx(app).cache.cached(ja2)
        ia, ib = identity_id_of(app, "a@example.com"), identity_id_of(app, "b@example.com")
        nb = shard_store.count_shards(settings.shard_db, ib)
        assert shard_store.count_shards(settings.shard_db, ia) > 0 and nb > 0

        assert a.request("DELETE", "/api/me", json={"password": PASSWORD}).status_code == 204
        assert not ctx(app).cache.cached(ja2)
        assert shard_store.count_shards(settings.shard_db, ia) == 0
        assert shard_store.count_shards(settings.shard_db, ib) == nb
        # A's identity id recalls nothing from the shard store
        mem = ShardMemory(db_path=settings.shard_db, scope=ia)
        try:
            assert mem.nodes() == []
            assert mem.recall("lighthouse grief lead", top_k=5) == []
        finally:
            mem.close()
        # closing every runtime (shutdown flush) does not bring them back
        ctx(app).cache.clear(flush=True)
        assert shard_store.count_shards(settings.shard_db, ia) == 0
        # B unaffected and still playing
        assert act(b, jb, choice_index=0).status_code == 200
        assert a.get("/api/me").status_code == 401
        with db_session(app) as db:
            assert {j.user_id for j in db.execute(select(Journey)).scalars()} == \
                {b.get("/api/me").json()["user"]["id"]}


# ─────────────────────────────────────────────────────────────
# Safety
# ─────────────────────────────────────────────────────────────

def test_crisis_pauses_and_resume(app, c):
    signup(c)
    jid, pending = new_journey(c)
    r = act(c, jid, text=CRISIS_TEXT)
    assert r.status_code == 200
    body = r.json()
    assert body["safety"]["level"] == "crisis" and body["safety"]["resources"]
    assert body["record"] is None and body["closed"] is False
    assert body["journey"]["status"] == "paused_safety"
    # nothing reached the engine or the quota
    assert c.get("/api/me").json()["usage"]["today"] == 0
    j = c.get(f"/api/journeys/{jid}").json()
    assert j["journey"]["status"] == "paused_safety" and j["records"] == []
    assert j["pending"] == pending
    # only level + time stored, never the text
    with db_session(app) as db:
        ev = db.execute(select(SafetyEvent)).scalars().all()
        assert [e.level for e in ev] == ["crisis"]
        assert set(SafetyEvent.__table__.columns.keys()) == {"id", "user_id", "journey_id",
                                                             "level", "created_at"}
    raw = (app.state.engine.url.database)
    dump = "\n".join(sqlite3.connect(raw).iterdump())
    assert "kill myself" not in dump
    # paused: acting is refused until an explicit, acknowledged resume
    r = act(c, jid, choice_index=0)
    assert r.status_code == 409 and err_code(r) == "journey_paused"
    assert c.post(f"/api/journeys/{jid}/resume", json={}).status_code == 422
    assert c.post(f"/api/journeys/{jid}/resume", json={"acknowledged": False}).status_code == 422
    assert c.post(f"/api/journeys/{jid}/resume", json={"acknowledged": "yes"}).status_code == 422
    r = c.post(f"/api/journeys/{jid}/resume", json={"acknowledged": True})
    assert r.status_code == 200
    assert r.json()["journey"]["status"] == "active" and r.json()["pending"] == pending
    assert act(c, jid, choice_index=0).status_code == 200


def test_concern_proceeds_with_gentle_message(c):
    signup(c)
    jid, _ = new_journey(c)
    r = act(c, jid, text=CONCERN_TEXT)
    assert r.status_code == 200
    body = r.json()
    assert body["record"] is not None and body["safety"]["level"] == "concern"
    assert body["safety"]["message"]
    r = act(c, jid, choice_index=0)
    assert "safety" not in r.json()


def test_onboarding_crisis_creates_no_identity(c):
    signup(c, onboard=False)
    r = c.post("/api/onboarding", json={"answers": {**ANSWERS, "become": CRISIS_TEXT}})
    assert r.status_code == 200
    assert r.json()["safety"]["level"] == "crisis" and "identity" not in r.json()
    assert c.get("/api/me").json()["has_identity"] is False
    assert c.post("/api/onboarding", json={"answers": "nope"}).status_code == 422


def test_identity_patch(c):
    signup(c)
    r = c.patch("/api/identity", json={"forbidden_themes": ["Spiders", "deep water", "spiders"],
                                       "name": "  Wren "})
    assert r.status_code == 200
    ident = r.json()["identity"]
    assert ident["forbidden_themes"] == ["spiders", "deep water"] and ident["name"] == "Wren"
    assert c.patch("/api/identity", json={"forbidden_themes": ["x"] * 51}).status_code == 422
    assert c.patch("/api/identity", json={"forbidden_themes": ["x" * 61]}).status_code == 422
    # new journeys use the updated never-show list
    jid, _ = new_journey(c)
    assert c.get(f"/api/journeys/{jid}").status_code == 200


# ─────────────────────────────────────────────────────────────
# Persistence across restarts; the self across journeys
# ─────────────────────────────────────────────────────────────

def _seed_from_db(app, jid):
    from ana.app.models import Identity
    with db_session(app) as db:
        j = db.get(Journey, jid)
        ident = db.execute(select(Identity).where(Identity.user_id == j.user_id)).scalars().one()
        return IdentitySeed.model_validate_json(ident.seed_json), ident.identity_id, j.seed_value


def _sc(scene: dict) -> dict:
    """A scene without its random id (ids are uuid-based, not seeded)."""
    return {k: v for k, v in scene.items() if k != "id"}


def test_restart_persistence_matches_uninterrupted_runtime(tmp_path):
    settings = make_settings(tmp_path, max_cycles=12)
    choices = [0, 1, 0, 2, 1]
    app1 = create_app(settings)
    with client(app1) as c1:
        signup(c1)
        jid, _ = new_journey(c1)
        for ch in choices[:3]:
            assert act(c1, jid, choice_index=ch).status_code == 200
        before = c1.get(f"/api/journeys/{jid}/pending").json()["pending"]
        seed, iid, seed_value = _seed_from_db(app1, jid)
        cookies = dict(c1.cookies)
    assert len(ctx(app1).cache) == 0           # lifespan shutdown dropped the cache
    del app1

    # the reference: one uninterrupted runtime with the same seed and actions
    ref = JourneyRuntime.start(seed, identity_id=iid, shard_db=str(tmp_path / "ref_shards.db"),
                               use_llm=False, seed_value=seed_value, max_cycles=12)
    try:
        for ch in choices[:3]:
            ref.pending()
            ref.act(UserAction(raw="", choice_index=ch))
        ref_pending = ref.pending().model_dump(mode="json")

        app2 = create_app(settings)
        with client(app2) as c2:
            c2.cookies.update(cookies)          # sessions are server-side and survive too
            after = c2.get(f"/api/journeys/{jid}/pending").json()["pending"]
            assert after == before
            assert _sc(after["scene"]) == _sc(ref_pending["scene"])
            assert after["journey_tick"] == ref_pending["journey_tick"] == 3
            assert after["chapter"] == ref_pending["chapter"]
            for ch in choices[3:]:
                r = act(c2, jid, choice_index=ch)
                assert r.status_code == 200
                rec = ref.act(UserAction(raw="", choice_index=ch))
                assert r.json()["record"]["scene"]["title"] == rec.scene.title
                assert r.json()["record"]["lesson"] == rec.outcome.lesson
                assert r.json()["record"]["convergence"] == round(rec.convergence, 4)
                assert _sc(r.json()["pending"]["scene"]) == \
                    _sc(ref.pending().model_dump(mode="json")["scene"])
            assert len(c2.get(f"/api/journeys/{jid}").json()["records"]) == len(choices)
    finally:
        ref.close()


def test_identity_state_carries_into_next_journey(app, c, settings):
    signup(c)
    j1, _ = new_journey(c)
    for _ in range(settings.max_cycles):
        r = act(c, j1, choice_index=0)
        if r.json()["closed"]:
            break
    ident = c.get("/api/identity").json()["identity"]
    progressed = {g["description"]: g["progress"] for g in ident["goals"]}
    assert any(p > 0 for p in progressed.values())
    assert ident["insights"]

    j2, _ = new_journey(c)
    rt = ctx(app).cache.get(j2, None)
    goals = {g.description: g.progress for g in rt.session.identity.goals}
    for desc, p in progressed.items():
        assert goals[desc] == pytest.approx(p, abs=1e-3)
    # the shards from journey 1 are recalled by journey 2's self
    assert len(rt.session.identity.memory.nodes()) >= \
        shard_store.count_shards(settings.shard_db, identity_id_of(app, "mira@example.com"))


# ─────────────────────────────────────────────────────────────
# Claude path (mocked transport) and the global budget
# ─────────────────────────────────────────────────────────────

def _make_gateway():
    try:
        from tests.test_llm import make_gateway
    except ImportError:      # pragma: no cover - when tests/ itself is on sys.path
        from test_llm import make_gateway
    return make_gateway


def test_llm_journey_and_budget_exhaustion(tmp_path):
    make_gateway = _make_gateway()

    gw, rec = make_gateway()
    # budget: onboarding (1) + first scene (1) + one turn (1)
    settings = make_settings(tmp_path, use_llm="auto", daily_llm_cycles_total=3, max_cycles=8)
    app = create_app(settings, gateway=gw)
    with client(app) as c:
        signup(c, onboard=False)
        assert c.get("/api/me").json()["llm_available"] is True
        assert c.get("/healthz").json()["llm"] is True
        assert c.post("/api/onboarding", json={"answers": ANSWERS}).status_code == 201
        jid, pending = new_journey(c)
        assert pending["scene"]["source"] == "llm" and pending["scene"]["title"] == "The Lantern Room"
        r = c.get(f"/api/journeys/{jid}").json()
        assert r["journey"]["use_llm"] is True

        r = act(c, jid, choice_index=0).json()             # within budget
        assert r["record"]["scene"]["source"] == "llm"
        assert r["pending"]["scene"]["source"] == "llm"
        calls = len(rec.requests)

        r = act(c, jid, choice_index=0).json()             # budget spent -> deterministic
        assert r["pending"]["scene"]["source"] == "deterministic"
        assert len(rec.requests) == calls                  # no model call this turn
        r = act(c, jid, choice_index=0).json()
        assert r["record"]["scene"]["source"] == "deterministic"
        assert len(rec.requests) == calls
        with db_session(app) as db:
            from ana.app.models import GlobalUsage
            assert db.execute(select(GlobalUsage)).scalars().one().llm_cycles == 3


def test_llm_off_ignores_gateway(tmp_path):
    make_gateway = _make_gateway()

    gw, rec = make_gateway()
    app = create_app(make_settings(tmp_path, use_llm="off"), gateway=gw)
    with client(app) as c:
        signup(c)
        jid, pending = new_journey(c)
        assert pending["scene"]["source"] == "deterministic" and rec.requests == []


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

def test_signup_validation(tmp_path):
    app = create_app(make_settings(tmp_path, invite_codes=("moon",)))
    with client(app) as c:
        def su(**kw):
            body = {"email": "x@example.com", "password": PASSWORD, "age_confirmed": True,
                    "invite_code": "moon"}
            body.update(kw)
            return c.post("/api/auth/signup", json=body)
        assert err_code(su(email="nope")) == "invalid_email"
        assert err_code(su(email="a@b.co" + "m" * 300)) == "invalid_email"
        assert err_code(su(password="short")) == "weak_password"
        assert err_code(su(password="p" * 300)) == "password_too_long"
        assert err_code(su(age_confirmed=False)) == "age_required"
        assert err_code(su(age_confirmed="true")) == "age_required"
        assert err_code(su(invite_code=None)) == "invite_required"
        assert err_code(su(invite_code="sun")) == "invalid_invite"
        assert c.get("/api/onboarding").json()["invite_required"] is True
        assert su(email="  X@Example.com ").status_code == 201
        assert su().status_code == 409
    app = create_app(make_settings(tmp_path / "closed", allow_signups=False))
    with client(app) as c:
        r = c.post("/api/auth/signup", json={"email": "x@example.com", "password": PASSWORD,
                                             "age_confirmed": True})
        assert r.status_code == 403 and err_code(r) == "signups_closed"


def test_login_logout_and_sessions(app, c):
    signup(c, onboard=False)
    cookie = c.cookies.get("ana_session")
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/me").status_code == 401
    c.cookies.set("ana_session", cookie)              # revoked server-side
    assert c.get("/api/me").status_code == 401
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": "MIRA@example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["user"]["email"] == "mira@example.com"
    assert c.get("/api/me").status_code == 200
    c.cookies.set("ana_session", c.cookies.get("ana_session")[:-2] + "xx")   # tampered
    assert c.get("/api/me").status_code == 401
    r = c.post("/api/auth/login", json={"email": "mira@example.com", "password": "wrong password"})
    assert r.status_code == 401 and err_code(r) == "invalid_credentials"
    r = c.post("/api/auth/login", json={"email": "ghost@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_password_is_argon2id(app, c):
    from ana.app.models import User
    signup(c, onboard=False)
    with db_session(app) as db:
        h = db.execute(select(User)).scalars().one().password_hash
    assert h.startswith("$argon2id$") and PASSWORD not in h


def test_login_rate_limited_per_email_and_ip(tmp_path):
    app = create_app(make_settings(tmp_path, auth_per_email_per_minute=3, auth_per_ip_per_minute=5))
    with client(app) as c:
        for _ in range(3):
            assert c.post("/api/auth/login", json={"email": "v@example.com",
                                                   "password": "whatever123"}).status_code == 401
        r = c.post("/api/auth/login", json={"email": "v@example.com", "password": "whatever123"})
        assert r.status_code == 429 and err_code(r) == "rate_limited"
        # a different email still has its own bucket, until the IP bucket runs dry
        assert c.post("/api/auth/login", json={"email": "w@example.com",
                                               "password": "whatever123"}).status_code == 401
        r = c.post("/api/auth/login", json={"email": "z@example.com", "password": "whatever123"})
        assert r.status_code == 429


def test_cookie_flags(tmp_path):
    app = create_app(make_settings(tmp_path))
    with client(app) as c:
        r = c.post("/api/auth/signup", json={"email": "a@example.com", "password": PASSWORD,
                                             "age_confirmed": True})
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc and "secure" not in sc
    app = create_app(make_settings(tmp_path / "prod", dev=False))
    with TestClient(app, base_url="https://testserver") as c:
        r = c.post("/api/auth/signup", json={"email": "a@example.com", "password": PASSWORD,
                                             "age_confirmed": True})
        sc = r.headers["set-cookie"].lower()
        assert "httponly" in sc and "samesite=lax" in sc and "secure" in sc
        assert "max-age=2592000" in sc


# ─────────────────────────────────────────────────────────────
# Guards, headers, misc
# ─────────────────────────────────────────────────────────────

def test_host_and_origin_guards(app, c):
    r = c.get("/api/onboarding", headers={"host": "evil.example"})
    assert r.status_code == 400 and err_code(r) == "bad_host"
    body = {"email": "a@example.com", "password": PASSWORD, "age_confirmed": True}
    r = c.post("/api/auth/signup", json=body, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and err_code(r) == "bad_origin"
    assert c.post("/api/auth/signup", json=body, headers={"origin": "null"}).status_code == 403
    assert c.post("/api/auth/signup", json=body,
                  headers={"referer": "https://evil.example/x"}).status_code == 403
    assert c.post("/api/auth/signup", json=body,
                  headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert c.post("/api/auth/signup", json=body,
                  headers={"origin": "http://testserver.evil.example"}).status_code == 403
    r = c.post("/api/auth/signup", json=body, headers={"origin": "http://testserver",
                                                       "sec-fetch-site": "same-origin"})
    assert r.status_code == 201
    # a logged-in cross-site POST is refused before it reaches the handler
    c.post("/api/onboarding", json={"answers": ANSWERS})
    r = c.post("/api/journeys", json={}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert c.get("/api/journeys").json() == {"journeys": []}
    # GETs from anywhere are fine (they change nothing)
    assert c.get("/api/me", headers={"origin": "https://evil.example"}).status_code == 200


def test_public_origin_is_accepted(tmp_path):
    app = create_app(make_settings(tmp_path, allowed_hosts=("localhost",),
                                   public_origin="https://ana.example.com"))
    with TestClient(app, base_url="https://ana.example.com") as c:
        r = c.post("/api/auth/signup", json={"email": "a@example.com", "password": PASSWORD,
                                             "age_confirmed": True},
                   headers={"origin": "https://ana.example.com"})
        assert r.status_code == 201
        assert "strict-transport-security" in r.headers
        assert c.get("/api/onboarding", headers={"host": "testserver"}).status_code == 400


def test_security_headers(c):
    for path in ("/", "/api/onboarding", "/healthz", "/static/app.css", "/api/me"):
        r = c.get(path)
        h = r.headers
        assert "default-src 'self'" in h["content-security-policy"], path
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert "script-src 'self'" in h["content-security-policy"]
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert h["referrer-policy"] == "same-origin"
    assert c.get("/api/onboarding").headers["cache-control"] == "no-store"


def test_static_and_spa_fallback(c):
    r = c.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert c.get("/home").text == r.text
    assert c.get("/missing.js").status_code == 404
    assert c.get("/static/app.js").status_code == 200
    r = c.get("/api/nope")
    assert r.status_code == 404 and err_code(r) == "not_found"
    r = c.put("/api/journeys")
    assert r.status_code in (404, 405) and "error" in r.json()


def test_healthz(c):
    assert c.get("/healthz").json() == {"ok": True, "db": True, "llm": False}


def test_config_from_env():
    with pytest.raises(ConfigError):
        Settings.from_env({})
    with pytest.raises(ConfigError):
        Settings.from_env({"ANA_SECRET_KEY": "x" * 32, "ANA_USE_LLM": "maybe"})
    s = Settings.from_env({"ANA_DEV": "1"})
    assert s.secret_key and s.dev and not s.secure_cookies
    s = Settings.from_env({"ANA_SECRET_KEY": "s3cret-value-0123456789",
                           "ANA_DATABASE_URL": "postgres://u:pw@db/ana",
                           "ANA_INVITE_CODES": "a, b", "ANA_ALLOWED_HOSTS": "Example.com",
                           "ANA_PUBLIC_ORIGIN": "https://ana.example.com/"})
    assert s.database_url == "postgresql+psycopg://u:pw@db/ana"
    assert s.invite_codes == ("a", "b") and s.secure_cookies
    assert s.host_allowlist == {"example.com", "ana.example.com"}
    described = json.dumps(s.describe(), default=str)
    assert "s3cret" not in described and ":pw@" not in described and '"a"' not in described


def test_lazy_module_app(monkeypatch, tmp_path):
    import importlib

    import ana.app.main as main
    monkeypatch.setenv("ANA_DEV", "1")
    monkeypatch.delenv("ANA_SECRET_KEY", raising=False)
    monkeypatch.setenv("ANA_DATABASE_URL", f"sqlite:///{tmp_path}/m.db")
    monkeypatch.setenv("ANA_SHARD_DB", str(tmp_path / "m_shards.db"))
    monkeypatch.setenv("ANA_USE_LLM", "off")
    main = importlib.reload(main)
    try:
        assert main.app.state.ctx.settings.dev is True
    finally:
        main.__dict__.pop("app", None)
    monkeypatch.setenv("ANA_DEV", "0")
    with pytest.raises(ConfigError):
        main.create_app()


# ─────────────────────────────────────────────────────────────
# Safety second opinion: the model can catch what keywords miss
# ─────────────────────────────────────────────────────────────

class _SafetyOnlyGateway:
    """A model that only answers the safety screen (everything else → None,
    so the story stays deterministic). It flags one specific phrase."""

    available = True

    def __init__(self, flag_phrase: str):
        self.flag_phrase = flag_phrase
        self.safety_calls = 0

    def structured(self, system, user, schema, **kw):
        props = schema.get("properties", {})
        if "level" in props and "reasons" in props:
            self.safety_calls += 1
            if self.flag_phrase in user:
                return {"level": "crisis", "reasons": ["passive_death_wish"]}
            return {"level": "none", "reasons": []}
        return None

    def complete(self, *a, **k):
        return None


def test_model_second_opinion_pauses_what_keywords_miss(tmp_path):
    phrase = "i keep thinking about the long sleep"   # euphemism the keyword screen misses
    from ana.safety import screen_text
    assert screen_text(phrase).level != "crisis"          # precondition: keywords alone miss it
    gw = _SafetyOnlyGateway(phrase)
    app = create_app(make_settings(tmp_path, use_llm="auto"), gateway=gw)
    with client(app) as c:
        signup(c)
        jid, _ = new_journey(c)
        r = act(c, jid, text="I walk toward the lighthouse.")
        assert r.status_code == 200 and not r.json().get("safety")
        r = act(c, jid, text=phrase).json()
        assert r["safety"]["level"] == "crisis" and r["record"] is None
        assert r["journey"]["status"] == "paused_safety"
    assert gw.safety_calls >= 2          # onboarding answers + both actions were screened


def test_healthz_accepts_any_host_but_api_does_not(tmp_path):
    app = create_app(make_settings(tmp_path))
    with TestClient(app, base_url="http://10.0.0.7:8000") as c:
        assert c.get("/healthz").status_code == 200          # platform probe by IP
        assert c.get("/api/me").status_code == 400            # everything else still guarded
        assert c.post("/healthz", json={}).status_code in (400, 405)
