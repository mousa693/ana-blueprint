"""Programmatic ANA journey — build a self, run the loop, inspect the result.

Run:  python examples/run_journey.py
Everything is deterministic and offline (no API key needed).
"""
from ana.config import ANAConfig
from ana.identity.core import IdentityCore
from ana.identity.memory import ShardMemory
from ana.observers import ConsoleObserver
from ana.session import build_session


def seed_self() -> IdentityCore:
    ic = IdentityCore(name="the wanderer", memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.set_trait("honesty", 0.8)
    ic.set_trait("courage", 0.4)
    ic.add_belief("I am not the kind of person others follow.")
    ic.add_goal("find my purpose", horizon="life", is_future_memory=True)
    ic.seed_memory("A childhood by a lighthouse on a grey coast.",
                   themes=["purpose", "courage"], symbols=["lighthouse"])
    ic.active_themes = ["leadership", "purpose", "courage"]
    ic.emotion.valence = -0.1
    return ic


def main() -> None:
    cfg = ANAConfig(use_llm=False, max_cycles=12)   # set use_llm=True with a key
    session = build_session(cfg, identity=seed_self(),
                            observers=[ConsoleObserver(show_telemetry=True)])
    session.run()

    ic = session.identity
    print("\nFinal self:")
    for name, t in sorted(ic.traits.items()):
        print(f"  {name}: {t.value:.2f}")
    print(f"  convergence: {ic.convergence:.2f}")
    print(f"  memory shards: {ic.memory.stats()['total_nodes']}")
    session.close()


if __name__ == "__main__":
    main()
