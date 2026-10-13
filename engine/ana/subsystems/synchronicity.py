"""Synchronicity Engine — Meaning Orchestration.

This is the ANA blueprint's *Synchronicity Engine (Meaning Orchestration)*: the
subsystem that manufactures meaningful coincidences — outer events that rhyme
with the user's inner state with uncanny timing. The blueprint's core insight is
that a synchronicity only *feels* fateful when it is rare: if the world mirrored
every passing mood, the effect would collapse into noise. So meaning here is
"artfully generated, not left to chance", yet deliberately *rationed* with a
probability model and a cooldown so each hit stays special.

The engine is advisory. It never writes a scene. Instead it hands the Generative
Experience Engine a :class:`~ana.contracts.SynchronicityHint` — a trigger, a
symbol, and a one-sentence staging instruction — and lets that engine weave the
coincidence in thematically-but-not-too-obviously. This keeps meaning-making
decoupled from imagination, as the interface in ``ana.interfaces`` prescribes.

Pipeline, per the blueprint's Synchronicity Engine section:

1. **Trigger detection** — scan the :class:`IdentitySnapshot` for a *salient*
   inner state: a strong emotion (high ``|valence|`` or high ``arousal``), a
   prominent active theme, a goal nearing completion, or a "longing" (low
   valence fused with a yearning theme such as ``home`` or ``purpose``).
2. **Cooldown + probability** — if the engine recently fired it stays silent;
   otherwise it fires with a probability scaled from ``base_prob`` by the
   *potency* of the trigger, so the more charged the moment, the likelier the
   coincidence.
3. **Event matching** — map the inner state to a fitting *symbol* drawn from an
   internal dictionary, choosing among several candidates with the rng for
   variety, and emit the hint.
"""
from __future__ import annotations

import random

from ..contracts import Emotion, IdentitySnapshot, SynchronicityHint, WorldState

# ─────────────────────────────────────────────────────────────
# Symbol vocabulary — the engine's lexicon of meaningful signs.
# Each key is an inner-state label; each value a pool of candidate symbols so
# the same longing never resolves to the same omen twice running.
# ─────────────────────────────────────────────────────────────

_SYMBOLS: dict[str, list[str]] = {
    # Emotion enum values
    "joy": ["a sudden burst of birdsong overhead", "sunlight breaking across a wet street"],
    "fear": ["a door left ajar that was surely locked", "a shadow that keeps pace one step behind"],
    "anger": ["a storm front massing on the horizon", "a bell cracked mid-peal"],
    "sadness": ["a candle that stays lit in the wind", "rain that falls only on one side of the lane"],
    "trust": ["a stranger who hands back a dropped coin", "two keys that fit the same lock"],
    "anticipation": ["a train whistle from a line long closed", "a clock striking the hour early"],
    "surprise": ["a playing card face-up on the path", "a mirror where none hung yesterday"],
    "curiosity": ["a map with one road inked in that no atlas shows", "an open book turned to a marked page"],
    "awe": ["an aurora over a city that never sees one", "a cathedral bell answering from nowhere"],
    "shame": ["a cracked mirror that still reflects clearly", "a mask slipping from a festival wall"],
    "acceptance": ["a tide that turns at the exact moment you pause", "a seed cracking open underfoot"],
    # Themes & yearnings
    "hope": ["a white feather drifting down", "a single star through torn cloud"],
    "home": ["a letter from home carried by a stranger", "the smell of a kitchen that isn't there"],
    "longing-for-home": ["a letter from home carried by a stranger", "a lamp lit in a window far off"],
    "courage": ["graffiti of a remembered quote", "a frayed rope that holds when tested"],
    "guidance": ["an unexpected mentor's arrival", "a signpost repainted overnight"],
    "wisdom": ["an old woman who answers the question you didn't ask", "an owl on a daylit branch"],
    "grief": ["a candle that stays lit in the wind", "a photograph the rain won't blur"],
    "freedom": ["a cage door rusted open", "a kite cut loose and still climbing"],
    "purpose": ["a compass that ignores north for your heading", "a path cleared where there was only thicket"],
    "leadership": ["a crowd that parts without being asked", "a lantern passed hand to hand toward you"],
    "self": ["your own reflection half a second too slow", "a name carved that matches your own"],
    # Fallback
    "mystery": ["a coincidence too neat to be chance", "a sign meant, unmistakably, for you"],
}

# Themes the blueprint reads as *yearning* — when fused with low valence they
# become a "longing" trigger rather than a plain active theme.
_YEARNING_THEMES = {"home", "purpose", "freedom", "belonging", "meaning"}

# Map Emotion enum members to the closest thematic symbol family, so a dominant
# emotion with no matching theme still resolves to a fitting omen.
_EMOTION_SYMBOL_KEY: dict[Emotion, str] = {
    Emotion.JOY: "joy",
    Emotion.FEAR: "fear",
    Emotion.ANGER: "anger",
    Emotion.SADNESS: "grief",
    Emotion.TRUST: "trust",
    Emotion.ANTICIPATION: "anticipation",
    Emotion.SURPRISE: "surprise",
    Emotion.CURIOSITY: "curiosity",
    Emotion.AWE: "awe",
    Emotion.SHAME: "shame",
    Emotion.ACCEPTANCE: "acceptance",
}


class _Trigger:
    """A detected salient inner state awaiting a symbol."""

    __slots__ = ("label", "key", "potency")

    def __init__(self, label: str, key: str, potency: float) -> None:
        self.label = label
        self.key = key
        self.potency = max(0.0, min(1.0, potency))


class SynchronicityOrchestrator:
    """Rations meaningful coincidences — the blueprint's Synchronicity Engine.

    See the module docstring for the full Meaning-Orchestration pipeline. The
    orchestrator is stateful across cycles: it keeps a cooldown counter so it
    cannot fire on consecutive cycles, and a seeded rng so a journey replays
    identically.
    """

    def __init__(self, base_prob: float = 0.35, cooldown: int = 2, seed: int = 42) -> None:
        self.base_prob = max(0.0, min(1.0, base_prob))
        self.cooldown = max(0, int(cooldown))
        self._rng = random.Random(seed)
        self._cooldown_left = 0
        self._fires = 0

    # ── Public interface (SynchronicityEngine protocol) ──────────

    def consider(self, identity: IdentitySnapshot, world: WorldState) -> SynchronicityHint | None:
        """Consider weaving a synchronicity for the current inner state.

        Returns a :class:`SynchronicityHint` when the stars align — a salient
        trigger, no active cooldown, and the probability roll succeeds — else
        ``None``. Firing resets the cooldown; otherwise the cooldown ticks down.
        """
        trigger = self._detect_trigger(identity)
        if trigger is None:
            # Nothing charged enough to deserve a sign; still let cooldown thaw.
            if self._cooldown_left > 0:
                self._cooldown_left -= 1
            return None

        # Cooldown gate: refuse to fire on consecutive cycles so signs stay rare.
        if self._cooldown_left > 0:
            self._cooldown_left -= 1
            return None

        # Probability gate: scale base_prob up by the trigger's potency so the
        # most charged moments are the most likely to be answered by the world.
        prob = self.base_prob * (0.5 + 0.5 * trigger.potency)
        prob = max(0.0, min(1.0, prob))
        if self._rng.random() > prob:
            return None

        # It fires. Reset cooldown, pick a symbol, stage the suggestion.
        self._cooldown_left = self.cooldown
        self._fires += 1
        symbol = self._pick_symbol(trigger)
        suggestion = self._compose_suggestion(trigger, symbol, world)
        return SynchronicityHint(
            trigger=trigger.label,
            symbol=symbol,
            suggestion=suggestion,
            potency=round(trigger.potency, 3),
        )

    # ── Trigger detection ────────────────────────────────────────

    def _detect_trigger(self, identity: IdentitySnapshot) -> _Trigger | None:
        """Scan identity for the single most salient inner state, or None."""
        emo = identity.emotion
        candidates: list[_Trigger] = []

        # 1. Longing: low valence fused with a yearning theme — the blueprint's
        #    archetypal synchronicity seed (the exile who finds a letter home).
        for theme in identity.active_themes:
            t = theme.lower()
            if t in _YEARNING_THEMES and emo.valence <= -0.2:
                deficit = min(1.0, -emo.valence)
                key = "longing-for-home" if t == "home" else t
                candidates.append(_Trigger(f"longing for {t}", key, 0.55 + 0.4 * deficit))

        # 2. A goal cresting toward completion wants an omen of arrival.
        for goal in identity.goals:
            if goal.progress >= 0.75:
                candidates.append(_Trigger("a goal nearing fruition", "purpose", 0.5 + 0.4 * goal.progress))
                break

        # 3. Strong emotion: high arousal or high |valence| tied to the dominant.
        intensity = max(abs(emo.valence), emo.arousal)
        if intensity >= 0.55:
            key = _EMOTION_SYMBOL_KEY.get(emo.dominant, "mystery")
            label = f"a surge of {emo.dominant.value}"
            candidates.append(_Trigger(label, key, 0.4 + 0.6 * intensity))

        # 4. A prominent active theme on its own (weaker than a longing).
        for theme in identity.active_themes:
            t = theme.lower()
            if t in _SYMBOLS:
                candidates.append(_Trigger(f"the theme of {t}", t, 0.45))
                break

        if not candidates:
            return None
        # The loudest signal in the psyche wins the right to be answered.
        return max(candidates, key=lambda c: c.potency)

    # ── Event matching ───────────────────────────────────────────

    def _pick_symbol(self, trigger: _Trigger) -> str:
        """Choose a symbol for the trigger, varying it via the rng."""
        pool = _SYMBOLS.get(trigger.key) or _SYMBOLS["mystery"]
        return self._rng.choice(pool)

    def _compose_suggestion(self, trigger: _Trigger, symbol: str, world: WorldState) -> str:
        """One-sentence staging note for the Generative Engine.

        Deliberately under-specified: the coincidence should land as felt, not
        as announced, honouring the blueprint's "thematically but not too
        obviously" rule.
        """
        openers = [
            f"Let {symbol} appear in passing at {world.location}",
            f"Have {symbol} surface quietly amid {world.location}",
            f"Slip {symbol} into the scene at {world.location}",
        ]
        opener = self._rng.choice(openers)
        return (
            f"{opener}, so it rhymes with the protagonist's {trigger.label} "
            f"without the narration ever naming the connection."
        )
