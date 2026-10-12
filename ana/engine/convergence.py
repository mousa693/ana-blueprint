"""Convergence dynamics — the pi-phi engine as ANA's inner/outer polarity.

The blueprint's central idea is that inner self and outer world start divergent
and *converge* over the journey. The author's pi-phi engine already models a
reciprocal system that converges: Omega expands, Alpha compresses, each keeps a
predictive model of the other, and convergence is reached when those models stop
being surprised by each other.

We map that directly:
    Omega (expansion)   = the environment / generative novelty pressure ("Myself")
    Alpha (compression) = the identity integrating experience ("Me")
    convergence          = how well Me and Myself now predict each other (0..1)

Instead of running pi-phi's own loop to completion, we step it once per ANA
cycle and read a convergence scalar. Scene intensity perturbs the shared state,
so turbulent experiences transiently push inner and outer apart (as they should)
before integration pulls them back together.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..cores.pi_phi import AlphaCore, EngineConfig, OmegaCore


@dataclass
class StepResult:
    convergence: float       # 0 divergent .. 1 fully mirrored
    omega_surprise: float    # how surprised "Me" was by the world's expansion
    alpha_surprise: float    # how surprised the world was by "Me"'s integration
    state: float


class ConvergenceEngine:
    """A single-step driver over the pi-phi Alpha/Omega cores."""

    def __init__(self, seed: int = 42, error_scale: float = 0.08,
                 ease_rate: float = 0.14, shock_gain: float = 0.6):
        import random

        self.config = EngineConfig(random_seed=seed)
        self.rng = random.Random(seed)
        self.alpha = AlphaCore(config=self.config)
        self.omega = OmegaCore(config=self.config, rng=self.rng)
        self.state = self.config.initial_state
        # Normalises recent prediction error into a 0..1 mutual-prediction target.
        self.error_scale = error_scale
        # Convergence is a slow integrator: it starts fully divergent and eases
        # toward the target as Me and Myself learn each other, dipping when a
        # turbulent cycle spikes surprise.
        self.conv = 0.0
        self.ease_rate = ease_rate
        self.shock_gain = shock_gain
        self._surprise_baseline = 0.1

    def step(self, novelty_pressure: float = 0.0) -> StepResult:
        """Advance the polarity by one cycle.

        `novelty_pressure` (0..1, from scene intensity + entropy) perturbs the
        shared state so a jolting experience shows up as a spike in surprise and
        a temporary dip in convergence.
        """
        # Perturb the shared state with environmental novelty before expansion.
        perturbed = self.state + novelty_pressure * self.rng.uniform(-0.5, 0.5)

        expanded = self.omega.smart_expand(perturbed)
        compressed, _damp = self.alpha.anticipate_and_compress(expanded)

        omega_surprise = self.alpha.update_omega_model(perturbed, expanded)
        alpha_surprise = self.omega.update_alpha_model(expanded, compressed)

        self.state = compressed

        conv = self._update_convergence(omega_surprise, alpha_surprise)
        return StepResult(
            convergence=conv,
            omega_surprise=omega_surprise,
            alpha_surprise=alpha_surprise,
            state=self.state,
        )

    def _update_convergence(self, omega_surprise: float, alpha_surprise: float) -> float:
        """Integrate this cycle's signal into the running convergence level.

        The instantaneous *target* is how well Me and Myself currently predict
        each other (low recent error -> target near 1). Convergence eases toward
        that target, so the polarity closes gradually over a journey rather than
        snapping shut. A surprise spike above the running baseline knocks it back
        down — turbulent experiences transiently re-open the inner/outer gap.
        """
        o = self.alpha.omega_model.convergence_snapshot().recent_mean_error
        a = self.omega.alpha_model.convergence_snapshot().recent_mean_error
        if o == float("inf") or a == float("inf"):
            target = 0.0
        else:
            avg_err = (o + a) / 2.0
            target = 1.0 / (1.0 + avg_err / max(1e-6, self.error_scale))

        # Ease toward the target (the gradual polarity-convergence arc).
        self.conv += self.ease_rate * (target - self.conv)

        # Shock: surprise above the running baseline temporarily reduces convergence.
        surprise = (omega_surprise + alpha_surprise) / 2.0
        shock = max(0.0, surprise - self._surprise_baseline)
        self.conv -= self.shock_gain * shock
        self._surprise_baseline += 0.1 * (surprise - self._surprise_baseline)

        self.conv = max(0.0, min(1.0, self.conv))
        return self.conv
