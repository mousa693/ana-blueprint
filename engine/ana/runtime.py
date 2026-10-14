"""Journey runtime — the turn-based engine the web product drives.

The CLI runs a journey in one go (``FeedbackLoopEngine.step()`` with a driver
choosing each action). On the web a *person* supplies each action, possibly
minutes or days later, from a different process. This module splits the loop
into "show a scene" (:meth:`JourneyRuntime.pending`) and "resolve an action"
(:meth:`JourneyRuntime.act`), and makes a journey survive restarts
(:meth:`JourneyRuntime.snapshot` / :meth:`JourneyRuntime.restore`).

Contract (docs/PRODUCT_SPEC.md §1):

* ``pending()`` then ``act()`` produce exactly the records ``step()`` would with
  a driver returning the same actions (``begin_cycle`` / ``complete_cycle``).
* ``snapshot()`` → ``restore()`` → continue produces the same next scene and
  records as continuing without the round trip (deterministic mode).
* Memory shards live in ``shard_db`` scoped by ``identity_id``; different
  identities never share (and a runtime only ever holds its own identity's
  nodes in memory, so a snapshot never contains another person's shards).
* The model gateway, sqlite connections and event loops are never pickled.

The self across journeys
------------------------
Two things carry a person's self from one journey to the next:

1. **Shards** (lessons, fulfilled visions, seeded beliefs/visions, raw events)
   are written to ``shard_db`` as they happen, scoped by ``identity_id``. Any
   later :meth:`JourneyRuntime.start` for the same ``identity_id`` and
   ``shard_db`` recalls them — no extra work for the caller.
2. **Evolving self-state** (trait values, goal progress/realisation, learned
   active themes, emotion, the journal behind the insights, the fulfilled
   statements) lives on the in-memory Identity Core, which is per journey.
   :meth:`JourneyRuntime.identity_state` returns it as a JSON-safe dict; the
   backend stores it (e.g. on the ``identities`` row) when a journey ends and
   passes it back as ``start(..., identity_state=...)``. The seed stays the
   person's declaration (name, beliefs, goals, forbidden themes); the state
   overlays what the self has *learned* on top of it. Journal ticks are rebased
   so the previous journey's entries sit at negative ticks (the new journey
   still starts at tick 0).

Snapshots are pickles: only ever restore blobs this server produced and stored
itself (they are server-side only and never sent to a browser).
"""
from __future__ import annotations

import asyncio
import io
import json
import pickle
import sqlite3
import tempfile
import zlib
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .config import ANAConfig
from .contracts import CycleRecord, EmotionalState, Goal, Scene, Trait, UserAction
from .cores.mcmg import (
    MCMG,
    MCMGConfig,
    MCMGIndex,
    MemoryCluster,
    MemoryNode,
    CoreOrigin,
    _stable_key,
)
from .engine.arc import CHAPTER_NAMES, STAGES, Debrief, normalize_stage
from .engine.feedback_loop import PendingCycle
from .identity.core import IdentityCore
from .identity.journal import Journal
from .identity.memory import PersistenceError, ShardMemory, scope_id
from .llm.gateway import ModelGateway
from .session import ANASession, build_session
from .subsystems.alignment import mentions_forbidden

__all__ = [
    "SeedGoal", "SeedMemory", "IdentitySeed", "PendingScene", "JourneyRuntime",
    "JourneyClosedError", "NoPendingSceneError", "SnapshotVersionError",
    "SNAPSHOT_VERSION", "IDENTITY_STATE_VERSION",
]

# Snapshot blob = MAGIC + 2-byte big-endian version + zlib(pickle(protocol 5)).
SNAPSHOT_MAGIC = b"ANA-RT"
SNAPSHOT_VERSION = 1
IDENTITY_STATE_VERSION = 1

_MAX_ACTION_CHARS = 2000


class JourneyClosedError(RuntimeError):
    """The journey has come home, hit max_cycles or was ended."""


class NoPendingSceneError(RuntimeError):
    """``act()`` was called with no scene pending (call ``pending()`` first)."""


class SnapshotVersionError(ValueError):
    """The blob is not a runtime snapshot, or was written by an unknown format."""


# ─────────────────────────────────────────────────────────────
# What onboarding produces
# ─────────────────────────────────────────────────────────────

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


class SeedGoal(BaseModel):
    description: str
    horizon: Literal["near", "mid", "life"] = "mid"
    is_future_memory: bool = False


class SeedMemory(BaseModel):
    summary: str
    themes: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)


class IdentitySeed(BaseModel):
    name: str = "the wanderer"
    traits: dict[str, float] = Field(default_factory=dict)
    beliefs: list[str] = Field(default_factory=list)
    goals: list[SeedGoal] = Field(default_factory=list)
    memories: list[SeedMemory] = Field(default_factory=list)
    active_themes: list[str] = Field(default_factory=list)
    forbidden_themes: list[str] = Field(default_factory=list)
    valence: float = 0.0
    stress: float = 0.2

    @field_validator("traits")
    @classmethod
    def _traits_in_range(cls, v: dict[str, float]) -> dict[str, float]:
        return {k: _clamp(x, 0.0, 1.0) for k, x in v.items()}

    @field_validator("valence")
    @classmethod
    def _valence_in_range(cls, v: float) -> float:
        return _clamp(v, -1.0, 1.0)

    @field_validator("stress")
    @classmethod
    def _stress_in_range(cls, v: float) -> float:
        return _clamp(v, 0.0, 1.0)


class PendingScene(BaseModel):
    """What the UI renders while waiting for the person."""

    journey_tick: int
    scene: Scene
    chapter: str
    stage: Optional[str] = None
    convergence: float
    location: str


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

class WebDriver:
    """Placeholder driver for web journeys: the person supplies every action via
    :meth:`JourneyRuntime.act`, so the engine's ``step()`` must never be used."""

    def act(self, scene, identity):  # pragma: no cover - guard only
        raise RuntimeError("web journeys take their actions from JourneyRuntime.act()")


def _bad(text: Optional[str], forbidden: list[str]) -> bool:
    return bool(text) and bool(forbidden) and \
        mentions_forbidden(text, forbidden, synonyms=True) is not None


def _connect(mem: ShardMemory, db_path: str) -> None:
    """Back an (in-memory, scoped) ShardMemory's graph with the SQLite store at
    ``db_path`` — same connection settings as MCMG's own — WITHOUT loading
    anything. Raises PersistenceError if it cannot be opened."""
    g = mem._g
    try:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception as e:
        raise PersistenceError(f"could not open shard store {db_path!r}: "
                               f"{type(e).__name__}: {e}") from e
    g._db = conn
    g.db_path = db_path
    g.db_error = None
    g._init_db()
    mem.db_path = db_path


def _load_scoped(mem: ShardMemory) -> int:
    """Load this scope's persisted shard/identity nodes (only these — never other
    identities') into the graph, exactly as MCMG._load_shards would: creation
    order, embeddings recomputed."""
    g = mem._g
    pattern = f'%"cluster_id": "{mem._cluster}"%'
    rows = g._db.execute(
        "SELECT data FROM nodes WHERE (is_shard = 1 OR origin = ?) AND data LIKE ?",
        (CoreOrigin.IDENTITY.value, pattern)).fetchall()
    loaded: list[MemoryNode] = []
    for row in rows:
        try:
            node = MemoryNode.from_dict(json.loads(row["data"]))
        except (ValueError, KeyError):
            continue
        if node.cluster_id == mem._cluster:
            loaded.append(node)
    loaded.sort(key=_stable_key)
    for node in loaded:
        node.embedding = g._embedder.embed(str(node.content))
        g._nodes[node.id] = node
        g._index.add(node)
    return len(loaded)


def _scoped_memory(identity_id: str, db_path: Optional[str]) -> ShardMemory:
    mem = ShardMemory(scope=identity_id)
    if db_path:
        try:
            _connect(mem, db_path)
        except Exception:
            mem.close()
            raise
    return mem


def _attach_store(identity: IdentityCore, db_path: str) -> None:
    """``IdentityCore.attach_persistent_memory`` semantics (earlier sessions'
    shards for this identity are loaded, seeded nodes copied across, skipping
    content the store already holds), but reading only this identity's rows."""
    new = _scoped_memory(identity.identity_id, db_path)
    _load_scoped(new)
    old = identity.memory
    new.import_from(old)
    identity.memory = new
    old.close()


def _memory_state(mem: ShardMemory) -> dict:
    """Everything needed to rebuild the memory graph exactly (including
    non-persisted OMEGA nodes). Embeddings are derived data and recomputed."""
    g = mem._g
    return {
        "scope": mem.scope,
        "config": g.config.to_dict(),
        "clusters": [c.to_dict() for c in g._clusters.values()],
        "nodes": [n.to_dict() for n in g._nodes.values()],
        "embedder": g._embedder,
        "last_decay": g._last_decay,
        "decay_count": g._decay_count,
    }


def _memory_from_state(state: dict, db_path: Optional[str]) -> ShardMemory:
    """Rebuild a ShardMemory from :func:`_memory_state` and (re)connect it to
    ``db_path`` without loading from it — the snapshot already holds every
    node this identity had, with the same ids, so nothing is duplicated."""
    scope = state["scope"]
    mem = ShardMemory(scope=scope) if scope else ShardMemory()
    g: MCMG = mem._g
    g.config = MCMGConfig.from_dict(state["config"])
    g._embedder = state["embedder"]
    g._last_decay = state["last_decay"]
    g._decay_count = state["decay_count"]
    g._nodes = {}
    g._index = MCMGIndex()
    g._clusters = {}
    for cd in state["clusters"]:
        g._clusters[cd["id"]] = MemoryCluster.from_dict(cd)
    for nd in state["nodes"]:
        node = MemoryNode.from_dict(nd)
        node.embedding = g._embedder.embed(str(node.content))
        g._nodes[node.id] = node
        g._index.add(node)
    if db_path:
        if not scope:
            mem.close()
            raise PersistenceError("cannot attach an unscoped memory to a shared shard store")
        try:
            _connect(mem, db_path)
        except Exception:
            mem.close()
            raise
    return mem


# Pickling: live handles are swapped for persistent ids and re-attached on load.
_GATEWAY_PID = "ana.gateway"
_MEMORY_PID = "ana.memory"


class _Pickler(pickle.Pickler):
    def __init__(self, file, memory: ShardMemory):
        super().__init__(file, protocol=5)
        self._memory = memory

    def persistent_id(self, obj: Any):
        if isinstance(obj, ModelGateway):
            return _GATEWAY_PID
        if isinstance(obj, ShardMemory):
            if obj is not self._memory:
                raise pickle.PicklingError("unexpected second ShardMemory in the runtime")
            return _MEMORY_PID
        if isinstance(obj, (sqlite3.Connection, asyncio.AbstractEventLoop)):
            raise pickle.PicklingError(f"live handle {type(obj).__name__} reached the snapshot")
        return None


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, *, gateway: ModelGateway, memory: ShardMemory):
        super().__init__(file)
        self._handles = {_GATEWAY_PID: gateway, _MEMORY_PID: memory}

    def persistent_load(self, pid: Any):
        try:
            return self._handles[pid]
        except (KeyError, TypeError):
            raise pickle.UnpicklingError(f"unknown persistent id {pid!r}") from None


def _default_gateway(cfg: ANAConfig) -> ModelGateway:
    return ModelGateway(model=cfg.model, max_tokens=cfg.max_tokens, enabled=cfg.use_llm,
                        effort=cfg.effort, use_fallbacks=cfg.use_fallbacks)


def _data_dir(shard_db: Optional[str]) -> Path:
    if shard_db and shard_db != ":memory:":
        return Path(shard_db).parent
    return Path(tempfile.gettempdir()) / "ana_runtime"


# ─────────────────────────────────────────────────────────────
# Seed / identity state -> Identity Core
# ─────────────────────────────────────────────────────────────

def _identity_from_seed(seed: IdentitySeed, identity_id: str,
                        state: Optional[dict]) -> IdentityCore:
    ic = IdentityCore(name=seed.name, identity_id=identity_id,
                      memory=ShardMemory(scope=identity_id))
    for name, value in seed.traits.items():
        ic.set_trait(name, value)
    for belief in seed.beliefs:
        ic.add_belief(belief)
    for g in seed.goals:
        ic.add_goal(g.description, horizon=g.horizon, is_future_memory=g.is_future_memory)
    for m in seed.memories:
        ic.seed_memory(m.summary, themes=list(m.themes), symbols=list(m.symbols))
    ic.active_themes = list(dict.fromkeys([*seed.active_themes, *ic.active_themes]))
    ic.emotion.valence = seed.valence
    ic.emotion.stress = seed.stress
    ic.emotion.clamp()
    if state:
        _apply_identity_state(ic, state)
    return ic


def _apply_identity_state(ic: IdentityCore, state: dict) -> None:
    """Overlay what the self learned in earlier journeys onto a freshly seeded
    Identity Core (see the module docstring)."""
    version = state.get("version", IDENTITY_STATE_VERSION)
    if version != IDENTITY_STATE_VERSION:
        raise ValueError(f"unknown identity_state version {version!r} "
                         f"(this engine reads version {IDENTITY_STATE_VERSION})")
    shift = int(state.get("journey_ticks", 0))

    for t in state.get("traits", []):
        tr = Trait.model_validate(t)
        ic.traits[tr.name] = tr

    by_desc = {g.description.strip().lower(): i for i, g in enumerate(ic.goals)}
    for gd in state.get("goals", []):
        goal = Goal.model_validate(gd)
        goal.advanced_at = [t - shift for t in goal.advanced_at]
        if goal.realised_at is not None:
            goal.realised_at -= shift
        key = goal.description.strip().lower()
        if key in by_desc:
            seeded = ic.goals[by_desc[key]]
            # The person's declaration (horizon, future-memory flag) wins; the
            # learned progress carries over.
            ic.goals[by_desc[key]] = goal.model_copy(update={
                "horizon": seeded.horizon, "is_future_memory": seeded.is_future_memory})
        else:
            ic.goals.append(goal)
            if goal.is_future_memory:
                ic.memory.record_future_memory(goal.description)

    themes = list(state.get("active_themes", []))
    ic.active_themes = list(dict.fromkeys([*themes, *ic.active_themes]))
    if state.get("emotion"):
        ic.emotion = EmotionalState.model_validate(state["emotion"]).clamp()
    if state.get("journal"):
        j = Journal.from_dict(state["journal"])
        for e in j.entries:
            e.tick -= shift
        ic.journal = j
    ic._fulfilled = list(state.get("fulfilled_visions", []))


# ─────────────────────────────────────────────────────────────
# The runtime
# ─────────────────────────────────────────────────────────────

class JourneyRuntime:
    """One person's journey, driven a turn at a time. Build with :meth:`start`
    or :meth:`restore`; release with :meth:`close`."""

    def __init__(self, session: ANASession, *, identity_id: str, shard_db: Optional[str],
                 use_llm: bool, seed_value: int, max_cycles: int,
                 forbidden: list[str], ended: bool = False):
        self._session = session
        self.identity_id = identity_id
        self.shard_db = shard_db
        self.use_llm = use_llm
        self.seed_value = seed_value
        self.max_cycles = max_cycles
        self._forbidden = list(forbidden)
        self._ended = ended
        self._closed_handles = False

    # -- construction ----------------------------------------------------
    @classmethod
    def start(cls, seed: IdentitySeed, *, identity_id: str, shard_db: Optional[str],
              use_llm: bool, gateway: Optional[ModelGateway] = None, seed_value: int = 42,
              max_cycles: int = 30, identity_state: Optional[dict] = None) -> "JourneyRuntime":
        """A new journey for ``seed``. With ``shard_db`` the self's shards are
        persisted there, scoped by ``identity_id`` (earlier journeys' shards
        for that id are recalled). ``identity_state`` — from a previous
        journey's :meth:`identity_state` — carries the evolving self forward."""
        if not identity_id or not str(identity_id).strip():
            raise ValueError("identity_id is required")
        if max_cycles < 1:
            raise ValueError("max_cycles must be >= 1")
        iid = scope_id(identity_id)
        forbidden = list(dict.fromkeys(f.strip() for f in seed.forbidden_themes if f.strip()))
        cfg = ANAConfig(use_llm=use_llm, max_cycles=max_cycles, seed=seed_value,
                        forbidden_themes=forbidden, data_dir=_data_dir(shard_db),
                        shard_db=shard_db or None, identity_id=iid)
        identity = _identity_from_seed(seed, iid, identity_state)
        if shard_db:
            try:
                _attach_store(identity, shard_db)
            except Exception:
                identity.close()
                raise
        session = build_session(cfg, identity=identity, driver=WebDriver(),
                                gateway=gateway or _default_gateway(cfg))
        session.engine.arc.max_cycles = max_cycles
        return cls(session, identity_id=iid, shard_db=shard_db or None, use_llm=use_llm,
                   seed_value=seed_value, max_cycles=max_cycles, forbidden=forbidden)

    # -- turn loop -------------------------------------------------------
    @property
    def engine(self):
        return self._session.engine

    @property
    def session(self) -> ANASession:
        return self._session

    @property
    def closed(self) -> bool:
        """The journey came home, hit max_cycles, or was ended."""
        return (self._ended or self.engine.closed
                or len(self.engine.history) >= self.max_cycles)

    @property
    def ended(self) -> bool:
        """True iff the person closed the journey early with :meth:`end`."""
        return self._ended

    def pending(self) -> PendingScene:
        """The scene waiting for the person; dreams the next one if none is
        pending. Idempotent. Raises JourneyClosedError once closed."""
        if self.closed:
            raise JourneyClosedError("the journey is closed")
        return self._pending_scene(self.engine.begin_cycle())

    @property
    def model_enabled(self) -> bool:
        """Whether the model dreams/interprets the next turn (always False for
        a ``use_llm=False`` journey)."""
        return bool(self.use_llm and getattr(self.engine.generative, "use_llm", False))

    def set_model_enabled(self, on: bool) -> None:
        """Per-turn model switch (e.g. the global daily model budget is spent:
        this turn runs deterministically). Never turns the model on for a
        journey started with ``use_llm=False``. Only this runtime's modules are
        touched — a gateway shared between runtimes is left alone. Takes effect
        for the next scene dreamed by ``pending()`` and the next ``act()``."""
        on = bool(on and self.use_llm)
        for mod in (self.engine.generative, self.engine.interpreter):
            if hasattr(mod, "use_llm"):
                mod.use_llm = on

    def has_pending(self) -> bool:
        return self.engine.pending is not None

    def act(self, action: UserAction) -> CycleRecord:
        """Resolve the pending scene with the person's action. A
        ``choice_index`` must name one of the scene's choices (its text fills
        an empty ``raw``); free text is clipped."""
        if self.closed:
            raise JourneyClosedError("the journey is closed")
        p = self.engine.pending
        if p is None:
            raise NoPendingSceneError("no scene is pending; call pending() first")
        action = self._normalise_action(action, p.scene)
        return self.engine.complete_cycle(action)

    @staticmethod
    def _normalise_action(action: UserAction, scene: Scene) -> UserAction:
        raw = (action.raw or "").strip()
        idx = action.choice_index
        if idx is not None:
            if isinstance(idx, bool) or not 0 <= idx < len(scene.choices):
                raise ValueError(f"choice_index {idx!r} is not one of the "
                                 f"{len(scene.choices)} choices")
            raw = raw or scene.choices[idx]
        if not raw:
            raise ValueError("an action needs a choice_index or text")
        raw = raw[:_MAX_ACTION_CHARS]
        if raw == action.raw:
            return action
        return action.model_copy(update={"raw": raw})

    def _pending_scene(self, p: PendingCycle) -> PendingScene:
        scene = p.scene
        stage = normalize_stage(scene.stage or p.stage) or self.engine.arc.stage or STAGES[0]
        chapters = self.engine.arc.chapters()
        n = len(chapters) if chapters and chapters[-1]["stage"] == stage else len(chapters) + 1
        return PendingScene(
            journey_tick=p.ident.tick,
            scene=scene.model_copy(deep=True),
            chapter=f"Chapter {n}: {CHAPTER_NAMES[stage]}",
            stage=stage,
            convergence=float(p.ident.convergence),
            location=p.request.world.location,
        )

    def end(self) -> None:
        """Close the journey early (the person chose to stop). Any pending
        scene is dropped unplayed."""
        if self._ended:
            return
        self._ended = True
        self.engine.abandon_cycle()

    # -- reading ---------------------------------------------------------
    @property
    def history(self) -> list[CycleRecord]:
        return list(self.engine.history)

    @property
    def forbidden_themes(self) -> list[str]:
        return list(self._forbidden)

    def insights(self, limit: int = 6) -> list[str]:
        """The journal's meta-analysis of the self (never naming a forbidden
        theme)."""
        out = [s for s in self._session.identity.journal.meta_analysis()
               if not _bad(s, self._forbidden)]
        return out[:limit] if limit else out

    def debrief(self) -> Debrief:
        """A reflection on the journey so far (ana.engine.arc.Debrief). Built
        from the story's safe view of the self, and filtered again, so it never
        names a forbidden theme."""
        from .engine.arc import build_debrief

        fb = self._forbidden
        records = self.engine.history
        if fb:
            records = [r.model_copy(update={"identity_before": r.request.identity})
                       for r in records]
        cast = getattr(self.engine.npc, "cast_summary", lambda: None)()
        d = build_debrief(records, gateway=self._session.gateway, use_llm=self.use_llm,
                          arc=self.engine.arc, cast=cast)
        return _safe_debrief(d, fb)

    def identity_state(self) -> dict:
        """The evolving self, JSON-safe, for the next journey's
        ``start(identity_state=...)``. See the module docstring."""
        ic = self._session.identity
        return {
            "version": IDENTITY_STATE_VERSION,
            "identity_id": self.identity_id,
            "journey_ticks": ic.tick,
            "traits": [t.model_dump(mode="json") for t in ic.traits.values()],
            "goals": [g.model_dump(mode="json") for g in ic.goals],
            "active_themes": list(ic.active_themes),
            "emotion": ic.emotion.model_dump(mode="json"),
            "journal": {"entries": [e.to_dict() for e in ic.journal.entries]},
            "fulfilled_visions": ic.fulfilled_visions(),
        }

    # -- persistence -----------------------------------------------------
    def snapshot(self) -> bytes:
        """Full resumable state (format version :data:`SNAPSHOT_VERSION`),
        excluding the gateway, sqlite connection and event loop."""
        if self._closed_handles:
            raise RuntimeError("cannot snapshot a closed runtime")
        mem = self._session.identity.memory
        buf = io.BytesIO()
        _Pickler(buf, mem).dump(self._session)
        payload = {
            "identity_id": self.identity_id,
            "use_llm": self.use_llm,
            "seed_value": self.seed_value,
            "max_cycles": self.max_cycles,
            "forbidden": list(self._forbidden),
            "ended": self._ended,
            "memory": _memory_state(mem),
            "session": buf.getvalue(),
        }
        body = pickle.dumps(payload, protocol=5)
        return SNAPSHOT_MAGIC + SNAPSHOT_VERSION.to_bytes(2, "big") + zlib.compress(body, 6)

    @classmethod
    def restore(cls, blob: bytes, *, shard_db: Optional[str],
                gateway: Optional[ModelGateway] = None) -> "JourneyRuntime":
        """Rebuild a runtime from :meth:`snapshot`, re-attaching ``gateway`` (or
        a fresh default one) and re-opening ``shard_db`` (no shard is loaded
        twice: the snapshot holds the identity's whole memory graph)."""
        version = snapshot_version(blob)
        if version != SNAPSHOT_VERSION:
            raise SnapshotVersionError(
                f"unsupported journey snapshot version {version} "
                f"(this engine reads version {SNAPSHOT_VERSION})")
        try:
            payload = pickle.loads(zlib.decompress(blob[len(SNAPSHOT_MAGIC) + 2:]))
        except Exception as e:
            raise SnapshotVersionError(f"corrupt journey snapshot: {type(e).__name__}") from e
        memory = _memory_from_state(payload["memory"], shard_db or None)
        try:
            # The session's config is inside the pickle, so a default gateway
            # is made first and configured from that config just below.
            gw = gateway
            if gw is None:
                gw = _default_gateway(ANAConfig(use_llm=payload["use_llm"]))
            session: ANASession = _Unpickler(io.BytesIO(payload["session"]),
                                             gateway=gw, memory=memory).load()
        except Exception:
            memory.close()
            raise
        if gateway is None:
            cfg = session.config
            gw.model, gw.max_tokens, gw.effort = cfg.model, cfg.max_tokens, cfg.effort
            gw.use_fallbacks, gw.enabled = cfg.use_fallbacks, cfg.use_llm
        session.config.shard_db = shard_db or None
        return cls(session, identity_id=payload["identity_id"], shard_db=shard_db or None,
                   use_llm=payload["use_llm"], seed_value=payload["seed_value"],
                   max_cycles=payload["max_cycles"], forbidden=payload["forbidden"],
                   ended=payload["ended"])

    def close(self, *, flush: bool = True) -> None:
        """Release the memory's sqlite connection and event loop. Idempotent;
        the runtime is unusable afterwards (restore a snapshot to go on).

        By default the identity's shards are flushed to ``shard_db`` first (as
        ``ShardMemory.close`` always does). Pass ``flush=False`` when the
        identity's shards are being deleted — otherwise closing a cached
        runtime would write them back."""
        if self._closed_handles:
            return
        self._closed_handles = True
        if not flush:
            g = self._session.identity.memory._g
            if g._db is not None:
                try:
                    g._db.close()
                finally:
                    g._db = None
        self._session.close()


def snapshot_version(blob: bytes) -> int:
    """The format version of a :meth:`JourneyRuntime.snapshot` blob. Raises
    SnapshotVersionError if ``blob`` is not a runtime snapshot at all."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise SnapshotVersionError("a journey snapshot must be bytes")
    blob = bytes(blob[:len(SNAPSHOT_MAGIC) + 2])
    if len(blob) < len(SNAPSHOT_MAGIC) + 2 or not blob.startswith(SNAPSHOT_MAGIC):
        raise SnapshotVersionError("not an ANA journey snapshot")
    return int.from_bytes(blob[len(SNAPSHOT_MAGIC):], "big")


def _safe_debrief(d: Debrief, forbidden: list[str]) -> Debrief:
    if not forbidden:
        return d
    upd: dict[str, Any] = {}
    for name in ("chapters", "turning_points", "lessons", "how_you_changed",
                 "recurring_symbols", "open_questions", "companions"):
        items = getattr(d, name)
        kept = [x for x in items if not _bad(x, forbidden)]
        if kept != items:
            upd[name] = kept
    if _bad(d.title, forbidden):
        upd["title"] = "Your journey"
    if _bad(d.arc_summary, forbidden):
        upd["arc_summary"] = "A journey was lived, one scene at a time."
    if _bad(d.closing_image, forbidden):
        upd["closing_image"] = ""
    return d.model_copy(update=upd) if upd else d
