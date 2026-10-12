"""The Generative Experience Engine — ANA's dream-maker.

This module is the blueprint's **Generative Experience Engine**: the imaginative
faculty that sits inside the recursive loop between the Identity Core ("Me") and
the Environment Mirror ("Myself"). Each cycle the Feedback Loop Engine hands it a
:class:`~ana.contracts.GenerationRequest` — a snapshot of who the protagonist is
right now, the state of the mirrored world, and a high-level directive — and the
engine *dreams the next scene* the protagonist will live through.

Per the blueprint, a scene is not a static template. It is an instantiation of an
**archetypal story pattern** (the Hero's Journey stages) selected to fit the
identity's active themes and goals, then coloured by the world's motifs, the
emotional valence of the moment, requested intensity, woven synchronicities, and
callbacks to recalled memory shards (continuity). The output must be deterministic
given a seed — so a journey can be replayed and inspected — yet genuinely varied.

Two paths produce a scene:

* **Deterministic path** (always available, no network): archetype selection +
  motif-driven prose assembled with a seeded ``random.Random``.
* **LLM path** (optional): if a :class:`~ana.llm.gateway.ModelGateway` is wired in
  and available, the engine asks it for a scene as JSON and maps it back. Any
  failure falls through to the deterministic path, so the simulation never
  hard-depends on the network.
"""
from __future__ import annotations

import random
from typing import Optional

from ..contracts import (
    Archetype,
    GenerationRequest,
    IdentitySnapshot,
    NPC,
    Scene,
    SceneBeat,
    SynchronicityHint,
    WorldState,
)
from ..llm.gateway import ModelGateway

# ─────────────────────────────────────────────────────────────
# Archetypal story patterns — the Hero's Journey skeleton
# ─────────────────────────────────────────────────────────────

# Each stage carries: the directive-ish "move" it realises, a verb-bank for the
# event beat, and the choice gestures it tends to offer. The engine selects a
# stage from the identity's themes/goals, then instantiates it with world motifs.
HERO_JOURNEY: dict[str, dict] = {
    "call": {
        "label": "the call to adventure",
        "event": "A summons arrives that cannot be unheard — {motif} marks the beginning of something.",
        "choices": ["Answer the call", "Hesitate at the edge", "Ask what it will cost"],
        "symbol": "the threshold-bell",
    },
    "threshold": {
        "label": "crossing the threshold",
        "event": "The way forward narrows to a single door, and {motif} waits on the far side.",
        "choices": ["Step across", "Turn back once more", "Leave a token behind"],
        "symbol": "the crossing",
    },
    "mentor": {
        "label": "meeting the mentor",
        "event": "A knowing presence offers what you did not know you needed, near {motif}.",
        "choices": ["Accept the gift", "Question the giver", "Offer something in return"],
        "symbol": "the given key",
    },
    "ordeal": {
        "label": "the ordeal",
        "event": "Everything you have been avoiding gathers here around {motif}, and will not be deferred.",
        "choices": ["Face it directly", "Find another way through", "Call for help"],
        "symbol": "the dark water",
    },
    "reward": {
        "label": "seizing the reward",
        "event": "What you came for lies within reach — {motif} glints with the thing itself.",
        "choices": ["Take it openly", "Take only what you need", "Share it at once"],
        "symbol": "the found thing",
    },
    "return": {
        "label": "the return",
        "event": "The road bends homeward, changed, and {motif} looks different than it did.",
        "choices": ["Carry it home", "Rest before the last mile", "Look back one time"],
        "symbol": "the long road",
    },
}

# Theme → the journey stage that best serves it. Falls back to a goal-horizon map.
THEME_STAGE: dict[str, str] = {
    "leadership": "ordeal",
    "courage": "ordeal",
    "trust": "mentor",
    "belonging": "mentor",
    "purpose": "call",
    "freedom": "threshold",
    "change": "threshold",
    "wisdom": "mentor",
    "healing": "return",
    "forgiveness": "return",
    "growth": "reward",
    "mastery": "reward",
    "exploration": "call",
}

# Weather/light register keyed to emotional valence, for the description beat.
_WARM = ["gold", "honeyed", "amber", "soft", "lit from within"]
_COOL = ["grey", "slate", "pale", "thin", "steel-blue"]
_NEUTRAL = ["even", "muted", "quiet", "level", "clear"]


class ExperienceEngine:
    """Deterministic-first implementation of the ``GenerativeEngine`` protocol.

    See the module docstring: this is the blueprint's Generative Experience
    Engine. Construct once per run with a seed so the whole journey is replayable.

    Args:
        gateway: optional model bridge. When present and ``available`` and
            ``use_llm`` is set, scenes are dreamed by the model (``source="llm"``).
        seed: seed for the engine's private ``random.Random``. All randomness is
            drawn from it — never the global ``random`` module — so runs reproduce.
        use_llm: master switch for the optional LLM path.
    """

    def __init__(self, gateway: ModelGateway | None = None, seed: int = 42,
                 use_llm: bool = True) -> None:
        self.gateway = gateway
        self.use_llm = use_llm
        self.rng = random.Random(seed)

    # -- public API ------------------------------------------------------
    def generate(self, request: GenerationRequest) -> Scene:
        """Dream a :class:`Scene` from a :class:`GenerationRequest`.

        Tries the LLM path when wired and permitted; otherwise, or on any
        failure/malformed response, builds the scene deterministically.
        """
        if self.use_llm and self.gateway is not None and self.gateway.available:
            scene = self._generate_llm(request)
            if scene is not None:
                return scene
        return self._generate_deterministic(request)

    # -- LLM path --------------------------------------------------------
    def _generate_llm(self, request: GenerationRequest) -> Optional[Scene]:
        """Ask the gateway for a scene as JSON and map it into a Scene."""
        system = (
            "You are ANA's Generative Experience Engine, the dream-maker in a "
            "recursive loop between an Identity Core and an Environment Mirror. "
            "Dream the next scene the protagonist lives through, as an "
            "instantiation of a Hero's Journey stage fitted to their themes. "
            "Return JSON with keys: title (str), summary (str), theme (str), "
            "intensity (number 0..1), beats (list of {kind, text, speaker?, "
            "symbol?} where kind is one of description|dialogue|event|choice|"
            "synchronicity), choices (list of str), symbols (list of str)."
        )
        user = self._llm_prompt(request)
        try:
            data = self.gateway.complete_json(system, user)
        except Exception:
            data = None
        if not isinstance(data, dict):
            return None
        try:
            return self._scene_from_json(data, request)
        except Exception:
            return None

    def _llm_prompt(self, request: GenerationRequest) -> str:
        idn, world = request.identity, request.world
        lines = [
            f"Directive: {request.directive}",
            f"Target theme: {request.target_theme or (idn.active_themes[0] if idn.active_themes else 'exploration')}",
            f"Target intensity (0..1): {request.target_intensity:.2f}",
            f"Active themes: {', '.join(idn.active_themes) or 'none'}",
            f"Goals: {', '.join(g.description for g in idn.goals) or 'none'}",
            f"Emotion valence/arousal/stress: {idn.emotion.valence:.2f}/{idn.emotion.arousal:.2f}/{idn.emotion.stress:.2f}",
            f"Location: {world.location}; time: {world.time_of_day}; weather: {world.weather}; ambience: {world.ambience}",
            f"Motifs present: {', '.join(world.motifs) or 'none'}",
            f"NPCs present: {', '.join(f'{n.name} ({n.archetype.value}: {n.persona})' for n in world.npcs) or 'none'}",
        ]
        if request.synchronicity:
            s = request.synchronicity
            lines.append(f"Weave a synchronicity — symbol '{s.symbol}', suggestion: {s.suggestion}")
        if request.recalled_shards:
            lines.append(f"Call back to these memories where fitting: {'; '.join(request.recalled_shards)}")
        if request.constraints:
            lines.append(f"Honour these constraints in tone: {'; '.join(request.constraints)}")
        return "\n".join(lines)

    def _scene_from_json(self, data: dict, request: GenerationRequest) -> Scene:
        raw_beats = data.get("beats") or []
        beats: list[SceneBeat] = []
        valid = {"description", "dialogue", "event", "choice", "synchronicity"}
        for b in raw_beats:
            if not isinstance(b, dict):
                continue
            kind = b.get("kind", "description")
            if kind not in valid:
                kind = "description"
            text = str(b.get("text", "")).strip()
            if not text:
                continue
            beats.append(SceneBeat(
                kind=kind, text=text,
                speaker=b.get("speaker") or None,
                symbol=b.get("symbol") or None,
            ))
        choices = [str(c) for c in (data.get("choices") or []) if str(c).strip()]
        symbols = [str(s) for s in (data.get("symbols") or []) if str(s).strip()]
        if not beats or not choices:
            # Malformed enough that we cannot honour the contract — fall back.
            raise ValueError("LLM scene missing beats or choices")
        try:
            intensity = float(data.get("intensity", request.target_intensity))
        except (TypeError, ValueError):
            intensity = request.target_intensity
        theme = str(data.get("theme") or self._pick_theme(request))
        return Scene(
            title=str(data.get("title") or theme.title()),
            summary=str(data.get("summary") or ""),
            beats=beats,
            theme=theme,
            intensity=max(0.0, min(1.0, intensity)),
            choices=choices[:4],
            symbols=symbols or list(request.world.motifs),
            source="llm",
        )

    # -- deterministic path ---------------------------------------------
    def _generate_deterministic(self, request: GenerationRequest) -> Scene:
        idn, world = request.identity, request.world
        theme = self._pick_theme(request)
        stage_key = self._pick_stage(theme, idn)
        stage = HERO_JOURNEY[stage_key]
        gentle = self._is_gentle(request)

        beats: list[SceneBeat] = []
        introduced: list[str] = []

        # 1) Setting — a description beat drawn from location/weather/motifs.
        beats.append(self._description_beat(world, idn, gentle))

        # 2) Dialogue — NPCs present, voiced per archetype/persona/represents.
        for npc in world.npcs[:2]:
            beats.append(self._dialogue_beat(npc, theme, stage, gentle))

        # 3) Synchronicity — a meaningful coincidence, if the engine was handed one.
        if request.synchronicity is not None:
            beats.append(self._synchronicity_beat(request.synchronicity))
            introduced.append(request.synchronicity.symbol)

        # 4) Memory callback — weave a recalled shard as continuity, when relevant.
        shard_beat = self._shard_beat(request.recalled_shards, theme)
        if shard_beat is not None:
            beats.append(shard_beat)

        # 5) Event — realise the directive / target theme via the chosen stage.
        motif = self._pick_motif(world)
        event_text = stage["event"].format(motif=motif)
        event_text = self._apply_directive(event_text, request.directive, theme, gentle)
        beats.append(SceneBeat(kind="event", text=event_text, symbol=stage["symbol"]))
        introduced.append(stage["symbol"])

        # Trim to the 3–6 beat band (description + event are always kept).
        beats = self._fit_beats(beats)

        choices = self._build_choices(stage, theme, gentle)
        intensity = self._pick_intensity(request.target_intensity, gentle)
        symbols = self._collect_symbols(world, introduced)
        title = self._make_title(stage, theme, motif)
        summary = self._make_summary(stage, theme, world, gentle)

        return Scene(
            title=title,
            summary=summary,
            beats=beats,
            theme=theme,
            intensity=intensity,
            choices=choices,
            symbols=symbols,
            source="deterministic",
        )

    # -- selection helpers ----------------------------------------------
    def _pick_theme(self, request: GenerationRequest) -> str:
        if request.target_theme:
            return request.target_theme
        themes = request.identity.active_themes
        if themes:
            return self.rng.choice(themes)
        return "exploration"

    # The Hero's Journey advances with inner-outer convergence: a divergent self
    # is still being called; a converging self is deep in the ordeal; a converged
    # self seizes the reward and returns. Theme only colors the stage, it no longer
    # pins it — so the arc actually moves across a journey.
    _ARC = ["call", "threshold", "mentor", "ordeal", "reward", "return"]

    def _pick_stage(self, theme: str, idn: IdentitySnapshot) -> str:
        conv = max(0.0, min(0.999, idn.convergence))
        idx = int(conv * len(self._ARC))
        # A life-goal still unmet keeps the very start honest; otherwise follow
        # convergence. Near-goals nudge one stage ahead toward the reward.
        if idx == 0 and any(g.horizon == "life" and g.progress < 0.2 for g in idn.goals):
            return "call"
        if any(g.horizon == "near" and g.progress >= 0.5 for g in idn.goals):
            idx = min(len(self._ARC) - 1, idx + 1)
        return self._ARC[idx]

    def _pick_motif(self, world: WorldState) -> str:
        if world.motifs:
            return self.rng.choice(world.motifs)
        return self.rng.choice(["a single lit window", "an open gate", "a held breath"])

    def _is_gentle(self, request: GenerationRequest) -> bool:
        blob = " ".join(request.constraints).lower()
        if any(k in blob for k in ("gentle", "hope", "soft", "calm", "safe", "ease")):
            return True
        return request.target_intensity < 0.35 or request.identity.emotion.stress > 0.6

    # -- beat builders ---------------------------------------------------
    def _description_beat(self, world: WorldState, idn: IdentitySnapshot,
                          gentle: bool) -> SceneBeat:
        v = idn.emotion.valence
        if gentle or v > 0.25:
            light = self.rng.choice(_WARM)
        elif v < -0.25:
            light = self.rng.choice(_COOL)
        else:
            light = self.rng.choice(_NEUTRAL)
        openers = [
            f"{world.location.capitalize()} holds the {world.time_of_day} in {light} light; the weather is {world.weather}.",
            f"You arrive at {world.location}. The air is {world.ambience}, the {world.time_of_day} {light}, the weather {world.weather}.",
            f"Here is {world.location}, {light} under a {world.weather} sky, the hour still {world.time_of_day}.",
        ]
        text = self.rng.choice(openers)
        if world.motifs:
            m = self.rng.choice(world.motifs)
            tails = [
                f" {m[0].upper() + m[1:]} draws the eye, as if it had been waiting.",
                f" Somewhere in it, {m} — small, insistent, meant for you.",
                f" You notice {m} before anything else.",
            ]
            text += self.rng.choice(tails)
        return SceneBeat(kind="description", text=text,
                         symbol=world.motifs[0] if world.motifs else None)

    def _dialogue_beat(self, npc: NPC, theme: str, stage: dict,
                       gentle: bool) -> SceneBeat:
        voice = self._voice_for(npc.archetype)
        represents = _humanize(npc.represents or theme, theme)
        persona = npc.persona or "unreadable"
        # Templates 0 and 2 append a noun phrase to the archetype's voice; template
        # 1 stands alone (it must NOT borrow `voice['open']`, which is a transitive
        # lead-in that only composes with a following noun).
        templates = [
            f"\"{voice['open']} {represents}. {voice['close']}\"",
            f"\"You stand at {stage['label']}, whether you name it or not.\"",
            f"\"They never tell you {voice['secret']} about {represents}.\"",
        ]
        line = self.rng.choice(templates)
        if gentle:
            line = line[:-1] + " But there is no hurry.\""
        text = f"{npc.name}, {persona}, speaks. {line}"
        return SceneBeat(kind="dialogue", text=text, speaker=npc.name,
                         archetype=npc.archetype)

    def _voice_for(self, archetype: Archetype) -> dict:
        voices = {
            Archetype.MENTOR: {"open": "You already carry", "close": "Trust the weight of it.", "secret": "how heavy a gift is"},
            Archetype.SHADOW: {"open": "You keep refusing", "close": "I am only what you buried.", "secret": "who you become in the dark"},
            Archetype.TRICKSTER: {"open": "Oh, you want", "close": "The rules are softer than you think.", "secret": "which door is painted on"},
            Archetype.THRESHOLD_GUARDIAN: {"open": "None pass who have not earned", "close": "Show me, don't tell me.", "secret": "what the toll really is"},
            Archetype.HERALD: {"open": "I came to announce", "close": "It has already begun.", "secret": "how little time there was"},
            Archetype.ALLY: {"open": "I'll walk with you toward", "close": "You won't do this alone.", "secret": "when to lean on someone"},
            Archetype.HERO: {"open": "I once chased", "close": "It cost more than I'd admit.", "secret": "that courage is mostly fear, walking"},
            Archetype.SELF: {"open": "Come home to", "close": "You were always this.", "secret": "that the search ends where it began"},
            Archetype.ANIMA: {"open": "Feel your way toward", "close": "The heart knows the map.", "secret": "what tenderness asks of you"},
            Archetype.ANIMUS: {"open": "Decide, then, about", "close": "Resolve is its own answer.", "secret": "what conviction leaves behind"},
        }
        return voices.get(archetype, voices[Archetype.ALLY])

    def _synchronicity_beat(self, hint: SynchronicityHint) -> SceneBeat:
        leads = [
            f"And then — {hint.symbol}, again. {hint.suggestion}",
            f"You have seen {hint.symbol} before. It returns now, and {hint.suggestion[0].lower() + hint.suggestion[1:]}",
            f"{hint.symbol[0].upper() + hint.symbol[1:]} surfaces where it should not: {hint.suggestion}",
        ]
        return SceneBeat(kind="synchronicity", text=self.rng.choice(leads),
                         symbol=hint.symbol)

    def _shard_beat(self, shards: list[str], theme: str) -> Optional[SceneBeat]:
        if not shards:
            return None
        shard = self.rng.choice(shards)
        frames = [
            f"A memory rises, unbidden: {shard}. It rhymes with where you stand now.",
            f"You remember — {shard} — and the echo of it changes the shape of this moment.",
            f"This has the texture of something before: {shard}.",
        ]
        return SceneBeat(kind="description", text=self.rng.choice(frames),
                         symbol="echo")

    # -- event / directive shaping --------------------------------------
    def _apply_directive(self, text: str, directive: str, theme: str,
                         gentle: bool) -> str:
        # The directive is an internal control signal from the loop/alignment layer
        # (e.g. "deliver the trust lesson more directly"). It SHAPES the scene via
        # stage/theme/intensity selection upstream; it must never be printed as
        # player-facing prose. Only the gentle-tone cue surfaces here.
        if gentle:
            text += " It asks, it does not demand — you may meet it in your own time."
        return text

    # -- assembly helpers -----------------------------------------------
    def _fit_beats(self, beats: list[SceneBeat]) -> list[SceneBeat]:
        """Keep 3–6 beats, always preserving the description and the event."""
        if len(beats) <= 6:
            if len(beats) >= 3:
                return beats
            # Too few — pad with a quiet descriptive breath.
            while len(beats) < 3:
                beats.insert(1, SceneBeat(
                    kind="description",
                    text="A pause. The scene settles, and waits for you.",
                ))
            return beats
        # Too many — keep first (setting), last (event), and sample the middle.
        head, tail = beats[0], beats[-1]
        middle = beats[1:-1]
        self.rng.shuffle(middle)
        return [head] + middle[:4] + [tail]

    def _build_choices(self, stage: dict, theme: str, gentle: bool) -> list[str]:
        pool = list(stage["choices"])
        self.rng.shuffle(pool)
        n = self.rng.randint(2, min(3, len(pool)))
        choices = pool[:n]
        # Always offer a reflective / self-aligned option, per the blueprint's
        # emphasis on the Identity Core choosing, not just reacting.
        reflective = f"Pause and ask what {theme} means to you here"
        if gentle:
            reflective = f"Choose the gentlest path toward {theme}"
        choices.append(reflective)
        return choices[:4]

    def _pick_intensity(self, target: float, gentle: bool) -> float:
        jitter = self.rng.uniform(-0.06, 0.06)
        val = target + jitter
        if gentle:
            val = min(val, 0.4)
        return max(0.0, min(1.0, round(val, 3)))

    def _collect_symbols(self, world: WorldState, introduced: list[str]) -> list[str]:
        out: list[str] = []
        for s in list(world.motifs) + introduced:
            if s and s not in out:
                out.append(s)
        return out

    def _make_title(self, stage: dict, theme: str, motif: str) -> str:
        patterns = [
            f"{stage['label'].title()}",
            f"{theme.title()} and {motif}",
            f"At {stage['label'].title()}",
        ]
        return self.rng.choice(patterns)

    def _make_summary(self, stage: dict, theme: str, world: WorldState,
                      gentle: bool) -> str:
        tone = "gently" if gentle else "squarely"
        return (
            f"At {world.location}, the protagonist meets {stage['label']} — "
            f"a scene that turns {tone} on {theme}."
        )


def _humanize(value: str, fallback: str) -> str:
    """Turn a raw `represents` tag (a trait key, theme, or goal) into a noun phrase
    that composes grammatically after a transitive voice lead-in. Long directive-
    like strings fall back to the theme so dialogue never reads as a pasted
    instruction."""
    if not value:
        return fallback
    v = value.strip()
    for suffix in ("_confidence", "_level", "_score"):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
    v = v.replace("_", " ").strip()
    if len(v.split()) > 5:
        return fallback
    return v or fallback
