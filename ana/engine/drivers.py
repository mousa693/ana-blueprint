"""Drivers — who acts in the simulation.

The blueprint notes the protagonist may be "the user (or an AI agent representing
them)." ANA supports both:

  * `HumanDriver`      — a person types the action (interactive sessions).
  * `ScriptedDriver`   — a fixed list of actions (tests, replays, the worked
                         example).
  * `AutonomousDriver` — the self acts on its own, so the loop genuinely "lives"
                         without waiting for input. It chooses from the scene's
                         offered choices using the identity's traits/emotion, or
                         (optionally) roleplays a free-text action via the model.

All return a `UserAction`.
"""
from __future__ import annotations

import random
from typing import Optional, Sequence

from ..contracts import IdentitySnapshot, Scene, UserAction
from ..llm.gateway import ModelGateway


class HumanDriver:
    def act(self, scene: Scene, identity: IdentitySnapshot) -> UserAction:  # pragma: no cover
        print(f"\n— {scene.title} —\n{scene.summary}")
        for b in scene.beats:
            who = f"{b.speaker}: " if b.speaker else ""
            print(f"  [{b.kind}] {who}{b.text}")
        if scene.choices:
            for i, c in enumerate(scene.choices):
                print(f"   {i}) {c}")
        raw = input("\nWhat do you do? ").strip()
        idx = None
        if raw.isdigit() and scene.choices and int(raw) < len(scene.choices):
            idx = int(raw)
            raw = scene.choices[idx]
        return UserAction(raw=raw, choice_index=idx)


class ScriptedDriver:
    def __init__(self, actions: Sequence[str]):
        self._actions = list(actions)
        self._i = 0

    def act(self, scene: Scene, identity: IdentitySnapshot) -> UserAction:
        if self._i < len(self._actions):
            raw = self._actions[self._i]
            self._i += 1
        else:
            raw = scene.choices[0] if scene.choices else "I observe and continue."
        return UserAction(raw=raw)


class AutonomousDriver:
    """The protagonist acts for itself — the living half of the loop.

    Deterministic policy: score each offered choice by how well it matches the
    identity's disposition (engaged, high-agency selves lean into challenge;
    fearful/avoidant states lean away), then pick with a little rng-driven
    variation so the journey isn't rigid. With a model available, it can instead
    roleplay a short free-text action in-character.
    """

    _ENGAGE_WORDS = ("help", "lead", "face", "climb", "save", "organize", "organise",
                     "stay", "speak", "accept", "trust", "confront", "protect", "try")
    _AVOID_WORDS = ("run", "flee", "hide", "ignore", "refuse", "leave", "avoid", "wait")

    def __init__(self, gateway: Optional[ModelGateway] = None, seed: int = 42,
                 use_llm: bool = False):
        self.gateway = gateway
        self.rng = random.Random(seed)
        self.use_llm = use_llm

    def act(self, scene: Scene, identity: IdentitySnapshot) -> UserAction:
        if self.use_llm and self.gateway is not None and self.gateway.available:
            a = self._act_llm(scene, identity)
            if a is not None:
                return a
        return self._act_policy(scene, identity)

    def _agency(self, identity: IdentitySnapshot) -> float:
        """0..1 disposition toward engaging rather than avoiding."""
        confid = [t.value for t in identity.traits if t.name.endswith("_confidence")]
        courage = identity.trait("courage_confidence") or identity.trait("courage")
        conf = (sum(confid) / len(confid)) if confid else courage
        base = 0.45 + 0.3 * identity.emotion.valence    # positive mood -> more agency
        base -= 0.4 * identity.emotion.stress            # stress -> withdrawal
        base += 0.3 * conf                               # growing confidence -> boldness
        return max(0.0, min(1.0, base))

    def _act_policy(self, scene: Scene, identity: IdentitySnapshot) -> UserAction:
        from .interpret import classify_stance

        agency = self._agency(identity)
        if not scene.choices:
            raw = "I lean in and act." if agency >= 0.5 else "I hold back and watch."
            return UserAction(raw=raw)

        # A self under strain reaches for a pause; a settled, confident self leans
        # into the challenge; a frightened one turns away. Exploration noise keeps
        # the same self from making identical choices forever.
        stress = identity.emotion.stress

        def score(choice: str) -> float:
            stance = classify_stance(choice)
            if stance == "engage":
                # A gentler ask is easier to say yes to: low-intensity scenes
                # lower the threshold for engaging (small first steps).
                s = agency + 0.6 * (0.5 - scene.intensity)
            elif stance == "avoid":
                # The pull to flee scales with how threatening the scene is: no one
                # runs from a calm meadow, they rest in it.
                s = (1.0 - agency) * 0.9 * min(1.0, 2.0 * scene.intensity)
            else:
                s = 0.35 + 0.4 * stress
            return s + self.rng.uniform(0.0, 0.3)

        idx = max(range(len(scene.choices)), key=lambda i: score(scene.choices[i]))
        return UserAction(raw=scene.choices[idx], choice_index=idx)

    def _act_llm(self, scene: Scene, identity: IdentitySnapshot) -> Optional[UserAction]:
        from ..llm.schemas import ACTION_SCHEMA

        system = (
            "You are the protagonist of an inner-journey simulation, acting from "
            "your traits and mood rather than from what would be ideal. Given the "
            "scene, say what you do next in one or two first-person sentences. If "
            "one of the offered choices is what you do, set choice_index to its "
            "0-based index; otherwise null."
        )
        user = (
            f"MY TRAITS: {[(t.name, round(t.value,2)) for t in identity.traits]}\n"
            f"MY THEMES: {identity.active_themes}\nMOOD valence={identity.emotion.valence:.2f} "
            f"stress={identity.emotion.stress:.2f}\n\nSCENE: {scene.summary or scene.title}\n"
            + "\n".join(f"- {b.text}" for b in scene.beats[:6])
            + (f"\nCHOICES: {scene.choices}" if scene.choices else "")
        )
        data = self.gateway.structured(system, user, ACTION_SCHEMA, effort="low")
        if not data or not str(data.get("action", "")).strip():
            return None
        idx = data.get("choice_index")
        if not (isinstance(idx, int) and 0 <= idx < len(scene.choices)):
            idx = None
        return UserAction(raw=str(data["action"]).strip(), choice_index=idx)
