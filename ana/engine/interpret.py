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

_ENGAGED = re.compile(
    r"\b(i|we)\b.*\b(help|lead|face|try|climb|organi[sz]e|save|stay|fight|speak|"
    r"choose|accept|forgive|trust|confront|protect|build|decide|act)\b",
    re.IGNORECASE,
)
_AVOIDANT = re.compile(
    r"\b(run|flee|hide|ignore|refuse|leave|avoid|give up|abandon|turn away|back)\b",
    re.IGNORECASE,
)


class Interpreter:
    def __init__(self, gateway: Optional[ModelGateway] = None, use_llm: bool = True):
        self.gateway = gateway
        self.use_llm = use_llm

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
        engaged = bool(_ENGAGED.search(raw))
        avoidant = bool(_AVOIDANT.search(raw))
        # If a choice index was given and no verbal cue, treat it as engagement.
        if action.choice_index is not None and not (engaged or avoidant):
            engaged = True

        theme = scene.theme or (identity.active_themes[0] if identity.active_themes else "self")

        # Emotional delta: intensity raises arousal; meeting a challenge lifts
        # valence and sheds stress; avoiding it does the reverse.
        if engaged and not avoidant:
            valence = 0.15 + 0.25 * scene.intensity
            stress = -0.1 - 0.1 * scene.intensity
            success: Optional[bool] = True
        elif avoidant:
            valence = -0.1 - 0.2 * scene.intensity
            stress = 0.1 + 0.15 * scene.intensity
            success = False
        else:
            valence = 0.05
            stress = 0.02
            success = None
        arousal = 0.1 + 0.3 * scene.intensity

        emotion_delta = EmotionalState(valence=valence, arousal=arousal, stress=stress)

        # Trait nudge on the theme (e.g. "leadership" -> "<theme>_confidence").
        trait_updates: dict[str, float] = {}
        trait_name = f"{theme}_confidence"
        if success is True:
            trait_updates[trait_name] = 0.12 + 0.1 * scene.intensity
        elif success is False:
            trait_updates[trait_name] = -0.06

        # Lesson: mint one when a themed challenge of real intensity was met.
        lesson = None
        if success is True and scene.intensity >= 0.45:
            lesson = _lesson_for(theme)
        elif success is False and scene.intensity >= 0.45:
            lesson = f"I turned away from {theme} this time; the question remains."

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


def _lesson_for(theme: str) -> str:
    table = {
        "leadership": "I can take charge when no one else will.",
        "trust": "Trust can be extended and survive the risk.",
        "courage": "Fear is not a reason to stop.",
        "purpose": "Being useful to others points toward my purpose.",
        "grief": "Grief can be carried without being hidden.",
        "freedom": "I can let go of what held me.",
        "wisdom": "Listening longer teaches more than speaking.",
        "shame": "What I hid loses power when I look at it.",
    }
    return table.get(theme, f"I met the test of {theme} and was changed by it.")


def _summarise(scene: Scene, raw: str, success: Optional[bool]) -> str:
    verb = {True: "met", False: "turned from", None: "moved through"}[success]
    head = scene.title or scene.theme
    act = (raw[:80] + "…") if len(raw) > 80 else raw
    return f"{head}: {verb} the moment — \"{act}\"" if act else f"{head}: {verb} the moment."
