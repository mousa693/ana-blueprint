"""The turn-based journey runtime (ana/runtime.py, docs/PRODUCT_SPEC.md §1).

  * step() == begin_cycle() + driver.act + complete_cycle(), byte-for-byte
    against golden journeys recorded from the pre-split engine;
  * pending()/act() == step() with a driver returning the same actions;
  * snapshot() -> restore() -> continue == continuing uninterrupted (also in a
    fresh process with a different PYTHONHASHSEED);
  * turn rules (idempotent pending, act without pending / after close raises);
  * forbidden themes never reach a pending scene, insights or the debrief;
  * the self persists across journeys per identity_id, and never leaks;
  * snapshots stay small and carry no gateway / API-key material;
  * the model path works through pending/act, and restore re-attaches a new
    gateway.
"""
from __future__ import annotations

import json
import os
import pickle
import re
import subprocess
import sys
import textwrap
import warnings
import zlib
from pathlib import Path

import pytest

from ana.contracts import UserAction
from ana.engine.drivers import AutonomousDriver, ScriptedDriver
from ana.runtime import (
    SNAPSHOT_MAGIC,
    SNAPSHOT_VERSION,
    IdentitySeed,
    JourneyClosedError,
    JourneyRuntime,
    NoPendingSceneError,
    PendingScene,
    SeedGoal,
    SeedMemory,
    SnapshotVersionError,
)

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden"
sys.path.insert(0, str(GOLDEN))
import make_golden  # noqa: E402

norm = make_golden.norm_record


def lighthouse_seed(**kw) -> IdentitySeed:
    base = dict(
        name="the wanderer",
        traits={"leadership_confidence": 0.25, "honesty": 0.8, "courage": 0.4},
        beliefs=["I am not the kind of person others follow."],
        goals=[SeedGoal(description="find my purpose", horizon="life", is_future_memory=True),
               SeedGoal(description="become someone who can lead", horizon="mid",
                        is_future_memory=True)],
        memories=[SeedMemory(summary="A childhood by a lighthouse on a grey coast.",
                             themes=["purpose", "courage"], symbols=["lighthouse"])],
        active_themes=["leadership", "purpose", "courage"],
        valence=-0.1,
    )
    base.update(kw)
    return IdentitySeed(**base)


def choose(scene, tick: int) -> UserAction:
    """A deterministic 'person': picks choices in rotation, writes now and then."""
    if tick % 4 == 3 or not scene.choices:
        return UserAction(raw="I stay with it and speak honestly about what I see.")
    return UserAction(raw="", choice_index=tick % len(scene.choices))


def live(rt: JourneyRuntime, cycles: int) -> list[str]:
    out = []
    for _ in range(cycles):
        if rt.closed:
            break
        p = rt.pending()
        out.append(norm(rt.act(choose(p.scene, p.journey_tick))))
    return out


def start(tmp_path, name="db", seed_value=42, identity_id="person-1", **kw) -> JourneyRuntime:
    kw.setdefault("use_llm", False)
    kw.setdefault("max_cycles", 30)
    seed = kw.pop("seed", None) or lighthouse_seed()
    return JourneyRuntime.start(seed, identity_id=identity_id,
                                shard_db=str(tmp_path / name / "shards.db"),
                                seed_value=seed_value, **kw)


# ── 0. the split loop is the old loop ─────────────────────────────────────

@pytest.mark.parametrize("name,seed,kind,forbid", make_golden.CASES,
                         ids=[c[0] for c in make_golden.CASES])
def test_step_matches_pre_split_golden(name, seed, kind, forbid):
    want = (GOLDEN / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    assert make_golden.journey_lines(seed, kind, forbid) == want


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_explicit_halves_match_golden(seed):
    """begin_cycle + driver.act + complete_cycle by hand == the golden step()."""
    want = (GOLDEN / f"scripted_s{seed}.jsonl").read_text(encoding="utf-8").splitlines()
    sess = make_golden.build(seed, "scripted", [])
    eng = sess.engine
    driver, eng.driver = eng.driver, None  # step() must not be what runs here
    got = []
    try:
        for _ in range(30):
            p = eng.begin_cycle()
            assert eng.begin_cycle() is p
            got.append(norm(eng.complete_cycle(driver.act(p.scene, p.view))))
            if eng.closed:
                break
        got.append(json.dumps({"closed": eng.closed, "chapter": eng.arc.current_chapter()},
                              sort_keys=True))
    finally:
        sess.close()
    assert got == want


def test_complete_without_begin_raises():
    sess = make_golden.build(1, "auto", [])
    try:
        with pytest.raises(RuntimeError, match="no pending cycle"):
            sess.engine.complete_cycle(UserAction(raw="x"))
    finally:
        sess.close()


# ── (a) pending/act == step ────────────────────────────────────────────────

@pytest.mark.parametrize("seed_value", [1, 2, 3])
def test_pending_act_equals_step(tmp_path, seed_value):
    a = start(tmp_path, "a", seed_value)
    b = start(tmp_path, "b", seed_value)
    try:
        # Reference: the engine's own step() with an autonomous driver.
        a.engine.driver = AutonomousDriver(seed=seed_value)
        ref = []
        for _ in range(20):
            if a.closed:
                break
            ref.append(norm(a.engine.step()))
        # Web path: the same policy, but the "person" acts through pending/act.
        person = AutonomousDriver(seed=seed_value)
        got = []
        for _ in range(20):
            if b.closed:
                break
            p = b.pending()
            got.append(norm(b.act(person.act(p.scene, b.engine.pending.view))))
        assert len(ref) >= 10
        assert got == ref
    finally:
        a.close()
        b.close()


# ── (b) snapshot -> restore -> continue == uninterrupted ───────────────────

@pytest.mark.parametrize("at", [1, 5, 12])
@pytest.mark.parametrize("with_pending", [False, True])
def test_snapshot_restore_continues_identically(tmp_path, at, with_pending):
    total = 20
    ref_rt = start(tmp_path, "ref", 7)
    ref = live(ref_rt, total)
    assert len(ref) > at + 2
    ref_next = ref_rt.pending() if not ref_rt.closed else None
    ref_rt.close()

    rt = start(tmp_path, "run", 7)
    got = live(rt, at)
    if with_pending:
        rt.pending()
    blob = rt.snapshot()
    rt.close()
    rt = JourneyRuntime.restore(blob, shard_db=str(tmp_path / "run" / "shards.db"))
    try:
        if with_pending:
            assert rt.has_pending()
        got += live(rt, total - at)
        assert got == ref
        nxt = rt.pending() if not rt.closed else None
        assert (nxt is None) == (ref_next is None)
        if nxt is not None:
            assert norm(nxt) == norm(ref_next)
        assert rt.closed == ref_rt.closed
    finally:
        rt.close()


def test_restore_twice_does_not_duplicate_shards(tmp_path):
    db = str(tmp_path / "x" / "shards.db")
    rt = start(tmp_path, "x", 3)
    live(rt, 4)
    n_nodes = len(rt.session.identity.memory.nodes())
    blob = rt.snapshot()
    rt.close()
    for _ in range(2):
        rt = JourneyRuntime.restore(blob, shard_db=db)
        assert rt.session.identity.memory.persistent
        assert len(rt.session.identity.memory.nodes()) == n_nodes
        rt.close()
    import sqlite3
    ids = [r[0] for r in sqlite3.connect(db).execute("select id from nodes")]
    assert len(ids) == len(set(ids))


# ── (c) restore in a fresh process with another hash seed ──────────────────

_CHILD = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[1] + "/tests")
    sys.path.insert(0, sys.argv[1] + "/tests/golden")
    from test_runtime import live
    from make_golden import norm_record
    from ana.runtime import JourneyRuntime
    blob = open(sys.argv[2], "rb").read()
    rt = JourneyRuntime.restore(blob, shard_db=sys.argv[3])
    lines = live(rt, int(sys.argv[4]))
    nxt = norm_record(rt.pending()) if not rt.closed else None
    rt.close()
    print(json.dumps({"lines": lines, "next": nxt}))
""")


def test_restore_in_fresh_process_other_hash_seed(tmp_path):
    rt = start(tmp_path, "p", 11)
    live(rt, 6)
    rt.pending()
    blob = rt.snapshot()
    rest = live(rt, 10)
    assert len(rest) >= 5
    nxt = norm(rt.pending()) if not rt.closed else None
    rt.close()

    path = tmp_path / "snap.bin"
    path.write_bytes(blob)
    env = dict(os.environ, PYTHONHASHSEED="987")
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, str(REPO), str(path),
         str(tmp_path / "child" / "shards.db"), "10"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["lines"] == rest
    assert data["next"] == nxt


# ── (d) turn rules ─────────────────────────────────────────────────────────

def test_turn_rules(tmp_path):
    rt = start(tmp_path, "d", 5, max_cycles=3)
    try:
        with pytest.raises(NoPendingSceneError):
            rt.act(UserAction(raw="I wait."))
        p1 = rt.pending()
        assert isinstance(p1, PendingScene) and p1.journey_tick == 0
        assert norm(rt.pending()) == norm(p1)          # idempotent
        assert rt.engine.begin_cycle() is rt.engine.begin_cycle()
        assert p1.chapter.startswith("Chapter 1:") and p1.location
        with pytest.raises(ValueError):
            rt.act(UserAction(raw="", choice_index=len(p1.scene.choices)))
        with pytest.raises(ValueError):
            rt.act(UserAction(raw="   "))
        rec = rt.act(UserAction(raw="", choice_index=0))
        assert rec.outcome.user_action.raw == p1.scene.choices[0]
        with pytest.raises(NoPendingSceneError):
            rt.act(UserAction(raw="again"))
        live(rt, 5)
        assert rt.closed and len(rt.history) == 3       # max_cycles
        with pytest.raises(JourneyClosedError):
            rt.pending()
        with pytest.raises(JourneyClosedError):
            rt.act(UserAction(raw="more"))
        assert rt.debrief().title
    finally:
        rt.close()
        rt.close()  # idempotent


def test_end_closes_early_and_survives_restore(tmp_path):
    rt = start(tmp_path, "e", 5)
    live(rt, 2)
    rt.pending()
    rt.end()
    assert rt.closed and rt.ended and not rt.has_pending()
    with pytest.raises(JourneyClosedError):
        rt.act(UserAction(raw="x"))
    blob = rt.snapshot()
    rt.close()
    rt2 = JourneyRuntime.restore(blob, shard_db=None)
    assert rt2.closed and len(rt2.history) == 2
    assert rt2.debrief().title
    rt2.close()


def test_unknown_snapshot_version_is_a_clear_error(tmp_path):
    rt = start(tmp_path, "v", 1)
    live(rt, 1)
    blob = rt.snapshot()
    rt.close()
    assert blob.startswith(SNAPSHOT_MAGIC)
    bumped = SNAPSHOT_MAGIC + (SNAPSHOT_VERSION + 1).to_bytes(2, "big") + blob[len(SNAPSHOT_MAGIC) + 2:]
    with pytest.raises(SnapshotVersionError, match="unsupported journey snapshot version"):
        JourneyRuntime.restore(bumped, shard_db=None)
    with pytest.raises(SnapshotVersionError, match="not an ANA journey snapshot"):
        JourneyRuntime.restore(b"garbage", shard_db=None)
    with pytest.raises(SnapshotVersionError, match="corrupt"):
        JourneyRuntime.restore(blob[:40], shard_db=None)


# ── (e) forbidden themes never reach the person ────────────────────────────

def _leaks(text: str, word: str) -> list[str]:
    pat = re.compile(rf"(?<![a-z0-9]){re.escape(word)}", re.I)
    return pat.findall(text.replace("_", " "))


@pytest.mark.parametrize("forbidden", ["leadership", "courage", "purpose"])
def test_forbidden_never_in_pending_scene(tmp_path, forbidden):
    rt = start(tmp_path, "f", 42, seed=lighthouse_seed(forbidden_themes=[forbidden]))
    try:
        n = 0
        while not rt.closed and n < 30:
            p = rt.pending()
            dump = json.dumps(p.model_dump(mode="json"))
            assert _leaks(dump, forbidden) == [], (p.journey_tick, dump[:400])
            rt.act(choose(p.scene, p.journey_tick))
            n += 1
        assert n >= 8
        for s in rt.insights(limit=0):
            assert _leaks(s, forbidden) == [], s
        md = rt.debrief().to_markdown()
        assert _leaks(md, forbidden) == [], [l for l in md.splitlines() if _leaks(l, forbidden)]
    finally:
        rt.close()


# ── (f) the self persists across journeys, per identity ────────────────────

def _lessons(rt: JourneyRuntime) -> set[str]:
    return {str(n.content) for n in rt.session.identity.memory.nodes()
            if "kind:lesson" in n.tags}


def test_identity_persists_across_journeys_and_never_leaks(tmp_path):
    db = str(tmp_path / "shared" / "shards.db")
    seed = lighthouse_seed()
    j1 = JourneyRuntime.start(seed, identity_id="alice", shard_db=db, use_llm=False,
                              seed_value=3, max_cycles=30)
    live(j1, 30)
    learned = {r.outcome.lesson for r in j1.history if r.outcome.lesson}
    fulfilled = j1.session.identity.fulfilled_visions()
    state = j1.identity_state()
    json.dumps(state)  # JSON-safe for the backend
    progress = {g["description"]: g["progress"] for g in state["goals"]}
    assert learned and max(progress.values()) > 0
    j1.close()

    # Somebody else on the same store sees none of it.
    bob = JourneyRuntime.start(lighthouse_seed(name="bob"), identity_id="bob", shard_db=db,
                               use_llm=False, seed_value=3)
    assert not (_lessons(bob) & learned)
    p = bob.pending()
    assert not (set(bob.engine.pending.ident.recent_shard_summaries) & learned)
    bob_blob = zlib.decompress(bob.snapshot()[len(SNAPSHOT_MAGIC) + 2:])
    for lesson in learned:
        assert lesson.encode() not in bob_blob
    bob.close()

    # Alice's second journey remembers her lessons and keeps her progress.
    j2 = JourneyRuntime.start(seed, identity_id="alice", shard_db=db, use_llm=False,
                              seed_value=4, identity_state=state)
    try:
        assert learned <= _lessons(j2)
        ident = j2.session.identity
        assert {g.description: g.progress for g in ident.goals} == progress
        assert ident.fulfilled_visions() == fulfilled
        assert all(f in _lessons(j2) for f in fulfilled)
        snap = ident.snapshot()
        assert set(snap.recent_shard_summaries) & learned
        assert snap.insights  # the journal came along
        assert ident.tick == 0 and all(e.tick < 0 for e in ident.journal.entries)
        # Seeded shards were not duplicated by re-seeding.
        beliefs = [n for n in ident.memory.nodes() if str(n.content).startswith("Belief:")]
        assert len(beliefs) == 1
        p = j2.pending()
        assert p.journey_tick == 0
        j2.act(choose(p.scene, 0))
        # Progress carries on from where it was, it doesn't restart.
        assert all(g.progress >= progress[g.description] - 0.05 for g in ident.goals)
    finally:
        j2.close()

    # Without identity_state the shards still persist (the state is optional).
    j3 = JourneyRuntime.start(seed, identity_id="alice", shard_db=db, use_llm=False)
    assert learned <= _lessons(j3)
    assert all(g.progress == 0 for g in j3.session.identity.goals)
    j3.close()


def make_gateway(api_key: str = "test-key"):
    """A real anthropic SDK client over an httpx MockTransport (see test_llm)."""
    import anthropic
    import httpx2 as httpx
    from test_llm import Recorder
    from ana.llm.gateway import ModelGateway

    rec = Recorder()
    client = anthropic.Anthropic(
        api_key=api_key, base_url="https://api.test",
        http_client=httpx.Client(transport=httpx.MockTransport(rec)), max_retries=0)
    return ModelGateway(client=client), rec


# ── (g) snapshot size and no secrets ───────────────────────────────────────

def test_snapshot_size_and_no_key_material(tmp_path, monkeypatch):
    fake = "sk-ant-api03-FAKE-KEY-0123456789abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", fake)
    gw, _ = make_gateway(api_key=fake)
    rt = start(tmp_path, "g", 42, use_llm=True, gateway=gw)
    sizes = {}
    try:
        n = 0
        for target in (1, 10, 30):
            while n < target and not rt.closed:
                p = rt.pending()
                rt.act(choose(p.scene, p.journey_tick))
                n += 1
            blob = rt.snapshot()
            sizes[target] = (n, len(blob))
            raw = zlib.decompress(blob[len(SNAPSHOT_MAGIC) + 2:])
            inner = pickle.loads(raw)["session"]
            for hay in (blob, raw, inner):
                assert fake.encode() not in hay
                assert b"sk-ant" not in hay
                assert b"anthropic.Anthropic" not in hay and b"httpx" not in hay
        print("snapshot sizes (cycles, bytes):", sizes)
        assert sizes[30][1] < 1_000_000
    finally:
        rt.close()


# ── (h) the model path, and re-attaching a gateway on restore ──────────────

def test_llm_runtime_pending_act_and_restore_reattaches_gateway(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no `ant auth login` profile either
    gw1, rec1 = make_gateway()
    rt = start(tmp_path, "h", 42, use_llm=True, gateway=gw1)
    try:
        for _ in range(3):
            p = rt.pending()
            assert p.scene.source == "llm"
            rt.act(UserAction(raw="", choice_index=0))
        assert rec1.requests
        rt.pending()
        blob = rt.snapshot()
    finally:
        rt.close()

    gw2, rec2 = make_gateway()
    rt2 = JourneyRuntime.restore(blob, shard_db=str(tmp_path / "h" / "shards.db"), gateway=gw2)
    try:
        eng = rt2.engine
        for mod in (eng.generative, eng.interpreter, eng.npc):
            assert mod.gateway is gw2
        assert rt2.session.gateway is gw2
        before = len(rec2.requests)
        rec = rt2.act(UserAction(raw="I light the lamp myself.", choice_index=0))
        assert len(rec2.requests) > before          # the new gateway is the one used
        assert rec.outcome.lesson == "I can act before I feel ready."
        p = rt2.pending()
        assert p.scene.source == "llm"
    finally:
        rt2.close()

    # Restoring without a gateway gives a fresh default one (offline here).
    rt3 = JourneyRuntime.restore(blob, shard_db=None)
    try:
        assert rt3.engine.generative.gateway is not gw1
        rt3.act(UserAction(raw="", choice_index=0))
    finally:
        rt3.close()


def test_per_turn_model_switch(tmp_path):
    gw, rec = make_gateway()
    rt = start(tmp_path, "sw", 42, use_llm=True, gateway=gw)
    try:
        assert rt.model_enabled
        rt.set_model_enabled(False)          # e.g. the daily model budget is spent
        n = len(rec.requests)
        p = rt.pending()
        rt.act(UserAction(raw="", choice_index=0))
        assert p.scene.source == "deterministic" and len(rec.requests) == n
        rt.set_model_enabled(True)
        assert rt.pending().scene.source == "llm"
    finally:
        rt.close()
    off = start(tmp_path, "sw2", 42, use_llm=False, gateway=make_gateway()[0])
    off.set_model_enabled(True)              # cannot switch a deterministic journey on
    assert not off.model_enabled and off.pending().scene.source == "deterministic"
    off.close()
