"""Convergence dynamics — the pi-phi engine as ANA's inner/outer polarity.

The blueprint's central idea is that inner self and outer world start divergent
and *converge* over the journey — but only "as the user engages and learns ...
the user recognizes the world as reflecting themselves". Convergence is earned,
not a clock. The author's pi-phi engine supplies the dynamics: Omega expands,
Alpha compresses, each keeps a predictive model of the other, and the pair
converge when those models stop being surprised by each other.

Mapping
-------
    Omega (expansion)   = the environment / generative novelty pressure ("Myself")
    Alpha (compression) = the identity integrating experience ("Me")
    m_t                 = mutual-prediction reading of the pi-phi cores (0..1)
    c_t                 = convergence, how far Me and Myself are in register (0..1)

Two entry points
----------------
``step(novelty)`` is the original, signal-free driver (kept byte-for-byte in
behaviour): convergence eases toward m_t alone.

``step_with(signal, novelty)`` is the *earned* driver. Each cycle it receives an
:class:`AlignmentSignal` — reflection r in [0,1], engagement e in [-1,1],
integration i in [0,1], external surprise x in [0,1] — and:

1. **Couples the signal into the pi-phi state.** The self/world disagreement

       d_t = K_MIS * (1 - r) + K_AVOID * max(0, -e)          (+ K_SHOCK_STATE * x)

   is injected as noise into what Me *observes* of the world's expansion
   (``expanded + d_t * N(0,1)``), and avoidance additionally scrambles Me's
   compression as the world sees it (``compressed + K_AVOID * max(0,-e) * N(0,1)``).
   So both cores' prediction error — and therefore surprise — rises when the
   self and world disagree, and m_t falls:

       m_t = 1 / (1 + err_t / error_scale)

2. **Accumulates earned evidence.**

       mu_t = clip(A_R * r + A_E * e + A_I * i, -1, 1)        (instant merit)
       M_t  = M_{t-1} + LAMBDA_M * (mu_t - M_{t-1})           (recent merit, EMA)
       I_t  = I_{t-1} + KAPPA * i * (1 - I_{t-1})
                      - DELTA * max(0, -e) * I_{t-1}          (integrated growth)

   I_t is a slow, saturating store of lessons learned — it is what makes
   convergence durable — while avoidance slowly erodes it.

3. **Sets the target as a blend of pi-phi and earned signals.**

       E_t = clip(W_M * M_t + W_I * I_t, 0, 1)
       T_t = m_t ** GAMMA * E_t

   The product means *both* must hold: the cores must predict each other AND
   the self must have earned recognition. Neither alone closes the polarity.

4. **Integrates asymmetrically, with pull-back and shock.**

       c_t = c_{t-1} + k * (T_t - c_{t-1}),  k = K_UP if T_t > c_{t-1} else K_DOWN
       c_t -= BETA * max(0, -e) * c_t                        (avoidance pulls back)
       c_t -= shock_gain * max(0, s_t - b_t) + X_DIP * x * c_t  (shock dip)
       c_t  = clip(c_t, 0, 1),  c_0 = 0

   where s_t is mean pi-phi surprise and b_t its running baseline. A shock does
   not touch M or I much, so T stays put and c recovers over a few cycles.

Phases (``phase_of``): divergent < 0.15 <= awakening < 0.35 <= engaging < 0.55
<= integrating < 0.75 <= converged.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from ..cores.pi_phi import AlphaCore, EngineConfig, OmegaCore

# ── Earned-convergence constants (see module docstring) ──────────────────
K_MIS = 0.12          # world/self mismatch noise injected into Omega's expansion
K_AVOID = 0.18        # avoidance noise on both observations
K_SHOCK_STATE = 0.4   # external shock noise on the shared state
A_R, A_E, A_I = 0.30, 0.35, 0.35   # instant-merit weights
LAMBDA_M = 0.35       # recent-merit EMA rate
KAPPA = 0.25          # integration accumulation rate
DELTA = 0.05          # erosion of integration under avoidance
W_M, W_I = 0.45, 0.55 # blend of recent merit vs integrated growth
GAMMA = 0.35           # softness of the pi-phi gate on the target
K_UP = 0.22           # integrator rate toward a higher target
K_DOWN = 0.30         # integrator rate toward a lower target (losses are faster)
BETA = 0.06           # direct pull-back per unit of avoidance
X_DIP = 0.30          # proportional dip per unit of external surprise
EARNED_SHOCK_GAIN = 0.15  # shock gain for pi-phi surprise spikes in step_with

PHASES = ("divergent", "awakening", "engaging", "integrating", "converged")
_PHASE_EDGES = (0.15, 0.35, 0.55, 0.75)


def phase_of(convergence: float) -> str:
    """Name the stage of the inner/outer polarity for a convergence level."""
    for edge, name in zip(_PHASE_EDGES, PHASES):
        if convergence < edge:
            return name
    return PHASES[-1]


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class AlignmentSignal:
    """What the cycle *earned*, as read by the Self-Alignment Protocols.

    reflection        0..1  how much of the self the world/scene reflected
    engagement       -1..1  met the challenge (+1), neutral (0), avoided (-1)
    integration       0..1  a lesson was learned / the identity actually changed
    surprise_external 0..1  vision or entropy shock from outside the self
    """

    reflection: float = 0.5
    engagement: float = 0.0
    integration: float = 0.0
    surprise_external: float = 0.0

    def clamped(self) -> "AlignmentSignal":
        return AlignmentSignal(
            reflection=_clip(self.reflection),
            engagement=_clip(self.engagement, -1.0, 1.0),
            integration=_clip(self.integration),
            surprise_external=_clip(self.surprise_external),
        )


@dataclass
class StepResult:
    convergence: float       # 0 divergent .. 1 fully mirrored
    omega_surprise: float    # how surprised "Me" was by the world's expansion
    alpha_surprise: float    # how surprised the world was by "Me"'s integration
    state: float
    phase: str = "divergent"     # phase_of(convergence)
    target: float = 0.0          # the integrator's target this cycle
    mutual_prediction: float = 0.0  # m_t, the pi-phi reading
    earned: float = 0.0          # E_t, the earned-recognition reading (step_with only)


class ConvergenceEngine:
    """A single-step driver over the pi-phi Alpha/Omega cores."""

    def __init__(self, seed: int = 42, error_scale: float = 0.08,
                 ease_rate: float = 0.14, shock_gain: float = 0.6):
        self.config = EngineConfig(random_seed=seed)
        self.rng = random.Random(seed)
        # A separate stream for signal coupling so step() stays reproducible.
        self._couple_rng = random.Random(seed * 7919 + 1)
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
        # Earned-convergence memory (step_with).
        self.merit = 0.0          # M_t
        self.integrated = 0.0     # I_t
        self.last_signal: AlignmentSignal | None = None

    # ------------------------------------------------------------------
    # Original, signal-free driver (backward compatible)
    # ------------------------------------------------------------------
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

        target = self._mutual_prediction()
        conv = self._update_convergence(omega_surprise, alpha_surprise)
        return StepResult(
            convergence=conv,
            omega_surprise=omega_surprise,
            alpha_surprise=alpha_surprise,
            state=self.state,
            phase=phase_of(conv),
            target=target,
            mutual_prediction=target,
        )

    def _mutual_prediction(self) -> float:
        """m_t: how well Me and Myself currently predict each other (0..1)."""
        o = self.alpha.omega_model.convergence_snapshot().recent_mean_error
        a = self.omega.alpha_model.convergence_snapshot().recent_mean_error
        if o == float("inf") or a == float("inf"):
            return 0.0
        avg_err = (o + a) / 2.0
        return 1.0 / (1.0 + avg_err / max(1e-6, self.error_scale))

    def _update_convergence(self, omega_surprise: float, alpha_surprise: float) -> float:
        """Integrate this cycle's signal into the running convergence level.

        The instantaneous *target* is how well Me and Myself currently predict
        each other (low recent error -> target near 1). Convergence eases toward
        that target, so the polarity closes gradually over a journey rather than
        snapping shut. A surprise spike above the running baseline knocks it back
        down — turbulent experiences transiently re-open the inner/outer gap.
        """
        target = self._mutual_prediction()

        # Ease toward the target (the gradual polarity-convergence arc).
        self.conv += self.ease_rate * (target - self.conv)

        # Shock: surprise above the running baseline temporarily reduces convergence.
        surprise = (omega_surprise + alpha_surprise) / 2.0
        shock = max(0.0, surprise - self._surprise_baseline)
        self.conv -= self.shock_gain * shock
        self._surprise_baseline += 0.1 * (surprise - self._surprise_baseline)

        self.conv = max(0.0, min(1.0, self.conv))
        return self.conv

    # ------------------------------------------------------------------
    # Earned driver
    # ------------------------------------------------------------------
    def step_with(self, signal: AlignmentSignal,
                  novelty_pressure: float = 0.0) -> StepResult:
        """Advance the polarity by one cycle, driven by what the cycle earned.

        See the module docstring for the full model. In short: self/world
        mismatch and avoidance are injected into the pi-phi observations (so the
        cores' surprise rises when Me and Myself disagree), and convergence eases
        toward ``m_t ** GAMMA * E_t`` — the pi-phi mutual-prediction reading
        gated by earned recognition — with avoidance pulling back and external
        shocks producing a temporary dip.
        """
        sig = signal.clamped()
        self.last_signal = sig
        avoid = max(0.0, -sig.engagement)
        g = self._couple_rng.gauss

        # 1. Couple the signal into the shared pi-phi state.
        perturbed = self.state + novelty_pressure * self.rng.uniform(-0.5, 0.5)
        perturbed += K_SHOCK_STATE * sig.surprise_external * g(0.0, 1.0)

        expanded = self.omega.smart_expand(perturbed)
        disagreement = K_MIS * (1.0 - sig.reflection) + K_AVOID * avoid
        observed_expansion = expanded + disagreement * g(0.0, 1.0)

        compressed, _damp = self.alpha.anticipate_and_compress(expanded)
        observed_compression = compressed + K_AVOID * avoid * g(0.0, 1.0)

        omega_surprise = self.alpha.update_omega_model(perturbed, observed_expansion)
        alpha_surprise = self.omega.update_alpha_model(expanded, observed_compression)

        self.state = compressed
        m = self._mutual_prediction()

        # 2. Accumulate earned evidence.
        mu = _clip(A_R * sig.reflection + A_E * sig.engagement
                   + A_I * sig.integration, -1.0, 1.0)
        self.merit += LAMBDA_M * (mu - self.merit)
        self.integrated += KAPPA * sig.integration * (1.0 - self.integrated)
        self.integrated -= DELTA * avoid * self.integrated
        self.integrated = _clip(self.integrated)

        # 3. Target: pi-phi reading gated by earned recognition.
        earned = _clip(W_M * self.merit + W_I * self.integrated)
        target = (m ** GAMMA) * earned

        # 4. Asymmetric integrator, avoidance pull-back, shock dip.
        k = K_UP if target > self.conv else K_DOWN
        self.conv += k * (target - self.conv)
        self.conv -= BETA * avoid * self.conv

        surprise = (omega_surprise + alpha_surprise) / 2.0
        spike = max(0.0, surprise - self._surprise_baseline)
        self.conv -= EARNED_SHOCK_GAIN * spike + X_DIP * sig.surprise_external * self.conv
        self._surprise_baseline += 0.1 * (surprise - self._surprise_baseline)

        self.conv = _clip(self.conv)
        return StepResult(
            convergence=self.conv,
            omega_surprise=omega_surprise,
            alpha_surprise=alpha_surprise,
            state=self.state,
            phase=phase_of(self.conv),
            target=target,
            mutual_prediction=m,
            earned=earned,
        )
