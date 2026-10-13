"""The seeded identity used for live runs in the viewer.

Mirrors the CLI's lighthouse example but lives here so the web package does not
couple to `ana/cli.py` (which may change independently).
"""
from __future__ import annotations

from ..identity.core import IdentityCore
from ..identity.memory import ShardMemory

# Same scripted actions as `ana-run demo`, for driver="scripted".
DEMO_ACTIONS = [
    "I explore the town and talk to the fishermen.",
    "I climb toward the lighthouse to see what's wrong.",
    "I organize a rescue and signal the stranded ship.",
    "I speak honestly to the council about what happened.",
    "I accept the role they offer, uneasy but willing.",
]


def seed_identity() -> IdentityCore:
    """The blueprint's worked example: insecure about leadership, values honesty,
    a childhood lighthouse memory, a goal to find purpose. In-memory shards only."""
    ic = IdentityCore(name="the wanderer", memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.set_trait("honesty", 0.8)
    ic.set_trait("courage", 0.4)
    ic.add_belief("I am not the kind of person others follow.")
    ic.add_goal("find my purpose", horizon="life", is_future_memory=True)
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.seed_memory("A childhood by a lighthouse on a grey coast.",
                   themes=["purpose", "courage"], symbols=["lighthouse"])
    ic.active_themes = ["leadership", "purpose", "courage"]
    ic.emotion.valence = -0.1
    ic.emotion.arousal = 0.45
    ic.emotion.dominant = ic.emotion.dominant.CURIOSITY
    return ic
