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

    def run(self, cycles: Optional[int] = None) -> list[CycleRecord]:
        return self.engine.run(cycles if cycles is not None else self.config.max_cycles)

    def step(self) -> CycleRecord:
        return self.engine.step()

    def close(self) -> None:
        self.identity.close()


def build_session(
    config: Optional[ANAConfig] = None,
    *,
    identity: Optional[IdentityCore] = None,
    driver: Optional[Driver] = None,
    observers: Optional[list[Observer]] = None,
    persist_shards: bool = False,
) -> ANASession:
    """Assemble a full ANA session with the default module implementations.

    If `identity` is not supplied, an empty Identity Core is created (seed it
    afterwards or before calling .run). `persist_shards=True` backs the memory
    with an on-disk SQLite store at config.shard_db.
    """
    config = config or ANAConfig()
    config.ensure_dirs()

    gateway = ModelGateway(model=config.model, max_tokens=config.max_tokens,
                           enabled=config.use_llm)

    if identity is None:
        mem = ShardMemory(db_path=config.shard_db if persist_shards else None)
        identity = IdentityCore(memory=mem)

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
