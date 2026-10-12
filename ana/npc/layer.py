"""NPC / Entropy Layer — autonomous others and controlled chance.

This module implements the blueprint's "NPC/Entropy Layer": the part of ANA that
keeps the simulated world from collapsing into a solipsistic echo of the user.
Two forces push back against the protagonist:

1. **Autonomous NPCs.** Some non-player characters are *mirrors* — they
   personify an identity element (a ``SHADOW`` who embodies a trait the user is
   conflicted on, a ``MENTOR`` who carries a goal). Others are *world* NPCs with
   high ``autonomy`` and ``represents=None``: they have their own agendas and are
   not reflections of the user's psyche. The blend is what makes the world feel
   inhabited rather than hallucinated.
2. **Controlled entropy.** With a bounded probability the layer injects one
   *unplanned but context-aware* event into a scene — a weather shift, a found
   object, an NPC doing something off-script. It is chance that fits the place
   and theme, never random nonsense (the blueprint treats entropy as a
   first-class, *calibrated* signal, not noise).

The blueprint's canonical illustration is the lighthouse: when the user calls
out for aid, one villager follows them into the storm and another flees. Same
event, divergent autonomous reactions. :meth:`NPCDirector.react` reproduces that
texture.

Everything here is deterministic given a seed (all randomness flows through a
single ``random.Random``) and runs with no network. The optional
:class:`~ana.llm.gateway.ModelGateway` may colour persona text and reactions when
``use_llm`` is on and the gateway is available, but the deterministic path is
always complete on its own.
"""
from __future__ import annotations

import random
from typing import Optional

from ..contracts import (
    Archetype,
    IdentitySnapshot,
    NPC,
    Scene,
    SceneBeat,
    UserAction,
    WorldState,
)
from ..llm.gateway import ModelGateway


# ─────────────────────────────────────────────────────────────
# Archetype library — behaviour, voice and reaction tendencies
# ─────────────────────────────────────────────────────────────
#
# A small "character bible" the director draws on so that each archetype reads
# distinctly. `persona` templates give evocative flavour; `helps`/`flees` biases
# steer autonomous reactions; `voice` seeds short reaction verbs.

_ARCHETYPE_LIB: dict[Archetype, dict] = {
    Archetype.SHADOW: {
        "names": ["Mara", "the Hollow Man", "Veil", "Corren", "the Other"],
        "persona": "a figure who wears the user's unspoken fears like a cloak",
        "help_bias": -0.4,
        "disposition": -0.3,
        "reactions": [
            "{name} watches without moving, as if measuring what it costs you",
            "{name} mutters that you will fail, and means it",
            "{name} mirrors your gesture back, mockingly",
        ],
    },
    Archetype.MENTOR: {
        "names": ["Old Ines", "Brother Haral", "the Keeper", "Sefa", "Tolan"],
        "persona": "a weathered guide who speaks in questions more than answers",
        "help_bias": 0.6,
        "disposition": 0.5,
        "reactions": [
            "{name} nods slowly and offers a single piece of hard-won counsel",
            "{name} steps closer, ready to steady your hand",
            "{name} asks the one question you were avoiding",
        ],
    },
    Archetype.TRICKSTER: {
        "names": ["Pip", "the Grinning Stranger", "Riss", "Nix", "the Ferryman"],
        "persona": "a quick-tongued opportunist who finds the joke in any disaster",
        "help_bias": 0.0,
        "disposition": 0.1,
        "reactions": [
            "{name} laughs and does the opposite of what you asked",
            "{name} offers help, for a price named later",
            "{name} turns the moment sideways with a sly suggestion",
        ],
    },
    Archetype.ALLY: {
        "names": ["Dren", "Marisol", "the Fisher", "Alba", "Tomas"],
        "persona": "a steady companion who throws in their lot with yours",
        "help_bias": 0.5,
        "disposition": 0.4,
        "reactions": [
            "{name} falls in beside you without needing to be asked",
            "{name} shoulders part of the load and keeps pace",
            "{name} answers your call and moves to act",
        ],
    },
    Archetype.THRESHOLD_GUARDIAN: {
        "names": ["the Warden", "Gaelle", "the Toll-keeper", "Sern", "the Door"],
        "persona": "one who stands at the edge of change and tests who may pass",
        "help_bias": -0.1,
        "disposition": 0.0,
        "reactions": [
            "{name} blocks the way and demands to know your purpose",
            "{name} weighs you with a long, cold look before deciding",
            "{name} names the price of passage",
        ],
    },
    Archetype.HERALD: {
        "names": ["the Messenger", "Wren", "the Rider", "Calla", "the Bell"],
        "persona": "a bearer of news that changes everything the moment it lands",
        "help_bias": 0.2,
        "disposition": 0.2,
        "reactions": [
            "{name} delivers word that reframes what you just did",
            "{name} points toward something you had not seen",
            "{name} announces your act to others nearby",
        ],
    },
    Archetype.HERO: {
        "names": ["Kiran", "the Champion", "Edda", "Roan", "the Volunteer"],
        "persona": "one who runs toward danger as if daring it to answer",
        "help_bias": 0.7,
        "disposition": 0.3,
        "reactions": [
            "{name} charges ahead of you, taking the first risk",
            "{name} rallies others to match your resolve",
            "{name} meets your action and raises it",
        ],
    },
    Archetype.SELF: {
        "names": ["the Quiet One", "Echo", "the Witness", "Anam"],
        "persona": "a serene presence that seems to already know how this ends",
        "help_bias": 0.3,
        "disposition": 0.3,
        "reactions": [
            "{name} simply watches, and somehow that is enough",
            "{name} reflects your choice back with uncanny calm",
        ],
    },
    Archetype.ANIMA: {
        "names": ["Liraine", "the Dreamer", "Soléa", "the Singer"],
        "persona": "a figure who stirs what the user keeps buried and tender",
        "help_bias": 0.3,
        "disposition": 0.2,
        "reactions": [
            "{name} responds to something beneath your words",
            "{name} softens, or sharpens, the feeling in the room",
        ],
    },
    Archetype.ANIMUS: {
        "names": ["Dorian", "the Strategist", "Varo", "the Builder"],
        "persona": "a figure of structure and resolve the user is learning to wield",
        "help_bias": 0.4,
        "disposition": 0.2,
        "reactions": [
            "{name} cuts your action down to its practical core",
            "{name} proposes a sharper plan",
        ],
    },
}

# Default fallback flavour for any archetype not elaborated above.
_DEFAULT_LIB = {
    "names": ["a stranger", "a passer-by", "someone nearby"],
    "persona": "a presence with purposes of their own",
    "help_bias": 0.0,
    "disposition": 0.0,
    "reactions": ["{name} reacts in their own way", "{name} responds, unpredictably"],
}


# ─────────────────────────────────────────────────────────────
# Context-aware entropy templates, keyed by location / theme cues
# ─────────────────────────────────────────────────────────────
#
# Each template is (text, symbol). The director picks a bucket that matches the
# world/scene before sampling, so the unplanned event fits the place.

_ENTROPY_BY_CUE: dict[str, list[tuple[str, Optional[str]]]] = {
    "coast": [
        ("A sudden squall rolls off the water; the light turns the colour of slate.", "storm"),
        ("The tide drags something half-buried into view — a weathered key.", "key"),
        ("A gull wheels low and drops a scrap of torn sailcloth at your feet.", "omen"),
    ],
    "forest": [
        ("The wind stops all at once, and the woods hold their breath.", "stillness"),
        ("A deer steps onto the path, unafraid, then is gone between the trunks.", "guide"),
        ("You find a cairn of stones no one admits to building.", "threshold"),
    ],
    "town": [
        ("A door bangs open somewhere and a voice calls a name that isn't yours.", "summons"),
        ("A coin glints in the gutter — older than any still minted here.", "coin"),
        ("The crowd parts for a moment and you glimpse a familiar face that cannot be here.", "double"),
    ],
    "interior": [
        ("A lamp gutters, flares, and settles — as if something passed.", "flame"),
        ("You notice a letter on the table that was not there a breath ago.", "letter"),
        ("A draft carries the scent of somewhere you have never been.", "memory"),
    ],
    "night": [
        ("A star falls, too slow, and leaves the dark feeling watched.", "omen"),
        ("Far off, a single light answers a light you did not know you'd shown.", "beacon"),
    ],
    "generic": [
        ("The air shifts without warning; something in the scene is subtly wrong.", "entropy"),
        ("A small object turns up where nothing should be — and it seems meant for you.", "gift"),
        ("Someone nearby does the unexpected, and the moment tilts.", "surprise"),
    ],
}


class NPCDirector:
    """Deterministic NPC + entropy layer implementing ``NPCEntropyLayer``.

    See the module docstring for the blueprint rationale. All randomness is drawn
    from a single seeded ``random.Random`` so a run is replayable; the optional
    gateway only ever *adds* colour and is never required.
    """

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        seed: int = 42,
        entropy_prob: float = 0.3,
        use_llm: bool = False,
    ) -> None:
        self.gateway = gateway
        self.seed = seed
        self.entropy_prob = max(0.0, min(1.0, entropy_prob))
        self.use_llm = use_llm
        self.rng = random.Random(seed)

    # -- helpers ---------------------------------------------------------
    def _llm_ready(self) -> bool:
        return bool(self.use_llm and self.gateway and self.gateway.available)

    def _lib(self, arch: Archetype) -> dict:
        return _ARCHETYPE_LIB.get(arch, _DEFAULT_LIB)

    def _unique_name(self, arch: Archetype, taken: set[str]) -> str:
        """Pick a fresh name for an archetype, avoiding collisions."""
        candidates = list(self._lib(arch)["names"])
        self.rng.shuffle(candidates)
        for name in candidates:
            if name not in taken:
                return name
        # Exhausted the library — synthesise a distinct one.
        n = 2
        base = candidates[0] if candidates else arch.value
        while f"{base} {n}" in taken:
            n += 1
        return f"{base} {n}"

    def _make_persona(self, arch: Archetype, represents: Optional[str]) -> str:
        """Deterministic persona, optionally enriched by the gateway."""
        base = self._lib(arch)["persona"]
        if represents:
            base = f"{base}; an embodiment of '{represents}'"
        if self._llm_ready():
            try:
                text = self.gateway.complete(  # type: ignore[union-attr]
                    system=(
                        "You write one vivid sentence (<=20 words) describing a "
                        "story NPC. No name, no quotes, just the description."
                    ),
                    user=f"Archetype: {arch.value}. Core idea: {base}.",
                )
                if text:
                    return text.strip().split("\n")[0][:160]
            except Exception:
                pass
        return base

    # -- NPCEntropyLayer protocol ---------------------------------------
    def populate(self, world: WorldState, identity: IdentitySnapshot) -> WorldState:
        """Seed a small cast (1-3) of NPCs for this world and identity.

        Builds *mirror* NPCs that personify identity elements — a ``SHADOW`` for
        a low or conflicted trait, a ``MENTOR`` for a live goal or theme — and at
        least one autonomous *world* NPC (high ``autonomy``, ``represents=None``)
        so the world is more than a reflection of the user. Existing NPCs
        (matched by name) are never duplicated.
        """
        existing_names = {n.name for n in world.npcs}
        desired: list[NPC] = []

        # 1. SHADOW mirror for the weakest / most conflicted trait, if any.
        if identity.traits:
            weak = min(identity.traits, key=lambda t: t.value)
            if weak.value <= 0.45:
                desired.append(
                    self._build_npc(
                        Archetype.SHADOW,
                        existing_names,
                        represents=weak.name,
                        disposition=-0.3,
                        autonomy=0.3,
                    )
                )

        # 2. MENTOR mirror for a goal or an active theme, if any.
        focus: Optional[str] = None
        if identity.goals:
            focus = identity.goals[0].description
        elif identity.active_themes:
            focus = identity.active_themes[0]
        if focus is not None:
            desired.append(
                self._build_npc(
                    Archetype.MENTOR,
                    existing_names,
                    represents=focus,
                    disposition=0.5,
                    autonomy=0.4,
                )
            )

        # 3. At least one fully autonomous "world" NPC for realism.
        world_arch = self.rng.choice(
            [Archetype.ALLY, Archetype.TRICKSTER, Archetype.THRESHOLD_GUARDIAN, Archetype.HERALD]
        )
        desired.append(
            self._build_npc(
                world_arch,
                existing_names,
                represents=None,
                disposition=self._lib(world_arch)["disposition"],
                autonomy=round(self.rng.uniform(0.7, 0.95), 2),
            )
        )

        # Keep the cast small (1-3 new) and deterministic.
        if len(desired) > 3:
            desired = desired[:3]

        world.npcs = list(world.npcs) + desired
        return world

    def _build_npc(
        self,
        arch: Archetype,
        taken: set[str],
        represents: Optional[str],
        disposition: float,
        autonomy: float,
    ) -> NPC:
        name = self._unique_name(arch, taken)
        taken.add(name)
        return NPC(
            name=name,
            archetype=arch,
            disposition=round(max(-1.0, min(1.0, disposition)), 2),
            autonomy=round(max(0.0, min(1.0, autonomy)), 2),
            persona=self._make_persona(arch, represents),
            represents=represents,
        )

    def inject_entropy(self, scene: Scene, world: WorldState) -> Scene:
        """Possibly append one unplanned, context-aware ``event`` beat.

        With probability ``entropy_prob`` the director samples a single event
        from the bucket best matching the world's location and the scene's theme,
        so the surprise *belongs* to the place (controlled entropy, not noise).
        Otherwise the scene is returned untouched.
        """
        if self.rng.random() >= self.entropy_prob:
            return scene

        bucket = self._entropy_bucket(world, scene)
        text, symbol = self.rng.choice(bucket)

        beat = SceneBeat(kind="event", text=text, symbol=symbol)
        scene.beats = list(scene.beats) + [beat]
        if symbol and symbol not in scene.symbols:
            scene.symbols = list(scene.symbols) + [symbol]
        return scene

    def _entropy_bucket(self, world: WorldState, scene: Scene) -> list[tuple[str, Optional[str]]]:
        cue = f"{world.location} {scene.theme} {scene.title} {world.ambience}".lower()
        coast_words = ("coast", "sea", "harbor", "harbour", "shore", "lighthouse", "tide", "port", "beach", "dock")
        forest_words = ("forest", "wood", "grove", "tree", "wild", "trail", "mountain")
        town_words = ("town", "city", "market", "street", "village", "square", "tavern")
        interior_words = ("room", "house", "hall", "chamber", "cabin", "indoor", "interior", "home")

        if any(w in cue for w in coast_words):
            return _ENTROPY_BY_CUE["coast"]
        if any(w in cue for w in forest_words):
            return _ENTROPY_BY_CUE["forest"]
        if any(w in cue for w in town_words):
            return _ENTROPY_BY_CUE["town"]
        if any(w in cue for w in interior_words):
            return _ENTROPY_BY_CUE["interior"]
        if "night" in cue or world.time_of_day in ("night", "dusk", "midnight"):
            return _ENTROPY_BY_CUE["night"]
        return _ENTROPY_BY_CUE["generic"]

    def react(self, scene: Scene, action: UserAction, world: WorldState) -> list[str]:
        """Return 1-3 short strings of how NPCs respond to the user's action.

        Each present NPC's response is driven by its archetype, disposition and
        ``autonomy``: high-autonomy NPCs may act *against* their disposition
        (the lighthouse texture — one villager follows, another flees), while
        mirror NPCs stay closer to type. Deterministic via the shared rng; the
        gateway may rephrase a line when enabled and available.
        """
        present = [n for n in world.npcs if n.alive]
        if not present:
            return []

        # Choose up to 3 reacting NPCs, prioritising variety of archetype.
        self.rng.shuffle(present)
        reactors = present[: min(3, len(present))]
        if not reactors:
            reactors = present[:1]

        reactions: list[str] = []
        for npc in reactors:
            reactions.append(self._react_one(npc, action))
        return reactions[:3] if reactions else [self._react_one(present[0], action)]

    def _react_one(self, npc: NPC, action: UserAction) -> str:
        lib = self._lib(npc.archetype)
        # Base inclination to help, blending disposition and archetype bias.
        help_score = 0.5 + 0.5 * npc.disposition + 0.5 * lib.get("help_bias", 0.0)
        # Autonomy injects unpredictability: the more autonomous, the more an NPC
        # may defy its own inclination (the villager who flees the rescue call).
        swing = (self.rng.random() - 0.5) * 2.0 * npc.autonomy
        help_score += swing

        if help_score >= 0.55:
            template = self.rng.choice(lib["reactions"])
            line = template.format(name=npc.name)
        elif help_score <= 0.2:
            # Defection / retreat — autonomous pushback.
            if npc.autonomy >= 0.6:
                line = f"{npc.name} turns and flees, wanting no part of it"
            else:
                line = f"{npc.name} hesitates and holds back, unwilling to commit"
        else:
            template = self.rng.choice(lib["reactions"])
            line = template.format(name=npc.name)

        if self._llm_ready():
            try:
                text = self.gateway.complete(  # type: ignore[union-attr]
                    system=(
                        "Rewrite the NPC reaction as one short, vivid sentence "
                        "(<=18 words). Keep the NPC's name and intent. No quotes."
                    ),
                    user=(
                        f"NPC {npc.name} ({npc.archetype.value}, {npc.persona}). "
                        f"User action: {action.raw}. Reaction: {line}."
                    ),
                )
                if text:
                    return text.strip().split("\n")[0][:160]
            except Exception:
                pass
        return line
