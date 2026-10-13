"""Interpreter — turns a played-out scene into an Outcome.

The blueprint's Feedback Loop Engine "collects the outcomes ... and feeds those
back into the Identity Core as learning data." That interpretation step lives
here: given the scene, the user's action, and the world, it produces an
`Outcome` — themes touched, symbols, an emotional delta, trait updates, and
(when the moment warrants) a named lesson.

Deterministic by default; an optional LLM pass can enrich the reading. The
deterministic logic is transparent and auditable: emotion moves with scene
intensity and valence, the chosen theme's trait is nudged by whether the action
read as engaged/avoidant, and a lesson is minted when a themed challenge was met.
"""
from __future__ import annotations

import re
from typing import Optional

from ..contracts import (
    EmotionalState,
    IdentitySnapshot,
    Outcome,
    Scene,
    UserAction,
    WorldState,
)
from ..llm.gateway import ModelGateway

# Stance lexicon. An action is read as ENGAGING (meets the challenge), AVOIDING
# (turns from it), or REFLECTING (neither — a pause, a question, a gesture). Picking
# an offered choice says nothing by itself; its words carry the stance.
_ENGAGE = re.compile(
    r"\b(help|lead|face|facing|try|climb|organi[sz]e|save|stay|fight|speak|choose|"
    r"accept|forgive|trust|confront|protect|build|decide|act|answer|step(?:s)? across|"
    r"cross|take (?:it|charge|the)|light|signal|share|carry|offer|call for help|"
    r"go (?:in|on|forward)|press on|admit|tell (?:them|the truth)|stand)\b",
    re.IGNORECASE,
)
_AVOID = re.compile(
    r"\b(run|flee|hide|ignore|refuse|avoid|give up|abandon|turn(?:s|ed)? (?:away|back)|"
    r"back (?:away|off)|retreat|hesitate|walk away|look away|stay silent|"
    r"wait (?:for|it out)|let someone else|pretend|deflect|freeze)\b",
    re.IGNORECASE,
)
_REFLECT = re.compile(
    r"\b(pause|ask (?:what|why|myself)|question|reflect|wonder|consider|rest|breathe|"
    r"look back|observe|watch|listen|leave a token|remember|notice)\b",
    re.IGNORECASE,
)


def classify_stance(text: str) -> str:
    """Return 'engage', 'avoid', or 'reflect' for an action's text."""
    e = len(_ENGAGE.findall(text))
    a = len(_AVOID.findall(text))
    r = len(_REFLECT.findall(text))
    if a > e and a >= r:
        return "avoid"
    if e > a and e >= r:
        return "engage"
    if r and r >= max(e, a):
        return "reflect"
    if e and e == a:
        return "reflect"  # torn — read it as neither
    return "reflect" if not (e or a) else ("engage" if e else "avoid")


class Interpreter:
    def __init__(self, gateway: Optional[ModelGateway] = None, use_llm: bool = True):
        self.gateway = gateway
        self.use_llm = use_llm
        # How many lessons of each kind have been minted per theme, so a theme
        # met repeatedly yields deepening lessons instead of the same sentence.
        self._lesson_counts: dict[str, int] = {}

    def interpret(self, scene: Scene, action: UserAction, world: WorldState,
                  identity: IdentitySnapshot) -> Outcome:
        if self.use_llm and self.gateway is not None and self.gateway.available:
            llm = self._interpret_llm(scene, action, world, identity)
            if llm is not None:
                return llm
        return self._interpret_det(scene, action, world, identity)

    # -- deterministic ---------------------------------------------------
    def _interpret_det(self, scene: Scene, action: UserAction, world: WorldState,
                       identity: IdentitySnapshot) -> Outcome:
        raw = action.raw or ""
        stance = classify_stance(raw)

        theme = scene.theme or (identity.active_themes[0] if identity.active_themes else "self")

        # Emotional delta: intensity raises arousal; meeting a challenge lifts
        # valence and sheds stress; avoiding it does the reverse; reflecting
        # settles the self slightly without resolving anything.
        if stance == "engage":
            valence = 0.15 + 0.25 * scene.intensity
            stress = -0.1 - 0.1 * scene.intensity
            success: Optional[bool] = True
        elif stance == "avoid":
            # Turning from a hard moment hurts; turning from a gentle one is
            # mostly a missed chance.
            valence = -0.05 - 0.2 * scene.intensity
            stress = 0.02 + 0.2 * scene.intensity
            success = False
        else:
            # Reflection is restorative, and more so in a gentle scene: rest is how
            # an overwhelmed self recovers enough to try again.
            valence = 0.05 + 0.05 * (1.0 - scene.intensity)
            stress = -0.04 - 0.12 * (1.0 - scene.intensity)
            success = None
        arousal = 0.1 + 0.3 * scene.intensity

        emotion_delta = EmotionalState(valence=valence, arousal=arousal, stress=stress)

        # Trait nudge on the theme (e.g. "leadership" -> "<theme>_confidence").
        trait_updates: dict[str, float] = {}
        trait_name = f"{theme}_confidence"
        current = identity.trait(trait_name) or 0.5
        if success is True:
            # Diminishing returns: growth slows as a trait matures, so a strong
            # trait is earned over a journey rather than saturating in a few scenes.
            trait_updates[trait_name] = (0.10 + 0.08 * scene.intensity) * (1.0 - current) * 1.4
        elif success is False:
            trait_updates[trait_name] = -0.06 * (0.5 + current)

        # Lesson: mint one when a themed challenge of real intensity was met.
        lesson = None
        if success is True and scene.intensity >= 0.45:
            n = self._lesson_counts.get(("met", theme), 0)
            lesson = _lesson_for(theme, n)
            self._lesson_counts[("met", theme)] = n + 1
        elif success is False and scene.intensity >= 0.45:
            n = self._lesson_counts.get(("avoid", theme), 0)
            lesson = _AVOID_LESSONS[min(n, len(_AVOID_LESSONS) - 1)].format(
                t=theme, T=theme.capitalize())
            self._lesson_counts[("avoid", theme)] = n + 1

        symbols = list(dict.fromkeys(scene.symbols + world.motifs))[:5]
        summary = _summarise(scene, raw, success)

        return Outcome(
            scene_id=scene.id,
            summary=summary,
            user_action=action,
            emotion_delta=emotion_delta,
            themes_touched=[theme],
            symbols=symbols,
            lesson=lesson,
            trait_updates=trait_updates,
            success=success,
        )

    # -- optional LLM ----------------------------------------------------
    def _interpret_llm(self, scene: Scene, action: UserAction, world: WorldState,
                       identity: IdentitySnapshot) -> Optional[Outcome]:
        from ..llm.schemas import OUTCOME_SCHEMA

        system = (
            "You are the interpretation step of ANA, a simulation in which a "
            "person's identity and a symbolic world shape each other. Given the "
            "scene the protagonist just lived and what they chose to do, describe "
            "what it meant for them: a one-sentence summary, the themes it "
            "touched, the symbols that mattered, and — only if the moment earned "
            "one — a short first-person lesson. `success` is true if they met the "
            "challenge, false if they turned from it, null if neither applies. "
            "Emotional deltas are small and honest: valence and stress in "
            "[-0.4, 0.4], arousal in [0, 0.4]. Trait updates are deltas in "
            "[-0.3, 0.3] on traits named like '<theme>_confidence' or the traits "
            "the protagonist already has. Avoiding a challenge is not a moral "
            "failure; read it with the same care as engaging."
        )
        user = (
            f"THEME: {scene.theme}\nINTENSITY: {scene.intensity}\n"
            f"SCENE: {scene.summary or scene.title}\n"
            f"BEATS:\n" + "\n".join(f"- {b.kind}: {b.text}" for b in scene.beats[:8]) +
            f"\nACTION: {action.raw}\n"
            f"ACTIVE THEMES: {identity.active_themes}\n"
            f"CURRENT TRAITS: {[(t.name, round(t.value, 2)) for t in identity.traits]}\n"
        )
        data = self.gateway.structured(system, user, OUTCOME_SCHEMA, effort="low")
        if not data:
            return None

        def clamp(x, lo, hi):
            return max(lo, min(hi, float(x)))

        try:
            raw_traits = data.get("trait_updates") or []
            if isinstance(raw_traits, dict):  # tolerate the legacy mapping shape
                raw_traits = [{"trait": k, "delta": v} for k, v in raw_traits.items()]
            trait_updates = {
                str(t["trait"]): clamp(t["delta"], -0.3, 0.3)
                for t in raw_traits if isinstance(t, dict) and t.get("trait")
            }
            themes = [str(t) for t in (data.get("themes_touched") or []) if str(t).strip()]
            success = data.get("success")
            return Outcome(
                scene_id=scene.id,
                summary=str(data.get("summary", "")),
                user_action=action,
                emotion_delta=EmotionalState(
                    valence=clamp(data.get("valence_delta", 0.0), -0.4, 0.4),
                    arousal=clamp(data.get("arousal_delta", 0.0), 0.0, 0.4),
                    stress=clamp(data.get("stress_delta", 0.0), -0.4, 0.4),
                ),
                themes_touched=themes or [scene.theme],
                symbols=[str(s) for s in (data.get("symbols") or scene.symbols)][:6],
                lesson=(str(data["lesson"]).strip() or None) if data.get("lesson") else None,
                trait_updates=trait_updates,
                success=success if isinstance(success, bool) else None,
            )
        except (TypeError, ValueError, KeyError):
            return None


_LESSONS: dict[str, list[str]] = {
    "leadership": [
        "I can take charge when no one else will.",
        "Leading is mostly deciding before I feel certain.",
        "People follow steadiness more than they follow brilliance.",
        "I can carry the weight of a choice and still ask for help.",
    ],
    "trust": [
        "Trust can be extended and survive the risk.",
        "Trusting someone is a choice I make, not a feeling I wait for.",
        "Being trusted asks as much of me as trusting does.",
    ],
    "courage": [
        "Fear is not a reason to stop.",
        "Courage is fear that has decided to keep walking.",
        "The thing I dreaded was smaller once I faced it.",
        "I can be afraid and still be the one who moves.",
    ],
    "purpose": [
        "Being useful to others points toward my purpose.",
        "Purpose shows up in what I keep returning to.",
        "I don't find meaning first and act later; acting is how I find it.",
    ],
    "grief": [
        "Grief can be carried without being hidden.",
        "What I lost still shapes what I love.",
    ],
    "freedom": [
        "I can let go of what held me.",
        "Freedom is choosing my constraints, not escaping all of them.",
    ],
    "wisdom": [
        "Listening longer teaches more than speaking.",
        "Not knowing, said aloud, is a kind of strength.",
    ],
    "shame": [
        "What I hid loses power when I look at it.",
        "I am more than the moment I'm ashamed of.",
    ],
}
_AVOID_LESSONS = [
    "I turned away from {t} this time; the question remains.",
    "Turning from {t} again — I notice the pattern now.",
    "{T} keeps finding me. Maybe it isn't going to stop asking.",
]


def _lesson_for(theme: str, nth: int = 0) -> str:
    bank = _LESSONS.get(theme)
    if not bank:
        return f"I met the test of {theme} and was changed by it."
    return bank[min(nth, len(bank) - 1)] if nth < len(bank) else bank[nth % len(bank)]


def _summarise(scene: Scene, raw: str, success: Optional[bool]) -> str:
    verb = {True: "met", False: "turned from", None: "moved through"}[success]
    head = scene.title or scene.theme
    act = (raw[:80] + "…") if len(raw) > 80 else raw
    return f"{head}: {verb} the moment — \"{act}\"" if act else f"{head}: {verb} the moment."
