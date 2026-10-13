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

3. **A persistent cast with memory.** Every NPC enters a registry that outlives
   the cycle (:class:`CastMember`): how often they were met, how they reacted
   (answered the call / fled), how the protagonist treated them (helped,
   followed, confronted, ignored, fled — read from the action text) and the
   resulting disposition drift, plus a *story role* (mentor, shadow, ally,
   trickster, guardian, herald). Roles already filled are re-used, so the same
   faces recur instead of a new stranger every cycle.
4. **Deliberate callbacks.** Given the Hero's-Journey stage
   (``populate(..., stage=...)``), the late stages bring earlier people back on
   purpose: the Mentor returns and the Shadow must be faced at the *ordeal*, an
   early ally helps at the *reward*, the villager who fled when you called comes
   back asking for trust (the blueprint's lighthouse aftermath), and someone from
   the *call* witnesses the *return*. :meth:`NPCDirector.cast_summary` exposes the
   whole cast for debriefs and viewers.

Everything here is deterministic given a seed (all randomness flows through a
single ``random.Random``) and runs with no network. The optional
:class:`~ana.llm.gateway.ModelGateway` may colour persona text and reactions when
``use_llm`` is on and the gateway is available, but the deterministic path is
always complete on its own.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Optional

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
        ("Someone nearby does the unexpected, and the moment tilts.", "the unexpected turn"),
    ],
}


# ─────────────────────────────────────────────────────────────
# Persistent cast — story roles, relationships, callbacks
# ─────────────────────────────────────────────────────────────

STORY_ROLES: tuple[str, ...] = ("mentor", "shadow", "ally", "trickster", "guardian", "herald")

_ROLE_BY_ARCH: dict[Archetype, str] = {
    Archetype.MENTOR: "mentor",
    Archetype.SELF: "mentor",
    Archetype.SHADOW: "shadow",
    Archetype.ALLY: "ally",
    Archetype.HERO: "ally",
    Archetype.ANIMA: "ally",
    Archetype.ANIMUS: "ally",
    Archetype.TRICKSTER: "trickster",
    Archetype.THRESHOLD_GUARDIAN: "guardian",
    Archetype.HERALD: "herald",
}

_STAGES = ("call", "threshold", "mentor", "ordeal", "reward", "return")

_STAGE_PHRASE: dict[str, str] = {
    "call": "at the very start, when the call first came",
    "threshold": "at the threshold",
    "mentor": "when you were first being guided",
    "ordeal": "in the ordeal",
    "reward": "at the reward",
    "return": "on the road home",
}


def _norm_stage(stage: Optional[str]) -> Optional[str]:
    if not stage:
        return None
    s = str(stage).strip().lower()
    if s in _STAGES:
        return s
    for key in _STAGES:
        if key in s:
            return key
    return None


# How the protagonist treated someone, read from free action text. Checked in
# order: retreat words win over engagement words ("I refuse to help" is a
# refusal), confrontation before help ("I face him and help" is a facing).
_TREATMENT_WORDS: list[tuple[str, tuple[str, ...]]] = [
    ("fled", ("flee", "fled", "run away", "run from", "i run", "hide", "escape",
              "retreat", "turn back", "back away", "abandon")),
    ("ignored", ("ignore", "refuse", "walk past", "walk away", "dismiss", "avoid",
                 "leave them", "leave him", "leave her", "turn away", "hesitate")),
    ("confronted", ("confront", "face", "challenge", "stand up to", "stand against",
                    "argue", "defy", "fight")),
    ("followed", ("follow", "listen", "heed", "accept", "trust", "ask", "take the advice",
                  "take their advice", "call for help", "learn from")),
    ("helped", ("help", "save", "protect", "rescue", "share", "give", "offer",
                "comfort", "carry", "lead", "organize", "organise", "thank", "shelter",
                "answer the call", "stay with", "support")),
]

# Disposition drift per treatment, by story role (default row first).
_DRIFT: dict[str, dict[str, float]] = {
    "default": {"helped": 0.15, "followed": 0.12, "confronted": -0.08,
                "ignored": -0.08, "fled": -0.12},
    # Facing the Shadow integrates it: it grows less hostile. Fleeing feeds it.
    "shadow": {"helped": 0.05, "followed": -0.05, "confronted": 0.15,
               "ignored": -0.1, "fled": -0.15},
    # Guardians respect being met head-on.
    "guardian": {"helped": 0.1, "followed": 0.08, "confronted": 0.06,
                 "ignored": -0.1, "fled": -0.12},
    "mentor": {"helped": 0.12, "followed": 0.15, "confronted": -0.04,
               "ignored": -0.12, "fled": -0.1},
}


def classify_action(text: str) -> Optional[str]:
    """Read how an action treats others: helped / followed / confronted /
    ignored / fled, or ``None`` for a neutral action ("I explore")."""
    t = f" {text.lower()} "
    for kind, words in _TREATMENT_WORDS:
        if any(w in t for w in words):
            return kind
    return None


@dataclass
class CastMember:
    """A registry entry: one NPC's life across the whole journey."""

    npc: NPC
    role: str
    first_cycle: int
    first_stage: Optional[str]
    last_cycle: int
    disposition_start: float
    managed: bool = True                  # created by the director (may rotate)
    times_met: int = 0                    # cycles present in the world
    last_met: int = -1
    interactions: int = 0                 # times they reacted to the protagonist
    helped_you: int = 0                   # times they answered your call
    fled: int = 0                         # times they fled / refused you
    faced: bool = False                   # a Shadow the protagonist confronted
    treatment: dict[str, int] = field(default_factory=dict)
    callbacks: list[str] = field(default_factory=list)       # stages they returned at
    callback_kinds: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.npc.name

    @property
    def disposition(self) -> float:
        return self.npc.disposition

    @property
    def trend(self) -> str:
        d = self.npc.disposition - self.disposition_start
        if d >= 0.1:
            return "warmer"
        if d <= -0.1:
            return "cooler"
        return "steady"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "archetype": self.npc.archetype.value,
            "role": self.role,
            "represents": self.npc.represents,
            "times_met": self.times_met,
            "interactions": self.interactions,
            "first_cycle": self.first_cycle,
            "first_stage": self.first_stage,
            "last_cycle": self.last_cycle,
            "disposition_start": round(self.disposition_start, 2),
            "disposition": round(self.npc.disposition, 2),
            "trend": self.trend,
            "helped_you": self.helped_you,
            "fled": self.fled,
            "faced": self.faced,
            "treatment": dict(self.treatment),
            "callbacks": list(self.callbacks),
            "alive": self.npc.alive,
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
        *,
        max_present: int = 5,
        newcomer_prob: float = 0.25,
    ) -> None:
        self.gateway = gateway
        self.seed = seed
        self.entropy_prob = max(0.0, min(1.0, entropy_prob))
        self.use_llm = use_llm
        self.rng = random.Random(seed)
        self.max_present = max(1, max_present)
        self.newcomer_prob = max(0.0, min(1.0, newcomer_prob))
        # The persistent cast registry (name -> CastMember), insertion-ordered.
        self._cast: dict[str, CastMember] = {}
        self._cycle = 0                       # populate() calls so far
        self._stage: Optional[str] = None     # stage given to the last populate()
        self._pending: list[tuple[str, str, str]] = []   # (name, kind, stage)
        self._callback_scenes: set[str] = set()

    # -- registry API ----------------------------------------------------
    @property
    def cast(self) -> dict[str, "CastMember"]:
        """The live registry (name -> :class:`CastMember`)."""
        return self._cast

    def member(self, name: str) -> Optional["CastMember"]:
        return self._cast.get(name)

    def cast_summary(self) -> list[dict]:
        """Everyone met so far, earliest first, as plain dicts for debriefs and
        viewers: name, archetype, role, represents, times_met, interactions,
        first_cycle/first_stage/last_cycle, disposition_start/disposition,
        trend (warmer/cooler/steady), helped_you, fled, faced, treatment counts,
        callbacks (stages they returned at), alive."""
        members = sorted(self._cast.values(), key=lambda m: (m.first_cycle, list(self._cast).index(m.name)))
        return [m.to_dict() for m in members]

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

    # -- cast registry helpers -------------------------------------------
    def _register(self, npc: NPC, stage: Optional[str], managed: bool) -> "CastMember":
        m = self._cast.get(npc.name)
        if m is None:
            m = CastMember(
                npc=npc,
                role=_ROLE_BY_ARCH.get(npc.archetype, "ally"),
                first_cycle=self._cycle,
                first_stage=stage,
                last_cycle=self._cycle,
                disposition_start=npc.disposition,
                managed=managed,
            )
            self._cast[npc.name] = m
        elif m.npc is not npc:
            # Same name, a fresh object (e.g. a reloaded world): keep the
            # registry's evolved disposition authoritative.
            npc.disposition = m.npc.disposition
            m.npc = npc
        return m

    def _find(self, role: str, represents: Optional[str] = None,
              exclude: Iterable[str] = ()) -> Optional["CastMember"]:
        """Earliest-met cast member in `role` (optionally matching `represents`)."""
        ex = set(exclude)
        pool = [m for m in self._cast.values()
                if m.role == role and m.npc.alive and m.name not in ex]
        if represents is not None:
            exact = [m for m in pool if m.npc.represents == represents]
            pool = exact or pool
        return min(pool, key=lambda m: m.first_cycle) if pool else None

    @staticmethod
    def _ensure_present(world: WorldState, npc: NPC) -> None:
        if all(n is not npc and n.name != npc.name for n in world.npcs):
            world.npcs = list(world.npcs) + [npc]

    # -- NPCEntropyLayer protocol ---------------------------------------
    def populate(self, world: WorldState, identity: IdentitySnapshot,
                 stage: Optional[str] = None) -> WorldState:
        """Seed / maintain the cast for this world and identity.

        Builds *mirror* NPCs that personify identity elements — a ``SHADOW`` for
        a low or conflicted trait, a ``MENTOR`` for a live goal or theme — and at
        least one autonomous *world* NPC (high ``autonomy``, ``represents=None``)
        so the world is more than a reflection of the user.

        The cast is **persistent**: every NPC goes into a registry that outlives
        the cycle, and a role that is already filled (the Shadow of
        ``leadership_confidence``, the Mentor of "find purpose") is *re-used*
        rather than re-cast, so the same faces recur and their relationship with
        the protagonist can evolve. Existing NPCs (matched by name) are never
        duplicated, and at most ``max_present`` registry-managed NPCs stand in
        the world at once (newcomers rotate older bystanders off-stage; they stay
        in the registry for later callbacks).

        ``stage`` (a Hero's-Journey key: call/threshold/mentor/ordeal/reward/
        return) is optional. ``None`` keeps the stage-agnostic behaviour. When
        it is ``ordeal``, ``reward`` or ``return`` the director plans deliberate
        **callbacks**: the Mentor returns and the Shadow must be faced at the
        ordeal; an early ally helps at the reward; the one who fled when you
        called comes back asking for trust; someone from the call witnesses the
        return. Callback NPCs are re-added and moved to the front of
        ``world.npcs`` (so the generator voices them first), and their beats are
        appended to the scene by :meth:`inject_entropy` (automatically) or by
        :meth:`inject_callback` (explicitly) — each callback is announced once.
        """
        self._cycle += 1
        st = _norm_stage(stage)
        self._stage = st
        self._pending = []

        # Anything already in the world joins the registry (externally placed
        # NPCs are tracked but never rotated out).
        for n in list(world.npcs):
            self._register(n, st, managed=n.name in self._cast and self._cast[n.name].managed)

        existing_names = {n.name for n in world.npcs} | set(self._cast)
        added: list[NPC] = []

        # 1. SHADOW mirror for the weakest / most conflicted trait, if any.
        if identity.traits:
            weak = min(identity.traits, key=lambda t: t.value)
            if weak.value <= 0.45:
                m = self._find("shadow", represents=weak.name)
                if m is not None and m.npc.represents in (weak.name, None):
                    self._ensure_present(world, m.npc)
                else:
                    added.append(self._build_npc(
                        Archetype.SHADOW, existing_names, represents=weak.name,
                        disposition=-0.3, autonomy=0.3))

        # 2. MENTOR mirror for a goal or an active theme, if any.
        focus: Optional[str] = None
        if identity.goals:
            focus = identity.goals[0].description
        elif identity.active_themes:
            focus = identity.active_themes[0]
        if focus is not None:
            m = self._find("mentor")
            if m is not None:
                self._ensure_present(world, m.npc)
            else:
                added.append(self._build_npc(
                    Archetype.MENTOR, existing_names, represents=focus,
                    disposition=0.5, autonomy=0.4))

        # 3. At least one fully autonomous "world" NPC for realism; now and then a
        #    newcomer arrives, so the world keeps its own life.
        world_present = [n for n in world.npcs
                         if n.represents is None and n.autonomy >= 0.6 and n.alive]
        if not world_present or self.rng.random() < self.newcomer_prob:
            world_arch = self.rng.choice(
                [Archetype.ALLY, Archetype.TRICKSTER, Archetype.THRESHOLD_GUARDIAN, Archetype.HERALD]
            )
            added.append(self._build_npc(
                world_arch, existing_names, represents=None,
                disposition=self._lib(world_arch)["disposition"],
                autonomy=round(self.rng.uniform(0.7, 0.95), 2)))

        added = added[:3]
        world.npcs = list(world.npcs) + added
        for n in added:
            self._register(n, st, managed=True)

        # Deliberate callbacks for the late stages.
        callback_names = self._plan_callbacks(world, st) if st else []

        self._trim(world, keep=set(callback_names) | {n.name for n in added})

        # Callback NPCs speak first.
        if callback_names:
            front = [n for nm in callback_names for n in world.npcs if n.name == nm]
            rest = [n for n in world.npcs if n.name not in callback_names]
            world.npcs = front + rest

        for n in world.npcs:
            m = self._cast.get(n.name)
            if m is not None and m.last_met != self._cycle:
                m.times_met += 1
                m.last_met = self._cycle
                m.last_cycle = self._cycle
        return world

    def _trim(self, world: WorldState, keep: set[str]) -> None:
        """Rotate the oldest managed bystanders off-stage beyond ``max_present``."""
        managed = [n for n in world.npcs
                   if n.name in self._cast and self._cast[n.name].managed]
        excess = len(managed) - self.max_present
        if excess <= 0:
            return
        removable = [n for n in managed if n.name not in keep
                     and self._cast[n.name].role not in ("mentor", "shadow")]
        removable.sort(key=lambda n: (self._cast[n.name].last_cycle,
                                      self._cast[n.name].first_cycle))
        drop = {n.name for n in removable[:excess]}
        world.npcs = [n for n in world.npcs if n.name not in drop]

    # -- callbacks -------------------------------------------------------
    def _plan_callbacks(self, world: WorldState, stage: Optional[str]) -> list[str]:
        """Choose who returns at this stage; queue their (one-time) beats."""
        if stage not in ("ordeal", "reward", "return"):
            return []
        earlier = [m for m in self._cast.values()
                   if m.first_cycle < self._cycle and m.npc.alive]
        plan: list[tuple[CastMember, str]] = []
        used: set[str] = set()

        def pick(cands: list[CastMember]) -> Optional[CastMember]:
            cands = [c for c in cands if c.name not in used]
            return min(cands, key=lambda m: (m.first_cycle, m.name)) if cands else None

        if stage == "ordeal":
            mentor = pick([m for m in earlier if m.role == "mentor"])
            if mentor:
                plan.append((mentor, "mentor_returns")); used.add(mentor.name)
            shadow = pick([m for m in earlier if m.role == "shadow"])
            if shadow:
                plan.append((shadow, "shadow_faced")); used.add(shadow.name)
        if stage in ("reward", "return"):
            fled = pick([m for m in earlier if m.fled and m.role != "shadow"
                         and "trust_issue" not in m.callback_kinds])
            if fled:
                plan.append((fled, "trust_issue")); used.add(fled.name)
        if stage == "reward":
            allies = [m for m in earlier if m.role not in ("shadow", "mentor")
                      and (m.role == "ally" or m.helped_you or m.disposition > 0.3)
                      and m.npc.disposition > 0.0]
            ally = pick(allies)
            if ally:
                plan.append((ally, "ally_helps")); used.add(ally.name)
        if stage == "return":
            from_call = [m for m in earlier if m.role != "shadow"
                         and (m.first_stage == "call"
                              or (m.first_stage is None and m.first_cycle == 1))]
            witness = pick(from_call) or pick([m for m in earlier if m.role != "shadow"])
            if witness:
                plan.append((witness, "witness")); used.add(witness.name)

        names: list[str] = []
        for m, kind in plan:
            self._ensure_present(world, m.npc)
            names.append(m.name)
            if stage not in m.callbacks:
                m.callbacks.append(stage)
            if kind not in m.callback_kinds:
                m.callback_kinds.append(kind)
                self._pending.append((m.name, kind, stage))
        return names

    def inject_callback(self, scene: Scene, world: WorldState,
                        stage: Optional[str] = None) -> Scene:
        """Append the beats for this cycle's callbacks to ``scene``.

        Uses the callbacks planned by the last :meth:`populate` call; passing a
        ``stage`` different from the one ``populate`` saw (or calling without a
        prior staged ``populate``) plans them now. Idempotent per scene: a scene
        never receives the same callbacks twice, so it is safe to call this and
        then :meth:`inject_entropy` (which also flushes pending callbacks).
        """
        st = _norm_stage(stage) if stage is not None else self._stage
        if st is not None and st != self._stage:
            self._stage = st
            self._plan_callbacks(world, st)
        if not self._pending or scene.id in self._callback_scenes:
            return scene
        new_beats: list[SceneBeat] = []
        for name, kind, cb_stage in self._pending:
            m = self._cast.get(name)
            if m is None:
                continue
            new_beats.append(self._callback_beat(m, kind))
        self._pending = []
        self._callback_scenes.add(scene.id)
        if new_beats:
            scene.beats = list(scene.beats) + new_beats
        return scene

    def _callback_beat(self, m: "CastMember", kind: str) -> SceneBeat:
        when = _STAGE_PHRASE.get(m.first_stage or "", "earlier on the road")
        helped_before = m.treatment.get("helped", 0) + m.treatment.get("followed", 0)
        if kind == "mentor_returns":
            text = (f"{m.name}, who first met you {when}, is here again where it is "
                    f"hardest. \"I said we'd meet once more. You already carry what "
                    f"you need — use it.\"")
            sym = "the mentor's return"
        elif kind == "shadow_faced":
            text = (f"{m.name} steps out of the dark and, this time, does not move "
                    f"aside. \"No more going around me. Whatever you do next, you "
                    f"do it facing me.\"")
            sym = "the shadow"
        elif kind == "ally_helps":
            why = ("You helped me once. Let me return it." if helped_before
                   else "You let me walk with you. I'm still walking.")
            text = (f"{m.name}, who stood beside you {when}, arrives with exactly "
                    f"what is needed. \"{why}\"")
            sym = "the returning ally"
        elif kind == "trust_issue":
            text = (f"{m.name} — the one who fled when you called — comes back, eyes "
                    f"low. \"I ran. I know I did. Will you trust me to stand with "
                    f"you this time?\"")
            sym = "trust"
        else:  # witness
            text = (f"{m.name}, who saw you {when}, is waiting at the edge of home "
                    f"to see who has come back.")
            sym = "the witness"
        return SceneBeat(kind="dialogue", text=text, speaker=m.name,
                         archetype=m.npc.archetype, symbol=sym)

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

        Any callbacks planned by a staged :meth:`populate` are flushed into the
        scene first (see :meth:`inject_callback`), so a loop that passes
        ``stage=`` to ``populate`` needs no extra call.
        """
        if self._pending:
            scene = self.inject_callback(scene, world)
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

        reactions: list[str] = []
        for npc in reactors:
            line, kind = self._react_one_kind(npc, action)
            reactions.append(line)
            self._note_reaction(npc, kind)
        self._drift(present, action)
        return reactions[:3]

    def _note_reaction(self, npc: NPC, kind: str) -> None:
        m = self._cast.get(npc.name) or self._register(npc, self._stage, managed=False)
        m.interactions += 1
        if kind == "help":
            m.helped_you += 1
        elif kind == "flee":
            m.fled += 1

    def _drift(self, present: list[NPC], action: UserAction) -> None:
        """Move each present NPC's disposition by how the protagonist treated
        them. NPCs named in the action feel it most; bystanders feel an echo."""
        kind = classify_action(action.raw or "")
        if kind is None:
            return
        low = (action.raw or "").lower()
        named = [n for n in present if n.name.lower() in low]
        for npc in present:
            m = self._cast.get(npc.name) or self._register(npc, self._stage, managed=False)
            table = _DRIFT.get(m.role, _DRIFT["default"])
            weight = 1.0 if not named else (1.5 if npc in named else 0.3)
            # Confronting a Shadow is the thing itself; confronting a bystander
            # who isn't the target barely registers.
            if kind == "confronted" and m.role == "shadow" and (not named or npc in named):
                m.faced = True
            delta = table[kind] * weight
            npc.disposition = round(max(-1.0, min(1.0, npc.disposition + delta)), 3)
            m.treatment[kind] = m.treatment.get(kind, 0) + 1

    def _react_one(self, npc: NPC, action: UserAction) -> str:
        return self._react_one_kind(npc, action)[0]

    def _react_one_kind(self, npc: NPC, action: UserAction) -> tuple[str, str]:
        """One NPC's reaction line plus its kind: ``help``, ``flee``/``hold`` or
        ``neutral``."""
        lib = self._lib(npc.archetype)
        # Base inclination to help, blending disposition and archetype bias.
        help_score = 0.5 + 0.5 * npc.disposition + 0.5 * lib.get("help_bias", 0.0)
        # Autonomy injects unpredictability: the more autonomous, the more an NPC
        # may defy its own inclination (the villager who flees the rescue call).
        swing = (self.rng.random() - 0.5) * 2.0 * npc.autonomy
        help_score += swing

        kind = "neutral"
        if help_score >= 0.55:
            template = self.rng.choice(lib["reactions"])
            line = template.format(name=npc.name)
            if lib.get("help_bias", 0.0) > 0.0:
                kind = "help"
        elif help_score <= 0.2:
            # Defection / retreat — autonomous pushback.
            if npc.autonomy >= 0.6:
                line = f"{npc.name} turns and flees, wanting no part of it"
                kind = "flee"
            else:
                line = f"{npc.name} hesitates and holds back, unwilling to commit"
                kind = "hold"
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
                    return text.strip().split("\n")[0][:160], kind
            except Exception:
                pass
        return line, kind
