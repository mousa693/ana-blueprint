"""ANASession — assembles a complete ANA simulation from config.

This is the top-level entry point: it constructs the Identity Core, Environment
Mirror, the generative engine, the NPC/entropy layer, the three cross-cutting
subsystems, the interpreter, a driver, and the convergence engine, and wires them
into a Feedback Loop Engine. Callers get a ready-to-run session.
"""
from __future__ import annotations

from typing import Optional

from .config import ANAConfig
from .contracts import CycleRecord
from .environment.mirror import EnvironmentMirror
from .identity.core import IdentityCore
from .identity.memory import ShardMemory
from .interfaces import Driver, Observer
from .llm.gateway import ModelGateway

from .engine.convergence import ConvergenceEngine
from .engine.drivers import AutonomousDriver
from .engine.feedback_loop import FeedbackLoopEngine
from .engine.interpret import Interpreter

from .generative.engine import ExperienceEngine
from .npc.layer import NPCDirector
from .subsystems.synchronicity import SynchronicityOrchestrator
from .subsystems.alignment import SelfAlignment
from .subsystems.harmonizer import TruthHarmonizer


class ANASession:
    def __init__(self, identity: IdentityCore, engine: FeedbackLoopEngine,
                 config: ANAConfig):
        self.identity = identity
        self.engine = engine
        self.config = config

    def run(self, cycles: Optional[int] = None, *, until_close: bool = False) -> list[CycleRecord]:
        return self.engine.run(cycles if cycles is not None else self.config.max_cycles,
                               until_close=until_close)

    @property
    def persistent(self) -> bool:
        """True iff the identity's shards are being written to disk."""
        return self.identity.memory.persistent

    @property
    def arc(self):
        return self.engine.arc

    def debrief(self, use_llm: Optional[bool] = None):
        """A reflection back to the person on the journey so far (see engine.arc)."""
        from .engine.arc import build_debrief

        cast = getattr(self.engine.npc, "cast_summary", lambda: None)()
        return build_debrief(
            self.engine.history, gateway=self.gateway,
            use_llm=self.config.use_llm if use_llm is None else use_llm,
            arc=self.engine.arc, cast=cast)

    def step(self) -> CycleRecord:
        return self.engine.step()

    @property
    def gateway(self) -> Optional[ModelGateway]:
        return getattr(self.engine.generative, "gateway", None)

    def close(self) -> None:
        self.identity.close()


def build_session(
    config: Optional[ANAConfig] = None,
    *,
    identity: Optional[IdentityCore] = None,
    driver: Optional[Driver] = None,
    observers: Optional[list[Observer]] = None,
    persist_shards: bool = False,
    gateway: Optional[ModelGateway] = None,
) -> ANASession:
    """Assemble a full ANA session with the default module implementations.

    If `identity` is not supplied, an empty Identity Core is created (seed it
    afterwards or before calling .run).

    `persist_shards=True` backs the identity's memory with the SQLite store at
    config.shard_db, scoped to the identity's `identity_id` (so different
    identities sharing one DB never recall each other's shards):

      * no `identity` -> a new identity (id = config.identity_id or derived from
        its name) with persistent memory;
      * an `identity` whose memory is already persistent -> used as is;
      * an `identity` with in-memory memory (the usual seeded case) -> its
        memory is attached to config.shard_db via
        `IdentityCore.attach_persistent_memory`: earlier sessions' shards for
        that identity are loaded and the seeded shards are copied across.

    If the DB cannot be opened, a RuntimeWarning is issued, the error is logged
    and `session.persistent` is False (memory stays in-process).
    """
    config = config or ANAConfig()
    config.ensure_dirs()

    gateway = gateway or ModelGateway(
        model=config.model, max_tokens=config.max_tokens, enabled=config.use_llm,
        effort=config.effort, use_fallbacks=config.use_fallbacks)

    if identity is None:
        if persist_shards:
            identity = IdentityCore.with_persistent_memory(
                config.shard_db, identity_id=config.identity_id)
        else:
            identity = IdentityCore(memory=ShardMemory(), identity_id=config.identity_id)
    elif persist_shards and not identity.memory.persistent:
        identity.attach_persistent_memory(config.shard_db)

    environment = EnvironmentMirror()
    generative = ExperienceEngine(gateway=gateway, seed=config.seed,
                                  use_llm=config.use_llm)
    npc = NPCDirector(gateway=gateway, seed=config.seed,
                      entropy_prob=config.entropy_prob, use_llm=False)
    synchronicity = SynchronicityOrchestrator(
        base_prob=config.synchronicity_base_prob,
        cooldown=config.synchronicity_cooldown, seed=config.seed)
    alignment = SelfAlignment(
        stress_ceiling=config.stress_ceiling,
        stress_floor=config.stress_floor_for_boredom,
        forbidden_themes=config.forbidden_themes,
        always_hope_after_dark=config.always_hope_after_dark)
    harmonizer = TruthHarmonizer(seed=config.seed)
    interpreter = Interpreter(gateway=gateway, use_llm=config.use_llm)
    convergence = ConvergenceEngine(seed=config.seed)
    driver = driver or AutonomousDriver(gateway=gateway, seed=config.seed,
                                        use_llm=config.use_llm)

    engine = FeedbackLoopEngine(
        identity=identity, environment=environment, generative=generative,
        npc=npc, synchronicity=synchronicity, alignment=alignment,
        harmonizer=harmonizer, interpreter=interpreter, driver=driver,
        convergence=convergence, observers=observers or [], seed=config.seed,
    )
    return ANASession(identity=identity, engine=engine, config=config)
