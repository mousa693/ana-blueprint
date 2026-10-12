"""Shared data contracts for ANA.

These are the typed objects that flow between every module. They are the fixed
interface the whole system is built against: the Feedback Loop Engine produces a
`CycleContext`, the Generative Engine turns a `GenerationRequest` into a
`Scene`, the subsystems read and annotate these objects, and the Identity Core
folds the resulting `Outcome` back into itself.

Everything here is standard-library + pydantic, deterministic, and
serialisable, so a run can be saved, replayed, and inspected.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ─────────────────────────────────────────────────────────────
# Archetypes & emotion — the symbolic vocabulary
# ─────────────────────────────────────────────────────────────


class Archetype(str, Enum):
    """The Jungian archetypes the blueprint draws on for NPCs and motifs."""

    SELF = "self"
    SHADOW = "shadow"
    ANIMA = "anima"
    ANIMUS = "animus"
    MENTOR = "mentor"
    TRICKSTER = "trickster"
    HERO = "hero"
    THRESHOLD_GUARDIAN = "threshold_guardian"
    HERALD = "herald"
    ALLY = "ally"


class Emotion(str, Enum):
    JOY = "joy"
    FEAR = "fear"
    ANGER = "anger"
    SADNESS = "sadness"
    TRUST = "trust"
    ANTICIPATION = "anticipation"
    SURPRISE = "surprise"
    CURIOSITY = "curiosity"
    AWE = "awe"
    SHAME = "shame"
    ACCEPTANCE = "acceptance"


class EmotionalState(BaseModel):
    """A lightweight affective register for the Identity Core.

    `valence` runs -1 (negative) .. +1 (positive); `arousal` 0 (calm) .. 1
    (activated). `stress` is tracked separately because the Self-Alignment
    Protocols gate on it directly.
    """

    valence: float = 0.0
    arousal: float = 0.3
    stress: float = 0.2
    dominant: Emotion = Emotion.CURIOSITY

    def clamp(self) -> "EmotionalState":
        self.valence = max(-1.0, min(1.0, self.valence))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.stress = max(0.0, min(1.0, self.stress))
        return self


# ─────────────────────────────────────────────────────────────
# Identity snapshot — what the generative engine is handed
# ─────────────────────────────────────────────────────────────


class Trait(BaseModel):
    name: str
    value: float = 0.5           # 0..1 strength
    confidence: float = 0.5


class Goal(BaseModel):
    description: str
    horizon: Literal["near", "mid", "life"] = "mid"
    progress: float = 0.0        # 0..1
    is_future_memory: bool = False  # a "memory of the future" attractor


class IdentitySnapshot(BaseModel):
    """A read-only picture of the Identity Core at one instant.

    This is the `state` the blueprint's loop reads at the top of each cycle. It
    is intentionally flat and serialisable so it can be logged and diffed to
    measure drift over a journey.
    """

    tick: int = 0
    traits: list[Trait] = Field(default_factory=list)
    beliefs: list[str] = Field(default_factory=list)
    goals: list[Goal] = Field(default_factory=list)
    emotion: EmotionalState = Field(default_factory=EmotionalState)
    active_themes: list[str] = Field(default_factory=list)   # e.g. "leadership", "trust"
    recent_shard_summaries: list[str] = Field(default_factory=list)
    # The convergence level from the pi-phi engine: 0 (inner/outer divergent)
    # .. 1 (fully mirrored). Drives the Environment Mirror's reflectiveness dial.
    convergence: float = 0.0

    def trait(self, name: str) -> float:
        for t in self.traits:
            if t.name == name:
                return t.value
        return 0.0


# ─────────────────────────────────────────────────────────────
# World & scene — the Environment Mirror's content
# ─────────────────────────────────────────────────────────────


class NPC(BaseModel):
    id: str = Field(default_factory=lambda: _id("npc"))
    name: str
    archetype: Archetype = Archetype.ALLY
    disposition: float = 0.0      # -1 hostile .. +1 friendly
    autonomy: float = 0.5         # how independent of the user's psyche
    persona: str = ""             # short description driving dialogue
    represents: Optional[str] = None  # identity element this NPC externalises
    alive: bool = True


class WorldState(BaseModel):
    """The current state of the simulated world (the Environment Mirror)."""

    location: str = "an unmarked threshold"
    time_of_day: str = "dawn"
    weather: str = "clear"
    ambience: str = "quiet"
    motifs: list[str] = Field(default_factory=list)   # symbolic objects present
    npcs: list[NPC] = Field(default_factory=list)
    reflectiveness: float = 0.3    # how directly the world mirrors identity (0..1)
    tick: int = 0


class SceneBeat(BaseModel):
    """One narrative beat within a scene."""

    kind: Literal["description", "dialogue", "event", "choice", "synchronicity"]
    text: str
    speaker: Optional[str] = None
    archetype: Optional[Archetype] = None
    symbol: Optional[str] = None       # symbolic tag for later interpretation


class Scene(BaseModel):
    """A generated experience: what the user encounters this cycle."""

    id: str = Field(default_factory=lambda: _id("scene"))
    title: str = ""
    summary: str = ""
    beats: list[SceneBeat] = Field(default_factory=list)
    theme: str = "exploration"
    intensity: float = 0.4             # 0 gentle .. 1 harrowing
    choices: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    is_vision: bool = False            # flagged by harmonizer as non-literal
    source: Literal["llm", "deterministic"] = "deterministic"


# ─────────────────────────────────────────────────────────────
# Requests & outcomes — what crosses module boundaries
# ─────────────────────────────────────────────────────────────


class SynchronicityHint(BaseModel):
    """A suggestion from the Synchronicity Engine to weave a meaningful coincidence."""

    trigger: str
    symbol: str
    suggestion: str
    potency: float = 0.5


class GenerationRequest(BaseModel):
    """Everything the Generative Experience Engine needs to dream the next scene."""

    identity: IdentitySnapshot
    world: WorldState
    directive: str = "continue"        # high-level intent from the loop engine
    target_theme: Optional[str] = None
    target_intensity: float = 0.4
    synchronicity: Optional[SynchronicityHint] = None
    recalled_shards: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)  # from harmonizer/alignment


class UserAction(BaseModel):
    """What the protagonist did in response to a scene."""

    raw: str
    choice_index: Optional[int] = None


class Outcome(BaseModel):
    """The consequences of a cycle, fed back into the Identity Core.

    This is the blueprint's `results` object: what happened, how it landed, and
    what it means — the raw material the Feedback Loop Engine turns into memory
    shards and identity updates.
    """

    scene_id: str
    summary: str = ""
    user_action: Optional[UserAction] = None
    emotion_delta: EmotionalState = Field(default_factory=lambda: EmotionalState(valence=0, arousal=0, stress=0))
    themes_touched: list[str] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    lesson: Optional[str] = None
    trait_updates: dict[str, float] = Field(default_factory=dict)  # name -> delta
    npc_events: list[str] = Field(default_factory=list)
    success: Optional[bool] = None


# ─────────────────────────────────────────────────────────────
# Alignment & harmonizer verdicts
# ─────────────────────────────────────────────────────────────


class AlignmentReport(BaseModel):
    """Verdict from the Self-Alignment Protocols for one cycle."""

    reflection_pct: float = 0.0        # how much of identity the world reflects
    adaptation_level: float = 0.0      # how much identity adapted to the world
    stress: float = 0.0
    in_safe_band: bool = True
    recommended_intensity: float = 0.4
    nudges: list[str] = Field(default_factory=list)   # directives for next cycle
    blocked: bool = False              # scene violates a safety rule
    block_reason: Optional[str] = None


class HarmonizerVerdict(BaseModel):
    """Verdict from the World-Truth Harmonizer on a proposed scene."""

    approved: bool = True
    is_vision: bool = False
    continuity_errors: list[str] = Field(default_factory=list)
    corrections: list[str] = Field(default_factory=list)
    grounding_note: Optional[str] = None


class CycleRecord(BaseModel):
    """A full, inspectable record of one feedback-loop cycle."""

    tick: int
    ts: float = Field(default_factory=time.time)
    identity_before: IdentitySnapshot
    request: GenerationRequest
    scene: Scene
    alignment: AlignmentReport
    harmonizer: HarmonizerVerdict
    outcome: Outcome
    convergence: float = 0.0
    omega_surprise: float = 0.0        # from the pi-phi engine
    alpha_surprise: float = 0.0
