"""
Temperament: the twin's developmental metabolism (plan §3.13).

Built on the same principle as the Pi-Phi engine (``pi_phi.py``): an
expanding force (Omega, explore) and a compressing force (Alpha, consolidate)
regulated by prediction error, with a guard against false convergence by
collapse.

    surprise high   → ω rises: try new things, doubt old expectations
    surprise low    → α rises: deepen, build habits, avoid risk
    settled too long while starved of novelty → restlessness (the energy floor)

Pi-Phi itself still runs on a single scalar state.  Generalizing it to the
per-domain surprise vector (social, work, self) is Phase 3; this class is the
interface that generalization will sit behind.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Temperament:
    omega: float = 0.5  # exploration temperature
    alpha: float = 0.5  # caution / consolidation
    surprise: float = 0.2  # running average of prediction error
    settled_ticks: int = 0
    restless_until: int = -1
    learning_rate: float = 0.015
    surprise_set_point: float = 0.18
    floor_ticks: int = 96  # two world days settled before the floor can fire

    def observe(self, prediction_error: float, novelty_level: float, tick: int) -> None:
        self.surprise = 0.9 * self.surprise + 0.1 * prediction_error
        gap = self.surprise - self.surprise_set_point
        self.omega = _clamp(self.omega + self.learning_rate * gap)
        self.alpha = _clamp(self.alpha - self.learning_rate * gap)
        self.settled_ticks = self.settled_ticks + 1 if gap < 0 else 0
        # Energy floor: a life that has gone flat pushes back.
        if self.settled_ticks > self.floor_ticks and novelty_level < 0.35:
            self.omega = _clamp(self.omega + 0.15)
            self.alpha = _clamp(self.alpha - 0.1)
            self.settled_ticks = 0
            self.restless_until = tick + 48

    def phase(self, tick: int) -> str:
        if tick < self.restless_until:
            return "restless"
        if self.omega > self.alpha + 0.15:
            return "exploring"
        if self.alpha > self.omega + 0.15:
            return "settling"
        return "balanced"


def _clamp(x: float) -> float:
    return 0.1 if x < 0.1 else 1.0 if x > 1.0 else x
