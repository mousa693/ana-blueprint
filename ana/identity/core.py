"""Identity Core ("Me") — the digital psyche ANA maintains.

Answers "who is the protagonist right now?" and provides the snapshot the whole
system builds the world from. It holds structured self-state (traits, beliefs,
goals, emotion) and owns the Memory Shard System. Every cycle's Outcome is
folded back in here — this is where learning and adaptation live.

The Core also seeds "memories of the future": goals flagged as future memories
are written as guiding shards the Generative Engine can foreshadow.
"""
from __future__ import annotations

from typing import Optional

from ..contracts import (
    EmotionalState,
    Goal,
    IdentitySnapshot,
    Outcome,
    Trait,
)
from .memory import ShardMemory


class IdentityCore:
    def __init__(self, *, name: str = "the protagonist",
                 memory: Optional[ShardMemory] = None):
        self.name = name
        self.tick = 0
        self.traits: dict[str, Trait] = {}
        self.beliefs: list[str] = []
        self.goals: list[Goal] = []
        self.emotion = EmotionalState()
        self.active_themes: list[str] = []
        self.convergence = 0.0
        self.memory = memory or ShardMemory()

    # -- seeding ---------------------------------------------------------
    def set_trait(self, name: str, value: float, confidence: float = 0.6) -> None:
        self.traits[name] = Trait(name=name, value=max(0.0, min(1.0, value)),
                                  confidence=confidence)

    def add_belief(self, belief: str) -> None:
        self.beliefs.append(belief)
        self.memory.record_identity(f"Belief: {belief}", tag="kind:belief")

    def add_goal(self, description: str, horizon: str = "mid",
                 is_future_memory: bool = False) -> None:
        goal = Goal(description=description, horizon=horizon,
                    is_future_memory=is_future_memory)
        self.goals.append(goal)
        if is_future_memory:
            self.memory.record_future_memory(description, theme=_first_theme(description))

    def seed_memory(self, summary: str, *, themes: list[str] | None = None,
                    symbols: list[str] | None = None) -> None:
        self.memory.record_identity(summary)
        if themes:
            for t in themes:
                if t not in self.active_themes:
                    self.active_themes.append(t)

    # -- snapshot --------------------------------------------------------
    def snapshot(self) -> IdentitySnapshot:
        """The read-only picture handed to the rest of the system each cycle."""
        recalled = self.memory.recall(
            " ".join(self.active_themes) or "who am i", top_k=4
        )
        return IdentitySnapshot(
            tick=self.tick,
            traits=list(self.traits.values()),
            beliefs=list(self.beliefs),
            goals=list(self.goals),
            emotion=self.emotion.model_copy(),
            active_themes=list(self.active_themes),
            recent_shard_summaries=recalled,
            convergence=self.convergence,
        )

    # -- learning --------------------------------------------------------
    def apply_outcome(self, outcome: Outcome) -> None:
        """Fold a cycle's consequences back into the self (the feedback half)."""
        # emotion
        d = outcome.emotion_delta
        self.emotion.valence += d.valence
        self.emotion.arousal += d.arousal
        self.emotion.stress += d.stress
        self.emotion.clamp()

        # traits
        for name, delta in outcome.trait_updates.items():
            cur = self.traits.get(name)
            base = cur.value if cur else 0.5
            self.set_trait(name, base + delta,
                           confidence=min(1.0, (cur.confidence if cur else 0.5) + 0.05))

        # themes
        for theme in outcome.themes_touched:
            if theme not in self.active_themes:
                self.active_themes.append(theme)

        # goal progress (crude: any touched theme nudges matching goals)
        for goal in self.goals:
            if any(t in goal.description.lower() for t in outcome.themes_touched):
                goal.progress = min(1.0, goal.progress + 0.15)

        # memory: record the event, and promote a lesson if one was learned
        self.memory.record_event(
            outcome.summary or "an experience",
            themes=outcome.themes_touched,
            symbols=outcome.symbols,
            emotion=self.emotion.dominant.value,
        )
        if outcome.lesson:
            self.memory.record_lesson(outcome.lesson, themes=outcome.themes_touched)

    def advance(self, convergence: float) -> None:
        self.tick += 1
        self.convergence = convergence

    def close(self) -> None:
        self.memory.close()


def _first_theme(text: str) -> str:
    return text.strip().split()[0].lower() if text.strip() else "self"
