"""
The twin's inner state: drives, feelings, relationships and goals (plan §3.4-3.7).

Plain data plus the small amount of arithmetic that belongs to each piece.
Appraisal (``appraisal.py``) decides how events change this state; choice
(``choice.py``) reads it to decide what to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

DRIVES = ("energy", "connection", "competence", "autonomy", "novelty", "meaning", "security")

# Level lost per world hour at weight 0.5.  Energy is handled separately (sleep).
BASE_DECAY = {
    "energy": 0.035,
    "connection": 0.012,
    "competence": 0.008,
    "autonomy": 0.006,
    "novelty": 0.010,
    "meaning": 0.008,
    "security": 0.004,
}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class Drives:
    """Homeostatic needs.  ``level`` 1 = fully met; ``weight`` = how much this person needs it."""

    weight: Dict[str, float]
    level: Dict[str, float] = field(default_factory=lambda: {d: 0.7 for d in DRIVES})

    def decay(self, hours: float, asleep: bool = False) -> None:
        for d in DRIVES:
            if d == "energy" and asleep:
                continue
            rate = BASE_DECAY[d] * (0.5 + self.weight[d])
            if asleep:
                rate *= 0.3
            self.level[d] = clamp(self.level[d] - rate * hours)

    def apply(self, effects: Dict[str, float]) -> None:
        for d, delta in effects.items():
            if d in self.level:
                self.level[d] = clamp(self.level[d] + delta)

    def pressure(self, d: str) -> float:
        """How much this drive is pushing right now: grows sharply as it empties."""
        return self.weight[d] * (1.0 - self.level[d]) ** 1.5

    def relief(self, effects: Dict[str, float]) -> float:
        """How much an expected set of effects would ease current needs."""
        total = 0.0
        for d, delta in effects.items():
            if d not in self.level:
                continue
            gain = clamp(self.level[d] + delta) - self.level[d]
            total += gain * (0.3 + 2.5 * self.pressure(d)) * (0.5 + self.weight[d])
        return total

    def most_pressing(self) -> str:
        return max(DRIVES, key=self.pressure)


@dataclass
class Emotion:
    name: str
    intensity: float
    valence: float  # -1..1
    arousal: float  # 0..1
    cause: str


@dataclass
class Affect:
    """Emotion episodes plus slow mood (pleasure, arousal, dominance)."""

    emotions: List[Emotion] = field(default_factory=list)
    pleasure: float = 0.1
    arousal: float = 0.3
    dominance: float = 0.1
    reactivity: float = 1.0  # from neuroticism; amplifies negative feelings

    def feel(self, name: str, intensity: float, valence: float, arousal: float, cause: str) -> Emotion:
        if valence < 0:
            intensity *= self.reactivity
        emotion = Emotion(name, clamp(intensity), valence, arousal, cause)
        self.emotions.append(emotion)
        return emotion

    def step(self) -> None:
        """One tick: mood drifts toward current feelings, feelings fade."""
        if self.emotions:
            weight = sum(e.intensity for e in self.emotions)
            p = sum(e.valence * e.intensity for e in self.emotions) / weight
            a = sum(e.arousal * e.intensity for e in self.emotions) / weight
            k = min(0.25, 0.08 * weight)
            self.pleasure = clamp(self.pleasure + k * (p - self.pleasure), -1, 1)
            self.arousal = clamp(self.arousal + k * (a - self.arousal))
        self.pleasure *= 0.995
        for e in self.emotions:
            e.intensity *= 0.85
        self.emotions = [e for e in self.emotions if e.intensity > 0.05]

    def strongest(self) -> Optional[Emotion]:
        return max(self.emotions, key=lambda e: e.intensity, default=None)


NEGATIVITY_BIAS = 1.8  # betrayal hits harder than kindness heals


@dataclass
class Relationship:
    """Where love and hate live: built from history, not set (plan §3.6)."""

    who: str
    name: str
    trust: float = 0.0
    affection: float = 0.0
    respect: float = 0.0
    familiarity: float = 0.0
    warm: int = 0
    cold: int = 0
    last_tick: int = -1
    history: List[str] = field(default_factory=list)

    def update(self, valence: float, tick: int, note: str, respect: float = 0.0) -> None:
        scale = NEGATIVITY_BIAS if valence < 0 else 1.0
        self.affection = clamp(self.affection + 0.08 * valence * scale, -1, 1)
        self.trust = clamp(self.trust + 0.05 * valence * scale, -1, 1)
        self.respect = clamp(self.respect + respect, -1, 1)
        self.familiarity = clamp(self.familiarity + 0.04 * (1 - self.familiarity))
        if valence > 0.2:
            self.warm += 1
        elif valence < -0.2:
            self.cold += 1
        self.last_tick = tick
        self.history.append(note)
        del self.history[:-20]

    @property
    def stance(self) -> str:
        a = self.affection
        if a > 0.6:
            return "loves"
        if a > 0.3:
            return "likes"
        if a < -0.4:
            return "resents"
        if a < -0.15:
            return "wary of"
        return "knows" if self.familiarity > 0.1 else "barely knows"


@dataclass
class Goal:
    id: str
    text: str
    drive: str
    serves: List[str]  # option keys or action kinds that make progress
    importance: float
    origin: str  # "seed" (a future shard) | "self" (it decided this)
    reason: str
    created_day: int
    progress: float = 0.0
    status: str = "active"  # active | done | abandoned
    last_progress_day: int = 0
    closed_reason: str = ""

    def advances(self, option_key: str, kind: str) -> bool:
        return option_key in self.serves or kind in self.serves
