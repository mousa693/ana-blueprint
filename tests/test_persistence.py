"""Persistence, determinism and identity scoping of the Memory Shard System.

Regression tests for:
  * persistence silently disabled unless ./ana_run existed in the CWD;
  * reloaded shards having no embeddings (semantic recall empty next session);
  * nondeterministic tag-recall order under a fixed seed (uuid4 frozenset order);
  * cross-identity leakage when several identities share one shard DB;
  * build_session(persist_shards=True, identity=...) ignoring persist_shards.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from ana.config import ANAConfig
from ana.identity.core import IdentityCore
from ana.identity.memory import PersistenceError, ShardMemory
from ana.session import build_session

REPO = Path(__file__).resolve().parents[1]


def _seed(ic: IdentityCore) -> IdentityCore:
    ic.set_trait("courage", 0.3)
    ic.set_trait("trust", 0.0)
    ic.add_belief("I am not brave enough to lead.")
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.add_goal("find where I belong", horizon="life", is_future_memory=True)
    ic.seed_memory("I grew up by the sea, near an old lighthouse.",
                   themes=["leadership", "courage"])
    return ic


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHORT_ID = re.compile(r'"(scene|npc)_[0-9a-f]{12}"')
_TS = re.compile(r'"(ts|timestamp|created|created_at|last_accessed)": [0-9.e+-]+')


def _norm(records) -> str:
    s = json.dumps([r.model_dump(mode="json") for r in records], sort_keys=True, default=str)
    s = _UUID.sub("UUID", s)
    s = _SHORT_ID.sub('"ID"', s)
    return _TS.sub(r'"\1": 0', s)


def _journey(cycles: int = 12, seed: int = 42) -> str:
    cfg = ANAConfig(use_llm=False, max_cycles=cycles, seed=seed)
    sess = build_session(cfg, identity=_seed(IdentityCore(name="the wanderer")))
    try:
        return _norm(sess.run())
    finally:
        sess.close()


# ── 1. persistence does not depend on ./ana_run ─────────────────────────────

def test_persists_from_cwd_without_ana_run(tmp_path, monkeypatch):
    work = tmp_path / "cwd"
    work.mkdir()
    monkeypatch.chdir(work)
    db = tmp_path / "nested" / "deeper" / "shards.db"  # parents don't exist yet

    m = ShardMemory(db_path=str(db))
    assert m.persistent and m.persistence_error is None
    m.record_lesson("Fear is not a reason to stop walking.", themes=["courage"])
    m.close()

    assert db.exists()
    assert not (work / "ana_run").exists()  # nothing written relative to the CWD
    rows = sqlite3.connect(db).execute("select count(*) from nodes").fetchone()[0]
    assert rows == 1

    m2 = ShardMemory(db_path=str(db))
    assert m2.recall_lessons("walking", top_k=1) == ["Fear is not a reason to stop walking."]
    m2.close()


def test_session_persist_shards_from_bare_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = ANAConfig(use_llm=False, seed=42, data_dir=tmp_path / "run")
    assert cfg.shard_db == str(tmp_path / "run" / "shards.db")  # derived from data_dir
    sess = build_session(cfg, persist_shards=True)
    assert sess.persistent
    _seed(sess.identity)
    sess.run(6)
    sess.close()
    rows = sqlite3.connect(cfg.shard_db).execute("select count(*) from nodes").fetchone()[0]
    assert rows > 0


def test_unopenable_db_is_loud_not_silent(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    bad = str(blocker / "shards.db")  # parent is a regular file
    with pytest.warns(RuntimeWarning, match="could not open persistent store"):
        m = ShardMemory(db_path=bad)
    assert not m.persistent
    assert m.persistence_error
    m.close()
    with pytest.raises(PersistenceError):
        ShardMemory(db_path=bad, strict=True)


def test_in_memory_is_unchanged():
    m = ShardMemory()
    assert not m.persistent and m.persistence_error is None and m.scope is None
    m.record_future_memory("become someone who can lead", theme="leadership")
    assert m.recall_future() == ["become someone who can lead"]
    m.close()


# ── 2. embeddings recomputed on reload ─────────────────────────────────────

def test_embeddings_and_semantic_recall_after_reopen(tmp_path):
    db = str(tmp_path / "e.db")
    m = ShardMemory(db_path=db)
    m.record_lesson("Fear is not a reason to stop walking toward the lighthouse.",
                    themes=["courage"])
    first = m.recall("afraid lighthouse walking")
    m.close()

    m2 = ShardMemory(db_path=db)
    nodes = list(m2._g)
    assert nodes and all(n.embedding is not None for n in nodes)
    assert m2.stats()["nodes_with_embeddings"] == len(nodes)
    # The query shares no substring with the content — this is semantic recall.
    assert m2.recall("afraid lighthouse walking") == first == [
        "Fear is not a reason to stop walking toward the lighthouse."]
    m2.close()


# ── 3. determinism ─────────────────────────────────────────────────────────

def test_tag_recall_order_is_deterministic_on_ties():
    # Equal-score shards: order must follow creation, not uuid hash order.
    orders = set()
    for _ in range(5):
        m = ShardMemory()
        for v in ("alpha vision", "beta vision", "gamma vision", "delta vision"):
            m.record_future_memory(v)
        orders.add(tuple(m.recall_future(top_k=4)))
        m.close()
    assert orders == {("alpha vision", "beta vision", "gamma vision", "delta vision")}


def test_same_seed_same_records_in_process():
    a = _journey()
    b = _journey()
    assert a == b


_SUBPROC = textwrap.dedent("""
    import hashlib, sys
    sys.path.insert(0, {repo!r})
    sys.path.insert(0, {tests!r})
    from test_persistence import _journey
    print(hashlib.sha256(_journey().encode()).hexdigest())
""")


def test_same_seed_same_records_across_hash_seeds():
    code = _SUBPROC.format(repo=str(REPO), tests=str(REPO / "tests"))
    digests = set()
    for hs in ("0", "1", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=str(REPO),
                             capture_output=True, text=True, timeout=300)
        assert out.returncode == 0, out.stderr[-2000:]
        digests.add(out.stdout.strip().splitlines()[-1])
    import hashlib
    digests.add(hashlib.sha256(_journey().encode()).hexdigest())  # this process
    assert len(digests) == 1, digests


# ── 4. identity scoping on one DB ──────────────────────────────────────────

def test_two_identities_one_db_do_not_leak(tmp_path):
    db = str(tmp_path / "shared.db")
    cfg = lambda: ANAConfig(use_llm=False, seed=42, data_dir=tmp_path, shard_db=db)

    alice = build_session(cfg(), identity=IdentityCore(name="Alice"), persist_shards=True)
    alice.identity.add_goal("become someone who can lead", is_future_memory=True)
    alice.identity.active_themes = ["leadership"]
    alice.run(25)
    alice_lessons = alice.identity.memory.recall_lessons("leadership", top_k=5)
    assert alice_lessons
    alice.close()

    bob = build_session(cfg(), identity=IdentityCore(name="Bob"), persist_shards=True)
    bob.identity.add_goal("heal my family", is_future_memory=True)
    bob.identity.active_themes = ["healing"]
    rec = bob.run(1)[0]
    mem = bob.identity.memory
    assert mem.recall_future(top_k=5) == ["heal my family"]
    assert "become someone who can lead" not in rec.request.recalled_shards
    assert not set(alice_lessons) & set(rec.identity_before.recent_shard_summaries)
    assert not set(alice_lessons) & set(mem.recall_lessons("leadership", top_k=5))
    assert not set(alice_lessons) & set(mem.recall("leadership lead", top_k=10))
    assert mem.fulfilled() == []
    assert mem.find_future_memory("become someone who can lead") is None
    bob.close()

    # Alice, reopened, still remembers her own journey.
    again = IdentityCore.with_persistent_memory(db, name="Alice")
    assert "become someone who can lead" in again.memory.recall_future(top_k=5)
    assert again.memory.fulfilled() == ["I became someone who can lead."]
    again.close()


def test_same_content_in_two_scopes_is_not_shared(tmp_path):
    db = str(tmp_path / "s.db")
    a = ShardMemory(db_path=db, scope="a")
    b = ShardMemory(db_path=db, scope="b")
    ida = a.record_lesson("Courage is a habit.")
    idb = b.record_lesson("Courage is a habit.")
    assert ida != idb  # not duplicate-reinforced across identities
    a.close()
    b.close()


# ── 5. persist_shards with a supplied identity ─────────────────────────────

def test_persist_shards_with_supplied_identity_attaches_db(tmp_path):
    db = tmp_path / "s.db"
    cfg = lambda: ANAConfig(use_llm=False, seed=42, data_dir=tmp_path, shard_db=str(db))

    ic = _seed(IdentityCore(name="the wanderer", memory=ShardMemory(db_path=None)))
    assert not ic.memory.persistent
    sess = build_session(cfg(), identity=ic, persist_shards=True)
    assert sess.persistent and ic.memory.persistent
    assert ic.memory.scope == ic.identity_id == "the-wanderer"
    # seeded shards were carried over
    assert "become someone who can lead" in ic.memory.recall_future(top_k=5)
    sess.run(4)
    sess.close()
    rows1 = sqlite3.connect(db).execute("select count(*) from nodes").fetchone()[0]
    assert rows1 > 0

    # Re-seeding the same identity next session loads, rather than duplicates.
    ic2 = _seed(IdentityCore(name="the wanderer"))
    sess2 = build_session(cfg(), identity=ic2, persist_shards=True)
    contents = [str(n.content) for n in ic2.memory.nodes()]
    assert len(contents) == len(set(contents))
    assert contents.count("Belief: I am not brave enough to lead.") == 1
    sess2.close()

    # Without persist_shards a supplied identity is left alone.
    ic3 = IdentityCore(name="x")
    sess3 = build_session(cfg(), identity=ic3)
    assert not sess3.persistent
    sess3.close()


def test_trait_of_zero_is_not_treated_as_half():
    from ana.contracts import Outcome
    ic = IdentityCore()
    ic.set_trait("trust", 0.0)
    ic.apply_outcome(Outcome(scene_id="s", summary="a small step", trait_updates={"trust": 0.05}))
    assert ic.traits["trust"].value == pytest.approx(0.05)
