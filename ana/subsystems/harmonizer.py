"""World-Truth Harmonizer — Reality Anchoring & Consistency.

This module implements the blueprint's **World-Truth Harmonizer (Reality
Anchoring & Consistency)** — section *D. World-Truth Harmonizer* of Mohamed
Mousa's "Adaptive Neural-Simulation Architecture".

From the blueprint:

    "The World-Truth Harmonizer ensures that ANA's simulation, while creative
    and personal, maintains internal consistency and aligns with external
    truths or logical norms as needed. It's effectively a filter and guide on
    the content generation and world state to avoid uncontrolled fantasy or
    contradiction."

It is *the guardian of coherence*: it lets the simulation explore strange,
intuitive, archetypal narratives, yet keeps the final experience "something the
user can integrate rather than just a fever dream". Concretely it does three
jobs, mirroring the three bullets of the blueprint's section D:

1. **Internal Consistency / Continuity** — "if an NPC died in a previous
   chapter, the harmonizer will block them from randomly showing up alive later
   unless there's a narrative reason (and if there is, that reason must be
   explained, e.g. 'it was their twin' or a resurrection event)." It is "like a
   dungeon master's rulebook and a continuity editor combined", tracking facts
   (deaths, known characters) across cycles.

2. **Symbolism vs. Literal Calibration** — "This calibration keeps the user
   oriented: they know when they're experiencing a vision vs. a normal event."
   A sudden surreal scene dropped into a realistic run is flagged
   ``is_vision=True`` and given a grounding note, so the Identity Core does not
   take it literally (reality testing / delusion prevention). A scene arriving
   while the run is already "deep in a visionary segment" is left alone.

3. **Reality Anchoring / User Reality Integration** — NPCs are "a conduit for
   truth": the harmonizer occasionally attaches a plain-language grounding note
   (a proverb or bridge to universal wisdom) "that an NPC could deliver" to keep
   the user oriented and to tie simulation insights to truths outside it.

The harmonizer only moderates (``approved=False``) genuinely incoherent scenes
it cannot repair with corrections — the blueprint's "override layer that can
veto". In the common case it approves the scene and hands back corrections and a
grounding note, because its purpose is to *harmonize*, not to censor.

Pure standard library + pydantic + project contracts. Deterministic (seeded
RNG), no LLM.
"""
from __future__ import annotations

import re
import random
from typing import Optional

from ..contracts import (
    HarmonizerVerdict,
    IdentitySnapshot,
    Scene,
    WorldState,
)


# Words that signal dreamlike / surreal / non-literal content. Used to estimate
# how far a scene has drifted from grounded reality (symbolism calibration).
_DREAMLIKE = (
    "melt", "melting", "dissolve", "dissolving", "float", "floating",
    "impossible", "clockwork", "endless", "infinite", "shifting", "liquid",
    "weightless", "backwards", "upside", "bleeding light", "sing", "singing",
    "whisper", "glowing", "fractal", "mirror", "mirrors", "shimmer",
    "boundless", "void", "unravel", "unraveling", "warp", "warping", "swirl",
    "morph", "morphing", "ether", "ethereal", "spectral", "phantom",
)

# Phrases that, if present near a dead NPC's name, give a *narrative reason* for
# the reappearance — the blueprint's "it was their twin / a resurrection event".
# When one of these is present the harmonizer does NOT raise a continuity error.
_REVIVAL_REASONS = (
    "twin", "vision", "dream", "ghost", "spirit", "apparition", "memory",
    "flashback", "hallucination", "resurrect", "reborn", "illusion",
    "echo of", "spectre", "specter", "phantom", "in the afterlife",
)

# Grounding wisdom an NPC could deliver — the "proverb or piece of real
# knowledge that gives perspective" the blueprint calls for. About a dozen.
_GROUNDING_NOTES = (
    "Sometimes, even in the real world, one must face their inner demons.",
    "A mirror shows a true face only to those willing to look.",
    "Storms pass; the lighthouse remains. What in you is the lighthouse?",
    "Not every sign is a summons — some are only weather.",
    "What you flee in the dream, you will meet again by daylight.",
    "The map is not the territory; hold your certainties loosely.",
    "Grief and growth often wear the same coat.",
    "Even here, breath is real and the ground still holds you.",
    "A symbol points beyond itself; do not mistake the finger for the moon.",
    "Courage is not the absence of fear but moving while afraid.",
    "What repeats is asking to be understood, not obeyed.",
    "The strangest visions still answer to an honest heart.",
)


class TruthHarmonizer:
    """Reality anchoring, continuity, and symbolism calibration.

    Implements the :class:`~ana.interfaces.WorldTruthHarmonizer` protocol. The
    instance keeps continuity state across :meth:`review` calls so it can act as
    the blueprint's "continuity editor" / "dungeon master's rulebook": it
    remembers which NPCs exist, which have died, which world facts have been
    established, and how literal-vs-symbolic the recent scenes have been.
    """

    # How surreal a scene must be to count as "very surreal".
    _SURREAL_HIGH = 0.5
    # Below this, the recent run counts as "realistic / grounded".
    _REALISTIC_LOW = 0.35
    # Baseline chance of volunteering a grounding note on an ordinary scene.
    _GROUNDING_CHANCE = 0.25

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)
        # Continuity state, carried across every review() call.
        self._seen_npcs: set[str] = set()       # every NPC name encountered
        self._dead_npcs: set[str] = set()        # NPCs recorded dead (by name)
        self._world_facts: set[str] = set()      # established declarative facts
        # Rolling record of recent symbolic scores (literal 0 .. surreal 1), so
        # a deep-in-a-vision sequence is recognised and tolerated.
        self._symbolic_history: list[float] = []
        self._history_window = 5

    # ────────────────────────────────────────────────────────────
    # Protocol entry point
    # ────────────────────────────────────────────────────────────

    def review(
        self,
        scene: Scene,
        world: WorldState,
        identity: IdentitySnapshot,
    ) -> HarmonizerVerdict:
        """Review a proposed scene for coherence and return a verdict.

        Runs the three blueprint checks — continuity, symbolism calibration, and
        reality anchoring — then decides moderation. Updates internal continuity
        state as a side effect so later scenes are judged against this one.
        """
        self._register_npcs(world)

        continuity_errors: list[str] = []
        corrections: list[str] = []

        # 1. INTERNAL CONSISTENCY / CONTINUITY ----------------------------------
        # Check reappearances of already-dead NPCs *before* recording any new
        # deaths from this scene (so a death scene doesn't flag itself).
        self._check_dead_reappearances(scene, continuity_errors, corrections)
        self._check_fact_contradictions(scene, continuity_errors, corrections)
        # Now fold this scene's deaths and facts into memory.
        self._record_deaths(scene)
        self._record_facts(scene)

        # 2. SYMBOLISM vs LITERAL CALIBRATION -----------------------------------
        symbolic_score = self._symbolic_score(scene)
        recent_avg = self._recent_symbolic_avg()
        is_vision = bool(scene.is_vision)
        grounding_note: Optional[str] = None

        sudden_surreal = (
            symbolic_score >= self._SURREAL_HIGH
            and recent_avg < self._REALISTIC_LOW
        )
        if sudden_surreal and not is_vision:
            # Realistic run, suddenly very surreal: contextualise as a vision so
            # the Identity Core won't take it literally (reality testing).
            is_vision = True
            grounding_note = (
                "This has the texture of a vision, not a literal event — "
                + self._pick_grounding_note()
            )
        # Record this scene's symbolism so a sustained visionary sequence is
        # recognised next time (deep-in-a-vision scenes are then allowed).
        self._push_symbolic(symbolic_score)

        # 3. REALITY ANCHORING --------------------------------------------------
        # Attach a grounding note when symbolic content is high, or occasionally
        # at random — a plain-language bridge an NPC could deliver.
        if grounding_note is None:
            high_symbol = symbolic_score >= self._SURREAL_HIGH
            if high_symbol or self.rng.random() < self._GROUNDING_CHANCE:
                grounding_note = self._pick_grounding_note()

        # 4. MODERATION / APPROVAL ---------------------------------------------
        approved = self._decide_approval(scene, continuity_errors)

        return HarmonizerVerdict(
            approved=approved,
            is_vision=is_vision,
            continuity_errors=continuity_errors,
            corrections=corrections,
            grounding_note=grounding_note,
        )

    # ────────────────────────────────────────────────────────────
    # Continuity helpers
    # ────────────────────────────────────────────────────────────

    def _register_npcs(self, world: WorldState) -> None:
        """Remember every NPC name present, and note any already flagged dead."""
        for npc in world.npcs:
            if npc.name:
                self._seen_npcs.add(npc.name)
                if not npc.alive:
                    self._dead_npcs.add(npc.name)

    def _scene_texts(self, scene: Scene) -> list[tuple[str, Optional[str]]]:
        """Return (text, speaker) pairs for every beat, plus the title."""
        pairs: list[tuple[str, Optional[str]]] = [(scene.title or "", None)]
        for beat in scene.beats:
            pairs.append((beat.text or "", beat.speaker))
        return pairs

    def _check_dead_reappearances(
        self,
        scene: Scene,
        continuity_errors: list[str],
        corrections: list[str],
    ) -> None:
        """Flag a dead NPC speaking/appearing with no stated narrative reason."""
        flagged: set[str] = set()
        for name in self._dead_npcs:
            if name in flagged:
                continue
            for text, speaker in self._scene_texts(scene):
                mentions = speaker == name or self._name_in(name, text)
                if not mentions:
                    continue
                # A fresh death cue in the same beat is not a reappearance.
                if self._death_cue(name, text):
                    continue
                # A stated reason (twin, vision, ghost, resurrection...) excuses it.
                if any(reason in text.lower() for reason in _REVIVAL_REASONS):
                    continue
                continuity_errors.append(
                    f"NPC '{name}' was recorded dead but reappears alive with "
                    f"no stated reason."
                )
                corrections.append(
                    f"Frame '{name}'s reappearance as a twin/vision/spirit, give "
                    f"an explicit resurrection beat, or remove it."
                )
                flagged.add(name)
                break

    def _record_deaths(self, scene: Scene) -> None:
        """Scan beats for death cues about known NPCs and remember them."""
        for text, _speaker in self._scene_texts(scene):
            lowered = text.lower()
            if not lowered:
                continue
            for name in self._seen_npcs:
                if name in self._dead_npcs:
                    continue
                if self._death_cue(name, text):
                    self._dead_npcs.add(name)

    def _death_cue(self, name: str, text: str) -> bool:
        """True if *text* says *name* died / was killed."""
        if not self._name_in(name, text):
            return False
        lowered = text.lower()
        n = re.escape(name.lower())
        patterns = (
            rf"{n}\s+(?:has\s+|had\s+)?died",
            rf"{n}\s+is\s+dead",
            rf"{n}\s+was\s+(?:killed|slain|murdered)",
            rf"{n}\s+(?:has\s+)?(?:fallen|perished)",
            rf"killed\s+{n}\b",
            rf"slew\s+{n}\b",
            rf"death\s+of\s+{n}\b",
            rf"buried\s+{n}\b",
        )
        return any(re.search(p, lowered) for p in patterns)

    @staticmethod
    def _name_in(name: str, text: str) -> bool:
        """Whole-name match (case-insensitive), robust to surrounding punctuation."""
        if not name or not text:
            return False
        return re.search(rf"\b{re.escape(name.lower())}\b", text.lower()) is not None

    # ────────────────────────────────────────────────────────────
    # Fact tracking (lightweight logical continuity)
    # ────────────────────────────────────────────────────────────

    def _record_facts(self, scene: Scene) -> None:
        """Remember short declarative facts from description/event beats.

        This is the blueprint's "logic rules engine for continuity" in miniature:
        a memory of plain world facts, kept so later scenes can be checked for
        direct contradiction.
        """
        for beat in scene.beats:
            if beat.kind not in ("description", "event"):
                continue
            fact = beat.text.strip().lower().rstrip(".")
            if 0 < len(fact) <= 120:
                self._world_facts.add(fact)

    def _check_fact_contradictions(
        self,
        scene: Scene,
        continuity_errors: list[str],
        corrections: list[str],
    ) -> None:
        """Flag a beat that directly negates an already-established fact."""
        for beat in scene.beats:
            if beat.kind not in ("description", "event"):
                continue
            fact = beat.text.strip().lower().rstrip(".")
            if not fact:
                continue
            negated = self._negate(fact)
            if negated and negated in self._world_facts:
                continuity_errors.append(
                    f"Scene asserts '{fact}', contradicting established fact "
                    f"'{negated}'."
                )
                corrections.append(
                    "Reconcile the two statements or stage an explicit change "
                    "(explain how/why the established fact no longer holds)."
                )

    @staticmethod
    def _negate(fact: str) -> Optional[str]:
        """Produce the simple negation of a fact for contradiction lookup."""
        if " is not " in fact:
            return fact.replace(" is not ", " is ", 1)
        if " is " in fact:
            return fact.replace(" is ", " is not ", 1)
        return None

    # ────────────────────────────────────────────────────────────
    # Symbolism calibration helpers
    # ────────────────────────────────────────────────────────────

    def _symbolic_score(self, scene: Scene) -> float:
        """Estimate how symbolic/surreal a scene is, on 0 (literal) .. 1 (dream)."""
        score = 0.0
        # Many symbols -> more surreal. ~3 symbols saturates this component.
        score += min(len(scene.symbols), 6) / 6.0 * 0.4
        # Dreamlike vocabulary across the beats.
        text = " ".join(b.text for b in scene.beats).lower()
        hits = sum(1 for w in _DREAMLIKE if w in text)
        score += min(hits, 4) / 4.0 * 0.35
        # Explicit vision flag and raw intensity.
        if scene.is_vision:
            score += 0.3
        score += scene.intensity * 0.15
        return max(0.0, min(1.0, score))

    def _recent_symbolic_avg(self) -> float:
        if not self._symbolic_history:
            return 0.0
        return sum(self._symbolic_history) / len(self._symbolic_history)

    def _push_symbolic(self, score: float) -> None:
        self._symbolic_history.append(score)
        if len(self._symbolic_history) > self._history_window:
            self._symbolic_history.pop(0)

    # ────────────────────────────────────────────────────────────
    # Reality anchoring + moderation
    # ────────────────────────────────────────────────────────────

    def _pick_grounding_note(self) -> str:
        return self.rng.choice(_GROUNDING_NOTES)

    def _decide_approval(self, scene: Scene, continuity_errors: list[str]) -> bool:
        """Approve unless the scene is genuinely incoherent beyond repair.

        The blueprint's override layer should "veto" sparingly — the harmonizer
        prefers to approve with corrections. A scene is rejected only when it is
        riddled with contradictions (several at once) or is empty of any
        reviewable content.
        """
        if not scene.beats and not scene.symbols and not scene.title:
            return False
        if len(continuity_errors) >= 3:
            return False
        return True
