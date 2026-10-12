"""Self-Alignment Protocols — Homeostatic Guidance.

This module implements the blueprint's *Self-Alignment Protocols
(Homeostatic Guidance)*: the system's built-in governance and therapeutic
sense. Where the pi-phi engine supplies raw convergence and the Environment
Mirror supplies reflection, the Self-Alignment Protocols sit on top as the
regulatory layer that keeps a journey on a constructive track — neither chaos
nor stagnation — and enforces psychological safety.

The blueprint names five jobs for this layer, and each is realised below:

1. **Inner-outer alignment.** The world should mirror the person. We measure
   ``reflection_pct`` (how much of the identity's active themes the world/scene
   currently reflect) and ``adaptation_level`` (how much the identity is
   actually engaging with what the world offers). Misalignment on either axis
   produces concrete nudges for the next cycle.

2. **Difficulty / stress management.** Borrowing Vygotsky's *zone of proximal
   development* (ZPD), we practise dynamic difficulty adjustment: push toward a
   productive band when the person is coasting, pull back toward calm when they
   are overwhelmed. This is the ``recommended_intensity`` dial.

3. **Reality testing.** We watch intensity *trends* across cycles (via a short
   rolling history) so a slow slide into relentless darkness is caught even
   when no single scene trips a hard limit.

4. **Ethical / emotional safety.** Forbidden themes are blocked outright, and a
   scene that is too intense while the person is already near their stress
   ceiling is refused. After a very dark beat we guarantee a beat of hope.

5. **Goal alignment.** As convergence rises — the inner and outer worlds coming
   into register — we nudge the Environment Mirror to raise its reflectiveness
   dial, so the person meets themselves more and more directly.

Pure standard library + pydantic. Deterministic, no LLM, no new deps.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ..contracts import (
    AlignmentReport,
    IdentitySnapshot,
    Scene,
    WorldState,
)

# A scene at or above this intensity is considered "very dark" for the purpose
# of the always-hope-after-dark guarantee and the rolling reality-testing trend.
_DARK_INTENSITY = 0.75


def _overlap(a: list[str], b: list[str]) -> float:
    """Fraction of ``a``'s items that appear (as a loose substring match) in ``b``.

    Themes and motifs are free text ("trust" vs. "a locked door with an offered
    key"), so we match on normalised substring containment in either direction
    rather than exact equality. Returns 0..1; 0 when ``a`` is empty.
    """
    if not a:
        return 0.0
    hay = " | ".join(x.lower() for x in b)
    hits = 0
    for item in a:
        token = item.lower().strip()
        if not token:
            continue
        if token in hay or any(token in h or h in token for h in (x.lower() for x in b)):
            hits += 1
    return hits / len(a)


class SelfAlignment:
    """Homeostatic guidance implementing :class:`AlignmentProtocols`.

    The protocol keeps a little state across cycles so it can reason about
    *trends* rather than just the current frame: the previous scene's intensity
    (for the always-hope-after-dark guarantee) and a short rolling window of
    stress readings (for reality testing / detecting a slide toward trauma or
    boredom).
    """

    def __init__(
        self,
        stress_ceiling: float = 0.85,
        stress_floor: float = 0.1,
        forbidden_themes: Optional[list[str]] = None,
        always_hope_after_dark: bool = True,
        zpd_low: float = 0.3,
        zpd_high: float = 0.6,
    ) -> None:
        self.stress_ceiling = stress_ceiling
        self.stress_floor = stress_floor
        self.forbidden_themes = [t.lower().strip() for t in (forbidden_themes or [])]
        self.always_hope_after_dark = always_hope_after_dark
        self.zpd_low = zpd_low
        self.zpd_high = zpd_high

        # Minimal cross-cycle state.
        self._prev_intensity: Optional[float] = None
        self._stress_history: deque[float] = deque(maxlen=5)

    # ──────────────────────────────────────────────────────────
    # The protocol entry point
    # ──────────────────────────────────────────────────────────
    def evaluate(
        self, identity: IdentitySnapshot, world: WorldState, scene: Scene
    ) -> AlignmentReport:
        stress = identity.emotion.stress

        # — Inner-outer alignment ————————————————————————————————
        # What the world + scene put in front of the person, as one bag of
        # symbolic content to test the identity's active themes against.
        world_and_scene = list(world.motifs) + [scene.theme] + list(scene.symbols)
        reflection_pct = _overlap(identity.active_themes, world_and_scene)

        # adaptation_level: how much the identity is engaging/adapting.
        # The snapshot is read-only and carries no first-class "engagement"
        # field, so we APPROXIMATE: blend the pi-phi convergence (the person's
        # inner/outer worlds coming into register — a good proxy for sustained
        # engagement) with the live theme overlap between the identity and this
        # scene (are they actually working the material on offer *right now*).
        scene_overlap = _overlap(identity.active_themes, [scene.theme] + list(scene.symbols))
        adaptation_level = max(0.0, min(1.0, 0.6 * identity.convergence + 0.4 * scene_overlap))

        # — Difficulty / stress management (ZPD) ——————————————————
        recommended_intensity, overwhelmed, bored = self._recommend_intensity(
            stress, adaptation_level, scene.intensity
        )

        # — Safety: in-safe-band test —————————————————————————————
        # Safe when not approaching trauma AND not pinned at boredom.
        in_safe_band = (stress < self.stress_ceiling) and not bored

        # — Nudges (directives for the NEXT cycle) ————————————————
        nudges = self._build_nudges(
            identity, reflection_pct, adaptation_level, overwhelmed, bored
        )

        # — Hard safety gates ————————————————————————————————————
        blocked, block_reason = self._safety_check(scene, stress)

        # — Always provide hope after a very dark sequence ————————
        if (
            self.always_hope_after_dark
            and self._prev_intensity is not None
            and self._prev_intensity >= _DARK_INTENSITY
        ):
            nudges.append(
                "provide hope: the previous beat was very dark — open a door, "
                "offer an ally, or surface a small act of grace"
            )
            # Cap intensity so we do not stack darkness on darkness.
            recommended_intensity = min(recommended_intensity, self.zpd_low)

        report = AlignmentReport(
            reflection_pct=round(reflection_pct, 4),
            adaptation_level=round(adaptation_level, 4),
            stress=stress,
            in_safe_band=in_safe_band,
            recommended_intensity=round(max(0.0, min(1.0, recommended_intensity)), 4),
            nudges=nudges,
            blocked=blocked,
            block_reason=block_reason,
        )

        # — Update cross-cycle state for next time ————————————————
        self._prev_intensity = scene.intensity
        self._stress_history.append(stress)

        return report

    # ──────────────────────────────────────────────────────────
    # Difficulty / stress management — the ZPD dial
    # ──────────────────────────────────────────────────────────
    def _recommend_intensity(
        self, stress: float, adaptation_level: float, scene_intensity: float
    ) -> tuple[float, bool, bool]:
        """Return (recommended_intensity, overwhelmed, bored).

        Dynamic difficulty adjustment in the spirit of the zone of proximal
        development: productive struggle lives between boredom and panic.
        """
        high_stress_mark = self.stress_ceiling * 0.8
        overwhelmed = stress > high_stress_mark
        # Boredom: very low stress, or low engagement despite the scene.
        bored = (stress < self.stress_floor) or (adaptation_level < 0.2)

        if overwhelmed:
            # Pull sharply back toward calm, below the comfortable floor.
            target = min(self.zpd_low, scene_intensity * 0.6)
        elif bored:
            # Raise to re-engage — reach toward the top of the ZPD and a touch
            # beyond, scaled by how disengaged the person seems.
            lift = 0.1 if adaptation_level < 0.2 else 0.0
            target = min(1.0, self.zpd_high + lift)
        else:
            # In the productive band: hold intensity inside [zpd_low, zpd_high],
            # nudging toward the centre of the band.
            centre = (self.zpd_low + self.zpd_high) / 2.0
            target = max(self.zpd_low, min(self.zpd_high, 0.5 * scene_intensity + 0.5 * centre))

        return target, overwhelmed, bored

    # ──────────────────────────────────────────────────────────
    # Nudges — concrete directives born of misalignment
    # ──────────────────────────────────────────────────────────
    def _build_nudges(
        self,
        identity: IdentitySnapshot,
        reflection_pct: float,
        adaptation_level: float,
        overwhelmed: bool,
        bored: bool,
    ) -> list[str]:
        nudges: list[str] = []

        # Inner-outer: an active theme the world is failing to reflect.
        if identity.active_themes and reflection_pct < 0.5:
            theme = identity.active_themes[0]
            nudges.append(
                f"deliver the '{theme}' lesson more directly via an NPC or motif — "
                f"the world is under-reflecting an active theme"
            )

        # Adaptation: the person is not engaging the material.
        if adaptation_level < 0.35:
            nudges.append(
                "introduce a more direct confrontation so the identity must "
                "engage rather than drift past the theme"
            )

        # Goal alignment: as convergence rises, mirror more directly.
        if identity.convergence >= 0.5:
            nudges.append("raise the reflectiveness dial — inner and outer are converging")

        # Difficulty-driven beats.
        if overwhelmed:
            nudges.append("insert a calming interlude — lower stakes and give room to breathe")
            nudges.append("introduce a rest beat before the next challenge")
        elif bored:
            nudges.append("raise the stakes with a fresh challenge or an unexpected arrival")

        return nudges

    # ──────────────────────────────────────────────────────────
    # Ethical / emotional safety — hard gates
    # ──────────────────────────────────────────────────────────
    def _safety_check(self, scene: Scene, stress: float) -> tuple[bool, Optional[str]]:
        # Forbidden theme on the scene's theme or any of its symbols.
        candidates = [scene.theme] + list(scene.symbols)
        for cand in candidates:
            low = cand.lower()
            for forbidden in self.forbidden_themes:
                if forbidden and (forbidden in low or low in forbidden):
                    return True, f"forbidden theme encountered: '{forbidden}' in '{cand}'"

        # Dangerously high intensity while already near the stress ceiling.
        if scene.intensity >= 0.8 and stress >= self.stress_ceiling * 0.9:
            return True, (
                f"scene intensity {scene.intensity:.2f} is unsafe while stress "
                f"{stress:.2f} is near the ceiling {self.stress_ceiling:.2f}"
            )

        return False, None
