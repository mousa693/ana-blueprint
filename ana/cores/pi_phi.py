"""
MIT License

Copyright (c) 2026 Mousa (@m_mousa)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Pi-Phi Adaptive Engine
======================

A production-grade adaptive dual-core engine built around reciprocal
prediction, surprise-driven parameter updates, and convergence-based
termination.

Key properties:
- Alpha maintains a learnable predictive model of Omega's expansion policy.
- Omega maintains a learnable predictive model of Alpha's compression policy.
- Both models update from prediction error and parameter drift.
- Termination is based on bounded recent error, bounded parameter drift, and
  non-trivial state stability, rather than arbitrary recursion depth.
- False convergence by collapse is explicitly guarded against via energy-floor
  checks.

This module is intentionally self-contained and standard-library only.
"""

from __future__ import annotations

import logging
import math
import random
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────


PHI = (1.0 + math.sqrt(5.0)) / 2.0
PI = math.pi


# ─────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────


def _safe_stdev(values: Sequence[float]) -> float:
    """Return standard deviation for a sequence, defaulting to 0.0 for size < 2."""
    if len(values) < 2:
        return 0.0
    return statistics.stdev(values)


def _safe_mean(values: Sequence[float], default: float = 0.0) -> float:
    """Return mean for a sequence, defaulting when empty."""
    if not values:
        return default
    return statistics.mean(values)


# ─────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EngineConfig:
    """Runtime configuration for the adaptive engine."""

    max_ticks: int = 100
    initial_state: float = 1.0
    omega_noise_scale: float = 0.08
    omega_candidate_count: int = 5
    omega_learning_rate: float = 0.10
    alpha_learning_rate: float = 0.08
    min_scale: float = 0.5
    max_scale: float = 3.0
    min_damping: float = 0.10
    max_damping: float = 0.95
    alpha_base_damping: float = 0.65
    alpha_cycle_weight: float = 0.35
    alpha_softening_gain: float = 0.05
    alpha_hardening_gain: float = 0.07
    max_softening: float = 0.20
    max_hardening: float = 0.15
    convergence_window: int = 8
    state_window: int = 8
    mean_error_threshold: float = 0.06
    error_stdev_threshold: float = 0.03
    parameter_drift_threshold: float = 0.015
    state_stdev_threshold: float = 0.005
    min_energy_floor: float = 0.10
    random_seed: Optional[int] = 42

    def __post_init__(self) -> None:
        if self.max_ticks <= 0:
            raise ValueError("max_ticks must be positive")
        if self.omega_candidate_count <= 0:
            raise ValueError("omega_candidate_count must be positive")
        if self.convergence_window < 4:
            raise ValueError("convergence_window must be at least 4")
        if self.state_window < 4:
            raise ValueError("state_window must be at least 4")
        if self.min_scale >= self.max_scale:
            raise ValueError("min_scale must be < max_scale")
        if self.min_damping >= self.max_damping:
            raise ValueError("min_damping must be < max_damping")
        if not (0.0 <= self.alpha_base_damping <= 1.0):
            raise ValueError("alpha_base_damping must be in [0, 1]")
        if self.min_energy_floor <= 0.0:
            raise ValueError("min_energy_floor must be > 0")


# ─────────────────────────────────────────────────────────────
# Model states and history records
# ─────────────────────────────────────────────────────────────


@dataclass
class TickRecord:
    """Per-tick engine telemetry."""

    tick: int
    pre_state: float
    expanded: float
    compressed: float
    omega_surprise: float
    alpha_surprise: float
    learned_scale: float
    learned_damping: float
    effective_damping: float
    omega_error_mean: float
    alpha_error_mean: float


@dataclass
class ModelConvergenceSnapshot:
    """Aggregated convergence state for a predictive model."""

    recent_mean_error: float
    recent_error_stdev: float
    recent_parameter_drift: float
    converged: bool


@dataclass
class RunResult:
    """Result bundle returned by the engine."""

    history: List[TickRecord]
    alpha: "AlphaCore"
    omega: "OmegaCore"
    converged: bool
    convergence_tick: Optional[int]


# ─────────────────────────────────────────────────────────────
# Internal predictive models
# ─────────────────────────────────────────────────────────────


@dataclass
class OmegaModel:
    """
    Alpha's internal model of Omega.

    This model estimates Omega as a context-constrained expansion process with
    a dominant multiplicative scale and residual noise. It does not claim to
    fully replicate Omega's candidate-selection policy, but it tracks the most
    important first-order behavior faithfully.
    """

    config: EngineConfig
    scale: float = PHI
    noise_estimate: float = 0.08
    prediction_errors: List[float] = field(default_factory=list)
    scale_history: List[float] = field(default_factory=lambda: [PHI])

    def predict(self, state: float) -> float:
        """Predict Omega's next expansion from the current state."""
        return state * self.scale

    def update(self, state: float, actual_expansion: float) -> float:
        """Update the learned scale and residual noise estimate from observation."""
        predicted = self.predict(state)
        error = abs(actual_expansion - predicted)
        self.prediction_errors.append(error)

        if abs(state) > 1e-12:
            observed_ratio = actual_expansion / state
            delta = observed_ratio - self.scale
            self.scale += self.config.omega_learning_rate * delta
            self.scale = max(self.config.min_scale, min(self.config.max_scale, self.scale))
            self.scale_history.append(self.scale)

        residual = actual_expansion - self.predict(state)
        self.noise_estimate = 0.9 * self.noise_estimate + 0.1 * abs(residual)
        return error

    def convergence_snapshot(self) -> ModelConvergenceSnapshot:
        """Evaluate whether recent errors and parameter drift indicate convergence."""
        window = self.config.convergence_window
        recent_errors = self.prediction_errors[-window:]
        recent_scales = self.scale_history[-window:]

        mean_error = _safe_mean(recent_errors, default=float("inf"))
        error_stdev = _safe_stdev(recent_errors)
        drift = abs(recent_scales[-1] - recent_scales[0]) if len(recent_scales) >= 2 else float("inf")

        converged = (
            len(recent_errors) >= window
            and mean_error <= self.config.mean_error_threshold
            and error_stdev <= self.config.error_stdev_threshold
            and drift <= self.config.parameter_drift_threshold
        )
        return ModelConvergenceSnapshot(mean_error, error_stdev, drift, converged)


@dataclass
class AlphaModel:
    """
    Omega's internal model of Alpha.

    This model estimates Alpha's compression policy by separating the linear
    damping component from the sinusoidal cycle term. That avoids the broken
    inference pattern where damping is polluted by cyclic residuals.
    """

    config: EngineConfig
    damping: float = 0.65
    cycle_weight: float = 0.35
    prediction_errors: List[float] = field(default_factory=list)
    damping_history: List[float] = field(default_factory=lambda: [0.65])

    def predict(self, state: float, *, damping_override: Optional[float] = None) -> float:
        """Predict Alpha's compression output for a given state."""
        damping = self.damping if damping_override is None else damping_override
        return (state * damping) + (math.sin(state * PI) * self.cycle_weight)

    def update(self, state: float, actual_compression: float) -> float:
        """Update damping estimate after removing the known cyclic contribution."""
        predicted = self.predict(state)
        error = abs(actual_compression - predicted)
        self.prediction_errors.append(error)

        if abs(state) > 1e-12:
            cyclic_component = math.sin(state * PI) * self.cycle_weight
            linear_component = actual_compression - cyclic_component
            observed_damping = linear_component / state
            delta = observed_damping - self.damping
            self.damping += self.config.alpha_learning_rate * delta
            self.damping = max(self.config.min_damping, min(self.config.max_damping, self.damping))
            self.damping_history.append(self.damping)

        return error

    def convergence_snapshot(self) -> ModelConvergenceSnapshot:
        """Evaluate whether recent errors and parameter drift indicate convergence."""
        window = self.config.convergence_window
        recent_errors = self.prediction_errors[-window:]
        recent_damping = self.damping_history[-window:]

        mean_error = _safe_mean(recent_errors, default=float("inf"))
        error_stdev = _safe_stdev(recent_errors)
        drift = abs(recent_damping[-1] - recent_damping[0]) if len(recent_damping) >= 2 else float("inf")

        converged = (
            len(recent_errors) >= window
            and mean_error <= self.config.mean_error_threshold
            and error_stdev <= self.config.error_stdev_threshold
            and drift <= self.config.parameter_drift_threshold
        )
        return ModelConvergenceSnapshot(mean_error, error_stdev, drift, converged)


# ─────────────────────────────────────────────────────────────
# Adaptive cores
# ─────────────────────────────────────────────────────────────


class OmegaCore:
    """
    Omega expands state through stochastic exploration.

    Omega is not blind. It scores candidate expansions using its learned model
    of Alpha's compression behavior, preferring candidates that are likely to
    survive compression while preserving meaningful amplitude.
    """

    def __init__(self, config: EngineConfig, rng: random.Random) -> None:
        self.config = config
        self.rng = rng
        self.phi = PHI
        self.alpha_model = AlphaModel(config=config, damping=config.alpha_base_damping, cycle_weight=config.alpha_cycle_weight)

    def expand(self, state: float) -> float:
        """Generate a raw stochastic expansion candidate."""
        return state * self.phi + self.rng.gauss(0.0, self.config.omega_noise_scale)

    def _candidate_score(self, candidate: float) -> float:
        """
        Score a candidate expansion.

        Higher is better. The score rewards candidates that:
        - retain a healthy amount of amplitude after Alpha compression
        - avoid violent mismatch between candidate and predicted compression
        - remain dynamically alive rather than collapsing into triviality
        """
        if abs(candidate) < 1e-12:
            return float("-inf")

        predicted_compression = self.alpha_model.predict(candidate)
        retention = abs(predicted_compression) / max(abs(candidate), 1e-12)
        mismatch = abs(candidate - predicted_compression)
        energy_bonus = min(abs(predicted_compression), 2.0)
        return (1.5 * retention) + (0.25 * energy_bonus) - (0.35 * mismatch)

    def smart_expand(self, state: float) -> float:
        """Sample candidates and return the highest-scoring survivable expansion."""
        candidates = [self.expand(state) for _ in range(self.config.omega_candidate_count)]
        return max(candidates, key=self._candidate_score)

    def update_alpha_model(self, state: float, observed_compression: float) -> float:
        """Update Omega's model of Alpha from an observed compression result."""
        return self.alpha_model.update(state, observed_compression)


class AlphaCore:
    """
    Alpha compresses and regulates state.

    Alpha does not simply damp everything by a fixed amount. It anticipates
    Omega's expansion pressure and can both soften and harden its damping,
    depending on whether the observed dynamic calls for accommodation or
    stronger stabilization.
    """

    def __init__(self, config: EngineConfig) -> None:
        self.config = config
        self.omega_model = OmegaModel(config=config, scale=PHI, noise_estimate=config.omega_noise_scale)

    def compress(self, state: float, damping: Optional[float] = None) -> float:
        """Apply Alpha's compression rule with the chosen damping coefficient."""
        active_damping = self.config.alpha_base_damping if damping is None else damping
        return (state * active_damping) + (math.sin(state * PI) * self.config.alpha_cycle_weight)

    def compute_effective_damping(self, state: float) -> float:
        """
        Adjust damping based on anticipated Omega pressure.

        - If expansion pressure is high, soften slightly to avoid over-collapse.
        - If pressure is low, harden slightly to keep the system from drifting.
        """
        anticipated_expansion = self.omega_model.predict(state)
        expansion_pressure = abs(anticipated_expansion - state)

        softening = min(self.config.max_softening, expansion_pressure * self.config.alpha_softening_gain)
        hardening = min(self.config.max_hardening, max(0.0, 1.0 - expansion_pressure) * self.config.alpha_hardening_gain)

        if expansion_pressure >= 1.0:
            damping = self.config.alpha_base_damping - softening
        else:
            damping = self.config.alpha_base_damping + hardening

        return max(self.config.min_damping, min(self.config.max_damping, damping))

    def anticipate_and_compress(self, state: float) -> Tuple[float, float]:
        """Compute adaptive damping and compress the incoming state."""
        damping = self.compute_effective_damping(state)
        return self.compress(state, damping=damping), damping

    def update_omega_model(self, state: float, observed_expansion: float) -> float:
        """Update Alpha's model of Omega from an observed expansion result."""
        return self.omega_model.update(state, observed_expansion)


# ─────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────


class PiPhiAdaptiveEngine:
    """Main engine orchestrating Omega expansion, Alpha compression, and convergence."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config or EngineConfig()
        self.rng = random.Random(self.config.random_seed)
        self.alpha = AlphaCore(config=self.config)
        self.omega = OmegaCore(config=self.config, rng=self.rng)

    def _state_is_non_trivial(self, history: Sequence[TickRecord]) -> bool:
        """Reject false convergence caused by collapse into near-zero amplitude."""
        recent = history[-self.config.state_window:]
        if len(recent) < self.config.state_window:
            return False
        mean_abs_state = _safe_mean([abs(item.compressed) for item in recent])
        return mean_abs_state >= self.config.min_energy_floor

    def _state_is_stable(self, history: Sequence[TickRecord]) -> bool:
        """Determine whether recent compressed states have stabilized."""
        recent = history[-self.config.state_window:]
        if len(recent) < self.config.state_window:
            return False
        recent_states = [item.compressed for item in recent]
        return _safe_stdev(recent_states) <= self.config.state_stdev_threshold

    def _models_converged(self) -> bool:
        """Determine whether both internal predictive models have converged."""
        omega_snapshot = self.alpha.omega_model.convergence_snapshot()
        alpha_snapshot = self.omega.alpha_model.convergence_snapshot()
        return omega_snapshot.converged and alpha_snapshot.converged

    def run(self) -> RunResult:
        """Execute the engine until convergence or until the configured tick limit."""
        state = self.config.initial_state
        history: List[TickRecord] = []
        convergence_tick: Optional[int] = None

        logger.info("Starting PiPhiAdaptiveEngine with initial_state=%s", state)

        for tick in range(self.config.max_ticks):
            expanded = self.omega.smart_expand(state)
            compressed, effective_damping = self.alpha.anticipate_and_compress(expanded)

            omega_surprise = self.alpha.update_omega_model(state, expanded)
            alpha_surprise = self.omega.update_alpha_model(expanded, compressed)

            omega_snapshot = self.alpha.omega_model.convergence_snapshot()
            alpha_snapshot = self.omega.alpha_model.convergence_snapshot()

            record = TickRecord(
                tick=tick,
                pre_state=state,
                expanded=expanded,
                compressed=compressed,
                omega_surprise=omega_surprise,
                alpha_surprise=alpha_surprise,
                learned_scale=self.alpha.omega_model.scale,
                learned_damping=self.omega.alpha_model.damping,
                effective_damping=effective_damping,
                omega_error_mean=omega_snapshot.recent_mean_error,
                alpha_error_mean=alpha_snapshot.recent_mean_error,
            )
            history.append(record)
            state = compressed

            if self._models_converged() and self._state_is_stable(history) and self._state_is_non_trivial(history):
                convergence_tick = tick
                logger.info(
                    "Engine converged at tick=%s scale=%.5f damping=%.5f state=%.5f",
                    tick,
                    self.alpha.omega_model.scale,
                    self.omega.alpha_model.damping,
                    state,
                )
                break
        else:
            logger.info("Engine reached max_ticks=%s without full convergence", self.config.max_ticks)

        return RunResult(
            history=history,
            alpha=self.alpha,
            omega=self.omega,
            converged=convergence_tick is not None,
            convergence_tick=convergence_tick,
        )


# ─────────────────────────────────────────────────────────────
# Rendering / CLI helpers
# ─────────────────────────────────────────────────────────────


def format_run_summary(result: RunResult) -> str:
    """Return a human-readable summary of the engine run."""
    lines: List[str] = []
    lines.append("Pi-Phi Adaptive Engine")
    lines.append("=" * 88)

    if result.converged:
        lines.append(f"Status: CONVERGED at tick {result.convergence_tick}")
    else:
        lines.append("Status: MAX TICKS reached without full convergence")

    lines.append(f"Alpha learned Omega scale: {result.alpha.omega_model.scale:.5f}")
    lines.append(f"Omega learned Alpha damping: {result.omega.alpha_model.damping:.5f}")
    lines.append("")
    lines.append(
        "Tick | Pre-State | Expanded  | Compressed | Ω-Surprise | α-Surprise | "
        "Ω-scale  | α-damping | eff-damp"
    )
    lines.append("-" * 114)

    for item in result.history:
        lines.append(
            f"{item.tick:4d} | "
            f"{item.pre_state:9.5f} | "
            f"{item.expanded:9.5f} | "
            f"{item.compressed:10.5f} | "
            f"{item.omega_surprise:10.5f} | "
            f"{item.alpha_surprise:10.5f} | "
            f"{item.learned_scale:8.4f} | "
            f"{item.learned_damping:9.4f} | "
            f"{item.effective_damping:8.4f}"
        )

    lines.append("")
    lines.append("Recent model diagnostics")
    lines.append("-" * 88)
    omega_diag = result.alpha.omega_model.convergence_snapshot()
    alpha_diag = result.omega.alpha_model.convergence_snapshot()
    lines.append(
        f"Alpha→Omega model | mean error: {omega_diag.recent_mean_error:.5f} | "
        f"error stdev: {omega_diag.recent_error_stdev:.5f} | "
        f"scale drift: {omega_diag.recent_parameter_drift:.5f} | "
        f"converged: {omega_diag.converged}"
    )
    lines.append(
        f"Omega→Alpha model | mean error: {alpha_diag.recent_mean_error:.5f} | "
        f"error stdev: {alpha_diag.recent_error_stdev:.5f} | "
        f"damping drift: {alpha_diag.recent_parameter_drift:.5f} | "
        f"converged: {alpha_diag.converged}"
    )
    return "\n".join(lines)


def main() -> None:
    """Run the engine with default settings and print a diagnostic summary."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    engine = PiPhiAdaptiveEngine()
    result = engine.run()
    print(format_run_summary(result))


if __name__ == "__main__":
    main()
