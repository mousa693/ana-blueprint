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

Escalating guidance
-------------------
Guidance is not a fixed string. Each active theme the world fails to reflect
climbs a *nudge ladder* of increasingly direct strategies, one rung per cycle
it is addressed (wrapping round at the top), so the same nudge is never issued
on two consecutive cycles:

    0 motif         weave the theme into a recurring motif / symbol of the setting
    1 npc           deliver the theme through an NPC who embodies it
    2 confrontation stage a direct confrontation the identity must face
    3 pause         offer a reflective pause: a quiet choice to name the theme
    4 dial          raise the reflectiveness dial — mirror the theme openly

A theme stops being nudged (and its ladder resets) once a scene reflects it.
When several themes are under-reflected, the one addressed least recently is
chosen, so attention rotates across them.

A theme counts as *reflected* when it appears (case-insensitive substring) in
the world's motifs, the scene theme, the scene symbols, or any beat's text.

Earned signal
-------------
:meth:`SelfAlignment.signal_for` turns a cycle (identity, world, scene,
outcome) into an :class:`~ana.engine.convergence.AlignmentSignal` that the
Feedback Loop Engine feeds to ``ConvergenceEngine.step_with`` so convergence is
earned by reflection, engagement and integration rather than elapsed time.

Pure standard library + pydantic. Deterministic, no LLM, no new deps.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Iterable, Optional

from ..contracts import (
    AlignmentReport,
    IdentitySnapshot,
    Outcome,
    Scene,
    WorldState,
)
from ..engine.convergence import AlignmentSignal

# A scene at or above this intensity is considered "very dark" for the purpose
# of the always-hope-after-dark guarantee and the rolling reality-testing trend.
_DARK_INTENSITY = 0.75

# The nudge ladder: increasingly direct strategies for an under-reflected theme.
# Each entry is (strategy name, template). Rung 4 deliberately begins with the
# phrase the Feedback Loop Engine listens for to raise the reflectiveness dial.
NUDGE_LADDER: tuple[tuple[str, str], ...] = (
    ("motif",
     "reflect '{theme}' through a recurring motif — let a symbol of {theme} "
     "appear in the setting without naming it"),
    ("npc",
     "deliver the '{theme}' lesson via an NPC who embodies it — someone whose "
     "need or example puts {theme} in front of the identity"),
    ("confrontation",
     "stage a direct confrontation over '{theme}' — the identity must face it "
     "and cannot drift past"),
    ("pause",
     "offer a reflective pause on '{theme}' — a quiet moment and a choice in "
     "which the identity can name what {theme} means to them"),
    ("dial",
     "raise the reflectiveness dial for '{theme}' — let the world mirror it "
     "openly, inner and outer in plain view"),
)

# Rotating phrasings for the generic disengagement nudge so it, too, varies.
_ENGAGE_VARIANTS: tuple[str, ...] = (
    "introduce a more direct confrontation so the identity must engage rather "
    "than drift past the theme",
    "put something the identity cares about at stake so drifting has a cost",
    "have an ally ask the identity, plainly, what they intend to do",
)

# Rotating phrasings for the boredom nudge.
_STAKES_VARIANTS: tuple[str, ...] = (
    "raise the stakes with a fresh challenge or an unexpected arrival",
    "let the world move on its own — a rival acts, a deadline lands",
    "reveal a hidden cost of standing still",
)

_AVOIDANT_WORDS = (
    "run", "flee", "hide", "ignore", "refuse", "leave", "avoid", "give up",
    "abandon", "turn away", "walk away", "back away", "skip",
)


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


def _theme_reflected(theme: str, tags: list[str], texts: list[str]) -> bool:
    """Is ``theme`` present in the symbolic tags or the prose of the scene?

    Short tags (motifs, scene theme, symbols) match by containment in either
    direction; long texts (beat prose) match when the theme is a substring.
    """
    token = theme.lower().strip()
    if not token:
        return False
    for tag in tags:
        t = tag.lower().strip()
        if t and (token in t or t in token):
            return True
    return any(token in txt.lower() for txt in texts)


# ──────────────────────────────────────────────────────────
# Forbidden-theme matching (shared by the loop, generator, NPC layer, harmonizer)
# ──────────────────────────────────────────────────────────

def forbidden_terms(forbidden: Optional[Iterable[str]]) -> list[str]:
    """Normalise a forbidden-theme list: lower-case, ``_``/``-`` as spaces,
    blanks dropped, order kept, duplicates removed."""
    out: list[str] = []
    for f in forbidden or []:
        t = " ".join(str(f or "").lower().replace("_", " ").replace("-", " ").split())
        if t and t not in out:
            out.append(t)
    return out


def mentions_forbidden(
    text: Optional[str], forbidden: Optional[Iterable[str]], *, synonyms: bool = False
) -> Optional[str]:
    """The first forbidden theme ``text`` mentions, else ``None``.

    Matching is case-insensitive and word-anchored: the forbidden word must
    start a word (so ``courage`` hits "Courage", "courageous" and
    "leadership_confidence" hits ``leadership``, but ``courage`` does not hit
    "encourage" and ``rest`` does not hit "interest"). With ``synonyms`` the
    identity lexicon (``ana.identity.core.canonical_themes``) is consulted too,
    so "become someone who can lead" counts as ``leadership``. The prose gate
    uses the word match; the loop's safe view uses ``synonyms=True`` to be
    conservative about what it feeds the generator.
    """
    terms = forbidden_terms(forbidden)
    if not text or not terms:
        return None
    low = " ".join(str(text).lower().replace("_", " ").replace("-", " ").split())
    for f in terms:
        if re.search(rf"(?<![a-z0-9]){re.escape(f)}", low):
            return f
    if synonyms:
        from ..identity.core import canonical_themes  # read-only, lazy (no cycle)
        canon = canonical_themes(str(text))
        for f in terms:
            if f in canon:
                return f
    return None


def _tag_hits_forbidden(tag: Optional[str], forbidden: Iterable[str]) -> Optional[str]:
    """Symbolic-tag match (theme / symbols): containment either way, the same
    rule the eval's safety metric uses — but never on an empty tag (the old
    ``"" in forbidden`` was always true)."""
    low = (tag or "").lower().replace("_", " ").strip().lstrip(ENTROPY_MARK).strip()
    if not low:
        return None
    for f in forbidden:
        if f and (f in low or (len(low) >= 3 and low in f)):
            return f
    return None


def scene_texts(scene: Scene) -> list[tuple[str, str]]:
    """Every player-facing string of a scene as (field, text) pairs: title,
    summary, theme, symbols, choices and each beat's text, speaker, symbol."""
    out: list[tuple[str, str]] = [
        ("title", scene.title or ""), ("summary", scene.summary or ""),
        ("theme", scene.theme or ""),
    ]
    out += [("symbol", s or "") for s in scene.symbols]
    out += [("choice", c or "") for c in scene.choices]
    for i, b in enumerate(scene.beats):
        out.append((f"beat[{i}].text", b.text or ""))
        if b.speaker:
            out.append((f"beat[{i}].speaker", b.speaker))
        if b.symbol:
            out.append((f"beat[{i}].symbol", b.symbol))
    return out


def scene_forbidden_hit(scene: Scene, forbidden: Iterable[str]) -> Optional[tuple[str, str, str]]:
    """First (forbidden theme, field, text) a scene leaks, else ``None``."""
    terms = forbidden_terms(forbidden)
    if not terms:
        return None
    for field_name, text in scene_texts(scene):
        if not text:
            continue
        tag = field_name in ("theme", "symbol") or field_name.endswith(".symbol")
        hit = _tag_hits_forbidden(text, terms) if tag else None
        hit = hit or mentions_forbidden(text, terms)
        if hit:
            return hit, field_name, text
    return None


# Entropy beats from the NPC layer carry this prefix on ``SceneBeat.symbol``
# (e.g. ``"~storm"``) so an unplanned shock is never confused with the
# generator's own closing stage event. Mirrors ``ana.npc.layer.ENTROPY_MARK``.
ENTROPY_MARK = "~"


def is_entropy_beat(beat) -> bool:
    return bool(beat.symbol) and str(beat.symbol).startswith(ENTROPY_MARK)


def has_entropy_shock(scene: Scene) -> bool:
    """Did an unplanned NPC-layer entropy event land in this scene?

    True when any beat carries the entropy mark. For hand-built scenes and
    third-party NPC layers that do not mark their beats, a closing ``event``
    beat with *no* symbol still counts (the deterministic generator always
    tags its own stage event with the stage symbol, and LLM scenes are
    excluded) — so the generator's own ending is never mistaken for a shock.
    """
    if any(is_entropy_beat(b) for b in scene.beats):
        return True
    if scene.beats and scene.source != "llm":
        last = scene.beats[-1]
        return last.kind == "event" and not last.symbol
    return False


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class SelfAlignment:
    """Homeostatic guidance implementing :class:`AlignmentProtocols`.

    The protocol keeps a little state across cycles so it can reason about
    *trends* rather than just the current frame: the previous scene's intensity
    (for the always-hope-after-dark guarantee), a short rolling window of
    stress readings (for reality testing / detecting a slide toward trauma or
    boredom), and a per-theme memory of the nudges already issued (so guidance
    escalates instead of repeating).
    """

    def __init__(
        self,
        stress_ceiling: float = 0.85,
        stress_floor: float = 0.1,
        forbidden_themes: Optional[list[str]] = None,
        always_hope_after_dark: bool = True,
        zpd_low: float = 0.3,
        zpd_high: float = 0.6,
        reflection_target: float = 0.75,
    ) -> None:
        self.stress_ceiling = stress_ceiling
        self.stress_floor = stress_floor
        self.forbidden_themes = forbidden_terms(forbidden_themes)
        self.always_hope_after_dark = always_hope_after_dark
        self.zpd_low = zpd_low
        self.zpd_high = zpd_high
        # Below this reflection fraction, under-reflected themes get nudged.
        self.reflection_target = reflection_target

        # Minimal cross-cycle state.
        self._prev_intensity: Optional[float] = None
        self._stress_history: deque[float] = deque(maxlen=5)

        # Nudge memory.
        self._cycle = 0                              # evaluate() calls committed
        self._ladder: dict[str, int] = {}            # theme -> next rung index
        self._last_addressed: dict[str, int] = {}    # theme -> cycle last nudged
        self._last_nudges: list[str] = []            # nudges issued last cycle
        self.nudge_log: list[tuple[int, str, str]] = []  # (cycle, theme, strategy)
        self._rot: dict[str, int] = {}               # rotating-phrasing cursors
        # Snapshot for re-evaluating the same tick after a safety block.
        self._last_eval_tick: Optional[int] = None
        self._last_eval_blocked = False
        self._snapshot: Optional[tuple] = None

    # ──────────────────────────────────────────────────────────
    # Reflection
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def reflected_themes(
        identity: IdentitySnapshot, world: WorldState, scene: Scene
    ) -> dict[str, bool]:
        """Map each active theme to whether this world + scene reflect it."""
        tags = list(world.motifs) + [scene.theme] + list(scene.symbols)
        tags += [b.symbol for b in scene.beats if b.symbol]
        texts = [b.text for b in scene.beats] + [scene.title, scene.summary]
        return {t: _theme_reflected(t, tags, texts) for t in identity.active_themes}

    # ──────────────────────────────────────────────────────────
    # The protocol entry point
    # ──────────────────────────────────────────────────────────
    def evaluate(
        self, identity: IdentitySnapshot, world: WorldState, scene: Scene
    ) -> AlignmentReport:
        # The loop re-evaluates a regenerated scene in the same tick after a
        # safety block; roll the nudge memory back so that cycle counts once.
        if (
            self._last_eval_blocked
            and self._last_eval_tick == identity.tick
            and self._snapshot is not None
        ):
            self._restore()
        self._snapshot = self._save()

        stress = identity.emotion.stress

        # — Inner-outer alignment ————————————————————————————————
        # A forbidden theme is never a reflection target: it is not measured
        # and never nudged toward (the loop also strips it from its safe view).
        reflected = {
            t: ok for t, ok in self.reflected_themes(identity, world, scene).items()
            if not mentions_forbidden(t, self.forbidden_themes)
        }
        reflection_pct = (
            sum(reflected.values()) / len(reflected) if reflected else 0.0
        )

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
            identity, reflected, reflection_pct, adaptation_level, overwhelmed, bored
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
        self._last_nudges = list(nudges)
        self._cycle += 1
        self._last_eval_tick = identity.tick
        self._last_eval_blocked = blocked

        return report

    # ──────────────────────────────────────────────────────────
    # The earned signal for the convergence engine
    # ──────────────────────────────────────────────────────────
    def signal_for(
        self,
        identity: IdentitySnapshot,
        world: WorldState,
        scene: Scene,
        outcome: Outcome,
    ) -> AlignmentSignal:
        """Read what this cycle *earned* for inner/outer convergence.

        reflection   fraction of active themes the world/scene reflected, also
                     counting themes the outcome actually touched. With no
                     active themes, 0.5 (nothing to mirror, nothing missed).
        engagement   +1 met the challenge (success True), -1 avoided it
                     (success False, or avoidant language with no verdict),
                     0 neutral.
        integration  a lesson learned while engaging (+0.5), plus positive
                     trait growth (sum of positive deltas / 0.25, capped, x0.5).
                     A lesson recorded while turning away ("the question
                     remains") counts only as faint recognition (0.1).
        surprise_external  vision (0.5) + an entropy event beat (0.35; see
                     :func:`has_entropy_shock`) + the
                     part of scene intensity above 0.6 (up to 0.3).
        """
        # Reflection
        if identity.active_themes:
            reflected = self.reflected_themes(identity, world, scene)
            touched = " | ".join(t.lower() for t in outcome.themes_touched)
            for theme in reflected:
                if not reflected[theme] and theme.lower().strip() and theme.lower().strip() in touched:
                    reflected[theme] = True
            reflection = sum(reflected.values()) / len(reflected)
        else:
            reflection = 0.5

        # Engagement
        raw = (outcome.user_action.raw if outcome.user_action else "") or ""
        avoidant_words = any(w in raw.lower() for w in _AVOIDANT_WORDS)
        if outcome.success is True:
            engagement = 1.0
        elif outcome.success is False:
            engagement = -1.0
        elif avoidant_words:
            engagement = -0.5
        else:
            engagement = 0.0

        # Integration
        growth = sum(max(0.0, d) for d in outcome.trait_updates.values())
        trait_part = 0.5 * _clip(growth / 0.25)
        if outcome.success is False:
            integration = 0.1 if outcome.lesson else 0.0
        else:
            integration = (0.5 if outcome.lesson else 0.0) + trait_part

        # External surprise — only a genuine NPC-layer entropy event counts,
        # never the generator's own closing stage event.
        entropy = has_entropy_shock(scene)
        surprise = (
            (0.5 if scene.is_vision else 0.0)
            + (0.35 if entropy else 0.0)
            + 0.3 * _clip((scene.intensity - 0.6) / 0.4)
        )

        return AlignmentSignal(
            reflection=_clip(reflection),
            engagement=_clip(engagement, -1.0, 1.0),
            integration=_clip(integration),
            surprise_external=_clip(surprise),
        )

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
    def _pick_theme(self, unreflected: list[str], order: list[str]) -> str:
        """The under-reflected theme addressed least recently (ties: list order)."""
        return min(
            unreflected,
            key=lambda t: (self._last_addressed.get(t, -1), order.index(t)),
        )

    def _theme_nudge(self, theme: str) -> tuple[str, str]:
        rung = self._ladder.get(theme, 0) % len(NUDGE_LADDER)
        strategy, template = NUDGE_LADDER[rung]
        text = template.format(theme=theme)
        if text in self._last_nudges:          # never verbatim twice in a row
            rung = (rung + 1) % len(NUDGE_LADDER)
            strategy, template = NUDGE_LADDER[rung]
            text = template.format(theme=theme)
        self._ladder[theme] = rung + 1
        self._last_addressed[theme] = self._cycle
        self.nudge_log.append((self._cycle, theme, strategy))
        return strategy, text

    def _build_nudges(
        self,
        identity: IdentitySnapshot,
        reflected: dict[str, bool],
        reflection_pct: float,
        adaptation_level: float,
        overwhelmed: bool,
        bored: bool,
    ) -> list[str]:
        nudges: list[str] = []

        # A reflected theme has been met: stop nudging it and reset its ladder.
        for theme, ok in reflected.items():
            if ok:
                self._ladder.pop(theme, None)

        # Inner-outer: an active theme the world is failing to reflect.
        strategy = None
        unreflected = [t for t, ok in reflected.items() if not ok]
        if unreflected and reflection_pct < self.reflection_target:
            theme = self._pick_theme(unreflected, list(reflected))
            strategy, text = self._theme_nudge(theme)
            nudges.append(text)

        # Adaptation: the person is not engaging the material. Skip if the
        # theme nudge is already a confrontation; otherwise rotate phrasing.
        if adaptation_level < 0.35 and strategy != "confrontation":
            nudges.append(self._rotate("engage", _ENGAGE_VARIANTS))

        # Goal alignment: as convergence rises, mirror more directly.
        if identity.convergence >= 0.5 and strategy != "dial":
            nudges.append("raise the reflectiveness dial — inner and outer are converging")

        # Difficulty-driven beats.
        if overwhelmed:
            nudges.append("insert a calming interlude — lower stakes and give room to breathe")
            nudges.append("introduce a rest beat before the next challenge")
        elif bored:
            nudges.append(self._rotate("stakes", _STAKES_VARIANTS))

        return nudges

    def _rotate(self, key: str, variants: tuple[str, ...]) -> str:
        """Next phrasing of a recurring nudge, never the one issued last cycle."""
        idx = self._rot.get(key, 0)
        for k in range(len(variants)):
            text = variants[(idx + k) % len(variants)]
            if text not in self._last_nudges:
                self._rot[key] = (idx + k + 1) % len(variants)
                return text
        return variants[idx % len(variants)]

    # ──────────────────────────────────────────────────────────
    # Nudge-memory snapshot (for same-tick re-evaluation)
    # ──────────────────────────────────────────────────────────
    def _save(self) -> tuple:
        return (
            self._cycle, dict(self._ladder), dict(self._last_addressed),
            list(self._last_nudges), len(self.nudge_log), dict(self._rot),
            self._prev_intensity, len(self._stress_history),
            list(self._stress_history),
        )

    def _restore(self) -> None:
        (self._cycle, ladder, addressed, last, log_len, rot,
         self._prev_intensity, _n, hist) = self._snapshot  # type: ignore[misc]
        self._ladder = dict(ladder)
        self._last_addressed = dict(addressed)
        self._last_nudges = list(last)
        self._rot = dict(rot)
        del self.nudge_log[log_len:]
        self._stress_history = deque(hist, maxlen=self._stress_history.maxlen)

    # ──────────────────────────────────────────────────────────
    # Ethical / emotional safety — hard gates
    # ──────────────────────────────────────────────────────────
    def _safety_check(self, scene: Scene, stress: float) -> tuple[bool, Optional[str]]:
        # Final gate: a forbidden theme anywhere the protagonist can read it —
        # title, summary, theme, symbols, choices, and every beat's text,
        # speaker and symbol (see :func:`scene_forbidden_hit`).
        hit = scene_forbidden_hit(scene, self.forbidden_themes)
        if hit is not None:
            forbidden, field_name, text = hit
            snippet = text if len(text) <= 60 else text[:57] + "..."
            return True, (f"forbidden theme encountered: '{forbidden}' in "
                          f"{field_name} '{snippet}'")

        # Dangerously high intensity while already near the stress ceiling.
        if scene.intensity >= 0.8 and stress >= self.stress_ceiling * 0.9:
            return True, (
                f"scene intensity {scene.intensity:.2f} is unsafe while stress "
                f"{stress:.2f} is near the ceiling {self.stress_ceiling:.2f}"
            )

        return False, None

    def retract(self) -> None:
        """Undo the last :meth:`evaluate` (nudge memory, stress history, cycle
        count). The Feedback Loop Engine calls this before re-evaluating a
        replacement scene in the same tick, so a replaced scene counts once."""
        if self._snapshot is not None:
            self._restore()
            self._last_eval_tick = None
            self._last_eval_blocked = False
