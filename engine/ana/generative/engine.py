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
import re
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
from ..environment.mirror import place_prep
from ..identity.core import humanize_goal
from ..llm.gateway import ModelGateway

# A constraint of this form tells the engine a theme must never be depicted
# (the Feedback Loop Engine adds one per forbidden theme).
NEVER_DEPICT = "never depict:"

# Neutral themes to fall back on when every candidate theme is forbidden.
_NEUTRAL_THEMES = ("exploration", "the road", "wonder", "stillness")
_NEUTRAL_BEAT = "A pause. The scene settles, and waits for you."


def forbidden_from_constraints(constraints: list[str]) -> list[str]:
    """Themes named by ``"never depict: X"`` constraints."""
    out: list[str] = []
    for c in constraints or []:
        low = (c or "").strip()
        if low.lower().startswith(NEVER_DEPICT):
            t = low[len(NEVER_DEPICT):].strip().strip("'\"").lower()
            if t and t not in out:
                out.append(t)
    return out


def _hits(text: Optional[str], banned: list[str]) -> bool:
    if not text or not banned:
        return False
    from ..subsystems.alignment import mentions_forbidden  # lazy: avoid import cycle
    return mentions_forbidden(text, banned) is not None

# ─────────────────────────────────────────────────────────────
# Archetypal story patterns — the Hero's Journey skeleton
# ─────────────────────────────────────────────────────────────

# Each stage carries: the directive-ish "move" it realises, a verb-bank for the
# event beat, and the choice gestures it tends to offer. The engine selects a
# stage from the identity's themes/goals, then instantiates it with world motifs.
HERO_JOURNEY: dict[str, dict] = {
    # `label` names the stage (titles/summaries; the eval and arc read it).
    # `at` is a place-like phrase for titles ("At the threshold"), `stand` the
    # same for speech ("You stand at the threshold"), `this` a spoken sentence
    # naming the moment. Stage labels are never used as places.
    # Event templates take {motif}/{Motif} and {s} (verb agreement: "" when the
    # motif is plural, "s" otherwise).
    "call": {
        "label": "the call to adventure",
        "at": "At the beginning",
        "stand": "at the beginning of something",
        "this": "This is how it begins.",
        "event": "A summons arrives that cannot be unheard; {motif} mark{s} the beginning of something.",
        "events": ["A summons arrives that cannot be unheard; {motif} mark{s} the beginning of something.",
                   "Someone runs up the road calling a name, and it is yours; behind them, {motif}.",
                   "The ordinary day cracks open around {motif}, and something on the other side is waiting.",
                   "A message meant for someone braver lands in your hands, near {motif}."],
        "choices": ["Answer the call", "Hesitate at the edge", "Ask what it will cost"],
        "symbol": "the threshold-bell",
    },
    "threshold": {
        "label": "crossing the threshold",
        "at": "At the threshold",
        "stand": "at the threshold",
        "this": "This is the threshold.",
        "event": "The way forward narrows to a single door, and {motif} wait{s} on the far side.",
        "events": ["The way forward narrows to a single door, and {motif} wait{s} on the far side.",
                   "The path you know ends here. Past {motif}, nothing is familiar.",
                   "A gatekeeper steps out beside {motif} and asks what you are willing to leave behind.",
                   "The ground changes under your feet near {motif}; going back would now be its own decision."],
        "choices": ["Step across", "Turn back once more", "Leave a token behind"],
        "symbol": "the crossing",
    },
    "mentor": {
        "label": "meeting the mentor",
        "at": "At the mentor's door",
        "stand": "at the mentor's door",
        "this": "This is where help finds you, if you let it.",
        "event": "A knowing presence offers what you did not know you needed, near {motif}.",
        "events": ["A knowing presence offers what you did not know you needed, near {motif}.",
                   "Someone who has walked this road before sits by {motif} and waits for your question.",
                   "An old hand presses something into yours beside {motif}: not an answer, a tool.",
                   "Near {motif}, a voice you trust says the thing you have been refusing to hear."],
        "choices": ["Accept the gift", "Question the giver", "Offer something in return"],
        "symbol": "the given key",
    },
    "ordeal": {
        "label": "the ordeal",
        "at": "In the heart of the ordeal",
        "stand": "in the heart of the ordeal",
        "this": "This is the ordeal.",
        "event": "Everything you have been avoiding gathers here around {motif}, and will not be deferred.",
        "events": ["Everything you have been avoiding gathers here around {motif}, and will not be deferred.",
                   "The storm breaks all at once over {motif}; there is no one else to decide.",
                   "Beside {motif}, the thing you feared turns to face you, and it knows your name.",
                   "Someone is depending on you, and it is happening here, beside {motif}, now."],
        "choices": ["Face it directly", "Find another way through", "Call for help"],
        "symbol": "the dark water",
    },
    "reward": {
        "label": "seizing the reward",
        "at": "Within reach of the reward",
        "stand": "within reach of the reward",
        "this": "This is what you came for.",
        "event": "What you came for lies within reach; {motif} glint{s} with the thing itself.",
        "events": ["What you came for lies within reach; {motif} glint{s} with the thing itself.",
                   "The danger passes, and beside {motif} people are looking at you differently.",
                   "Near {motif}, you find what you came for, and it is not what you expected it to be.",
                   "Something is given back to you near {motif}, something you thought was gone."],
        "choices": ["Take it openly", "Take only what you need", "Share it at once"],
        "symbol": "the found thing",
    },
    "return": {
        "label": "the return",
        "at": "On the road home",
        "stand": "on the road home",
        "this": "This is the way home.",
        "event": "The road bends homeward, and {motif} look{s} different than before.",
        "events": ["The road bends homeward, and {motif} look{s} different than before.",
                   "You walk back past {motif}, and what once scared you is only part of the view.",
                   "Home is close. Beside {motif} you realise you are bringing something back with you.",
                   "People meet you on the road and ask what happened out there, and you find you can tell them."],
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
_WARM = ["gold", "honeyed", "amber", "soft", "warm"]
_COOL = ["grey", "slate", "pale", "thin", "steel-blue"]
_NEUTRAL = ["even", "muted", "level", "still", "silver"]

# Ways of saying the mirror's ambience, so "charged air" is not every scene.
_AIR = {
    "charged": ["charged", "restless", "taut", "electric"],
    "quiet": ["quiet", "hushed", "unmoving"],
}

# Light that weather imposes whatever the mood (no "gold light" in a storm).
_WEATHER_LIGHT = {
    "storm": ["bruised", "slate", "steel-blue", "dim", "flickering"],
    "overcast": ["grey", "pale", "muted", "flat"],
    "clearing": ["rinsed", "silver", "new", "pearl-grey"],
}

# How each weather reads in prose: a clause ("the sky is clear") and a noun
# phrase ("a clear sky"). Unknown weather words fall back to a plain phrasing.
_WEATHER_CLAUSE = {
    "storm": "a storm is breaking overhead",
    "overcast": "the sky hangs low and overcast",
    "clear": "the sky is clear",
    "golden light": "the whole sky is lit with gold",
    "clearing": "the sky is washed clean after the storm",
}
_WEATHER_NP = {
    "storm": "a storm breaking overhead",
    "overcast": "a low, overcast sky",
    "clear": "a clear sky",
    "golden light": "a sky lit with gold",
    "clearing": "a sky washed clean after the storm",
}

# Reflective choices, rotated so the same wording is never offered three times
# running. Each reads as a pause (the interpreter's "reflect" stance).
_REFLECTIVE = [
    "Pause and ask what {theme} means to you here",
    "Notice where {theme} lives in you right now",
    "Breathe, and listen for what {theme} is asking of you",
    "Wonder quietly what this place knows about {theme}",
    "Consider what {theme} would ask of you here",
    "Look back at how far {theme} has walked with you",
]
_GENTLE_REFLECTIVE = [
    "Choose the gentlest path toward {theme}",
    "Choose one small, gentle step toward {theme}",
    "Decide on the softest way to meet {theme}",
]
_GENTLE_LINE_TAILS = ["There is no hurry.", "Take whatever time you need.",
                      "Go gently.", "Nothing here needs to be rushed."]
_GENTLE_EVENT_TAILS = [
    " It asks; it does not demand. You may meet it in your own time.",
    " Nothing about it is urgent.",
    " It will still be here when you are ready.",
]

_GENTLE_ORDEAL_TAILS = [
    " Breathe first; you do not have to meet it all at once.",
    " You can take this one breath at a time.",
]

# A constraint the Feedback Loop Engine adds after the person wrote something
# that sounded like real distress: the next scene is a warm, quiet beat.
TENDER_CONSTRAINT = "after distress: a warm, low-intensity beat; allies close, shadows quiet"
_TENDER_CAP = 0.25


_SCENE_SYSTEM = """\
You are the Generative Experience Engine of ANA, a simulation in which a person's
inner identity ("Me") and a symbolic world ("Myself") shape each other in a loop.
Your job is to dream the next scene the protagonist lives through.

What makes a scene good here:
- It is a concrete, sensory moment in a specific place, not an abstract lesson.
  The world mirrors the protagonist's inner state through symbol and situation,
  without ever naming the psychology outright.
- It realises the current Hero's-Journey movement and the target theme, and it
  leaves the protagonist a real decision: 2-4 choices that differ in what they
  cost, including at least one quieter or reflective option.
- NPCs speak as themselves, in short lines, with their own wants. A Shadow figure
  can be uncomfortable; a Mentor can be wrong. Nobody lectures.
- Recalled memories and any synchronicity are woven in lightly, as echoes the
  protagonist might or might not notice.
- Intensity should land near the requested value. If the constraints ask for
  gentleness or hope, the scene is safe, warm, and unhurried.
- The material is symbolic and personal and can be dark, but it never depicts
  graphic violence, self-harm, or content that could hurt the person reading it.

Write 3-6 beats. Keep each beat to one to three sentences. Put spoken lines in
"dialogue" beats with the speaker's name, and use a "synchronicity" beat only if a
synchronicity was requested. `symbols` lists the symbolic objects that appear.
"""


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

    # Class-level defaults keep snapshots pickled before these existed loadable.
    _turn = 0
    _last_reflective: Optional[str] = None

    def __init__(self, gateway: ModelGateway | None = None, seed: int = 42,
                 use_llm: bool = True) -> None:
        self.gateway = gateway
        self.use_llm = use_llm
        # Anti-repetition memory for the deterministic path.
        from collections import deque
        self._met_npcs: set[str] = set()
        self._recent_text: deque = deque(maxlen=24)
        self._recent_shards: deque = deque(maxlen=4)
        self._cycle = 0
        # Per-NPC line memory (name -> [(turn, line, parts)]) so nobody repeats
        # themselves within a few scenes, and the last reflective choice offered.
        self._turn = 0
        self._npc_lines: dict[str, list] = {}
        self._last_reflective: Optional[str] = None
        self.rng = random.Random(seed)
        # Forbidden themes. The engine never voices one: it is the union of this
        # attribute (the Feedback Loop Engine sets it from the alignment layer's
        # ``forbidden_themes``) and any ``"never depict: X"`` request constraint.
        self.forbidden: list[str] = []
        self._ban: list[str] = []      # effective list for the current request

    # -- public API ------------------------------------------------------
    def generate(self, request: GenerationRequest) -> Scene:
        """Dream a :class:`Scene` from a :class:`GenerationRequest`.

        Tries the LLM path when wired and permitted; otherwise, or on any
        failure/malformed response, builds the scene deterministically.
        """
        self._ban = list(dict.fromkeys(
            [str(f).lower().strip() for f in (self.forbidden or []) if str(f).strip()]
            + forbidden_from_constraints(request.constraints)))
        if self.use_llm and self.gateway is not None and self.gateway.available:
            scene = self._generate_llm(request)
            if scene is not None:
                return self._scrub(scene, request)
        return self._scrub(self._generate_deterministic(request), request)

    # -- forbidden-theme hygiene -----------------------------------------
    def _scrub(self, scene: Scene, request: GenerationRequest) -> Scene:
        """Last line of the engine's own defence: no field may name a forbidden
        theme. Offending beats become a neutral pause (the closing event is kept
        as an event), choices/symbols are dropped, and title/summary fall back
        to theme-free phrasings. A no-op when nothing is forbidden."""
        ban = self._ban
        if not ban:
            return scene
        stage = HERO_JOURNEY.get(scene.stage or request.stage or "", None)
        label = stage["label"] if stage else "the next step"
        if _hits(scene.theme, ban):
            scene.theme = self._neutral_theme()
        beats: list[SceneBeat] = []
        for b in scene.beats:
            if _hits(b.text, ban) or _hits(b.speaker, ban) or _hits(b.symbol, ban):
                b = SceneBeat(kind="event" if b.kind == "event" else "description",
                              text=_NEUTRAL_BEAT,
                              symbol=None if _hits(b.symbol, ban) else b.symbol)
            beats.append(b)
        scene.beats = beats
        scene.symbols = [x for x in scene.symbols if not _hits(x, ban)]
        choices = [c for c in scene.choices if not _hits(c, ban)]
        scene.choices = choices or ["Pause and breathe", "Move on when ready"]
        if _hits(scene.title, ban):
            scene.title = _sentence(label)
        if _hits(scene.summary, ban):
            scene.summary = (f"{_sentence(label)} {place_prep(request.world.location)} "
                             f"{request.world.location}.")
            if _hits(scene.summary, ban):
                scene.summary = f"{_sentence(label)}."
        return scene

    def _neutral_theme(self) -> str:
        for t in _NEUTRAL_THEMES:
            if not _hits(t, self._ban):
                return t
        return "the next step"

    def _clean(self, options: list[str]) -> list[str]:
        """Options that name no forbidden theme (or the original list when none
        is clean — :meth:`_scrub` then neutralises the result)."""
        if not self._ban:
            return options
        return [o for o in options if not _hits(o, self._ban)] or options

    # -- LLM path --------------------------------------------------------
    def _generate_llm(self, request: GenerationRequest) -> Optional[Scene]:
        """Ask the gateway for a schema-conformant scene and map it into a Scene."""
        from ..llm.schemas import SCENE_SCHEMA

        user = self._llm_prompt(request)
        try:
            data = self.gateway.structured(_SCENE_SYSTEM, user, SCENE_SCHEMA)
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
            f"Journey stage to realise: {HERO_JOURNEY[request.stage]['label'] if request.stage in HERO_JOURNEY else 'whatever fits the self now'}",
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
        # The Self-Alignment Protocols own pacing: keep the model's chosen
        # intensity near what alignment asked for, and cap it when gentleness was
        # requested.
        t = request.target_intensity
        intensity = max(t - 0.2, min(t + 0.15, intensity))
        if self._is_gentle(request):
            intensity = min(intensity, 0.4)
        theme = str(data.get("theme") or self._pick_theme(request))
        return Scene(
            title=str(data.get("title") or theme.capitalize()),
            summary=str(data.get("summary") or ""),
            beats=beats,
            theme=theme,
            intensity=max(0.0, min(1.0, intensity)),
            choices=choices[:4],
            symbols=symbols or list(request.world.motifs),
            source="llm",
            stage=request.stage if request.stage in HERO_JOURNEY else None,
        )

    # -- deterministic path ---------------------------------------------
    def _generate_deterministic(self, request: GenerationRequest) -> Scene:
        idn, world = request.identity, request.world
        # Line memory is kept in story turns (the identity's tick), so a scene
        # regenerated within the same turn does not age anyone's lines.
        self._turn = max(self._turn, int(idn.tick))
        theme = self._pick_theme(request)
        stage_key = (request.stage if request.stage in HERO_JOURNEY
                     else self._pick_stage(theme, idn))
        stage = HERO_JOURNEY[stage_key]
        tender = self._is_tender(request)
        gentle = tender or self._is_gentle(request)

        beats: list[SceneBeat] = []
        introduced: list[str] = []

        # 1) Setting — a description beat drawn from location/weather/motifs.
        beats.append(self._description_beat(world, idn, gentle, tender))

        # 2) Dialogue — NPCs present, voiced per archetype/persona/represents.
        #    After distress the Shadow and the Trickster stay quiet.
        speakers = list(world.npcs)
        if tender:
            speakers = [n for n in speakers if n.archetype not in _QUIET_WHEN_TENDER]
        voiced = speakers[:2]
        soother = next((n for n in voiced if n.archetype not in _QUIET_WHEN_TENDER), None)
        self._scene_parts = set()
        for npc in voiced:
            beats.append(self._dialogue_beat(npc, theme, stage, gentle,
                                             tender=tender, soothe=gentle and npc is soother))

        # 3) Synchronicity — a meaningful coincidence, if the engine was handed one.
        if request.synchronicity is not None and not tender:
            beats.append(self._synchronicity_beat(request.synchronicity))
            introduced.append(request.synchronicity.symbol)

        # 4) Memory callback — weave a recalled shard as continuity, when relevant.
        shard_beat = self._shard_beat(request.recalled_shards, theme, idn)
        if shard_beat is not None:
            beats.append(shard_beat)

        # 5) Event — realise the directive / target theme via the chosen stage.
        motif = self._pick_motif(world)
        if tender:
            event_text = self._fresh(_TENDER_EVENTS)
        else:
            s_ = "" if _is_plural(motif) else "s"
            event_text = self._fresh(
                [e.format(motif=motif, Motif=_cap(motif), s=s_)
                 for e in stage.get("events", [stage["event"]])])
            event_text = self._apply_directive(event_text, request.directive, theme, gentle,
                                               stage_key)
        beats.append(SceneBeat(kind="event", text=event_text, symbol=stage["symbol"]))
        introduced.append(stage["symbol"])

        # Trim to the 3–6 beat band (description + event are always kept).
        beats = self._fit_beats(beats)

        choices = self._build_choices(stage, theme, gentle, tender)
        intensity = self._pick_intensity(request.target_intensity, gentle)
        if tender:
            intensity = min(intensity, _TENDER_CAP)
        symbols = self._collect_symbols(world, introduced)
        title = self._make_title(stage, theme, motif, tender)
        summary = self._make_summary(stage, theme, world, gentle)

        return Scene(
            stage=stage_key,
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
        if request.target_theme and not _hits(request.target_theme, self._ban):
            return request.target_theme
        themes = [t for t in request.identity.active_themes if not _hits(t, self._ban)]
        if themes:
            return self.rng.choice(themes)
        return self._neutral_theme() if self._ban else "exploration"

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
        motifs = [m for m in world.motifs if not _hits(m, self._ban)]
        if motifs:
            return self.rng.choice(motifs)
        return self.rng.choice(["a single lit window", "an open gate", "a held breath"])

    def _is_gentle(self, request: GenerationRequest) -> bool:
        blob = " ".join(request.constraints).lower()
        if any(k in blob for k in ("gentle", "hope", "soft", "calm", "safe", "ease")):
            return True
        return request.target_intensity < 0.35 or request.identity.emotion.stress > 0.6

    @staticmethod
    def _is_tender(request: GenerationRequest) -> bool:
        """The person just wrote something that sounded like real distress (the
        Feedback Loop Engine adds :data:`TENDER_CONSTRAINT`)."""
        return any(TENDER_CONSTRAINT in (c or "") for c in request.constraints)

    # -- beat builders ---------------------------------------------------
    def _description_beat(self, world: WorldState, idn: IdentitySnapshot,
                          gentle: bool, tender: bool = False) -> SceneBeat:
        v = idn.emotion.valence
        if gentle or v > 0.25:
            bank = _WARM
        elif v < -0.25:
            bank = _COOL
        else:
            bank = _NEUTRAL
        weather = (world.weather or "clear").strip()
        if weather in _WEATHER_LIGHT:
            bank = _WEATHER_LIGHT[weather]
        if weather == "golden light":
            bank = [b for b in bank if b not in ("gold", "honeyed", "amber")] or ["soft"]
        air = self.rng.choice(_AIR.get(world.ambience or "", [world.ambience or "still"]))
        bank = [b for b in bank if b != air and b not in weather] or bank
        light = self.rng.choice(bank)
        loc = world.location
        prep = place_prep(loc)
        hour = (world.time_of_day or "day").strip()
        hour_cap = _cap(hour)
        hour_light = hour if "light" in hour else f"{hour} light"
        glow = "glow" if "light" in hour else "light"     # never "first light ... light"
        lit = f"{_a(light)} {light} glow" if glow == "glow" else f"{light} light"
        clause = _WEATHER_CLAUSE.get(weather, f"the weather holds {weather}")
        np_ = _WEATHER_NP.get(weather, f"{weather} weather")
        # Openers never start with the location itself (a one-letter "A" under
        # a drop cap reads as "Atown"), and never repeat a word.
        openers = [
            f"You arrive at {loc}. The {hour_light} is {light}, and {clause}.",
            f"{hour_cap} {prep} {loc}: {lit}, {air} air, and {np_}.",
            f"{hour_cap} finds you {prep} {loc}, the air {air} and the {glow} {light}; {clause}.",
        ]
        text = self._fresh(openers)
        motifs = [x for x in world.motifs if not _hits(x, self._ban)]
        if tender:
            text += " " + self._fresh(_TENDER_TAILS)
        elif motifs:
            m = self.rng.choice(motifs)
            draws = "draw" if _is_plural(m) else "draws"
            tails = [
                f" {_cap(m)} {draws} the eye, as if waiting for you.",
                f" Somewhere in it, {m}: small, insistent, meant for you.",
                f" You notice {m} before anything else.",
            ]
            text += self.rng.choice(tails)
        return SceneBeat(kind="description", text=text,
                         symbol=motifs[0] if motifs else None)

    def _dialogue_beat(self, npc: NPC, theme: str, stage: dict, gentle: bool, *,
                       tender: bool = False, soothe: bool = False) -> SceneBeat:
        voice = self._voice_for(npc.archetype)
        rep_tag = npc.represents if not _hits(npc.represents, self._ban) else None
        represents = _humanize(rep_tag or theme, theme, max_words=3)
        persona = npc.persona or "unreadable"
        if _hits(persona, self._ban):
            persona = persona.split(";")[0].split(", carrying")[0].strip()
            if _hits(persona, self._ban):
                persona = "unreadable"
        if tender:
            cands = [(f"\"{x}\"", (x,)) for x in _TENDER_LINES]
        else:
            cands = self._line_candidates(voice, represents, stage)
        line = self._pick_line(npc.name, cands)
        if _hits(line, self._ban):
            line = "\"" + self._fresh(["We are here now. That is enough.",
                                       "Look around you. Then decide."]) + "\""
        if soothe and not tender:
            line = line[:-1] + " " + self._fresh(_GENTLE_LINE_TAILS) + "\""
        name = _cap(npc.name)
        if npc.name not in self._met_npcs:
            self._met_npcs.add(npc.name)
            text = f"{name}, {persona}, speaks. {line}"
        else:
            verbs = ["says", "says quietly", "tells you", "murmurs", "says softly"]
            text = f"{name} {self.rng.choice(verbs)}: {line}"
        return SceneBeat(kind="dialogue", text=text, speaker=npc.name,
                         archetype=npc.archetype)

    @staticmethod
    def _line_candidates(voice: dict, rep: str, stage: dict) -> list[tuple[str, tuple]]:
        """Every line this archetype could say now, with the voice parts each
        uses (so a phrase is not leaned on turn after turn)."""
        out: list[tuple[str, tuple]] = []
        for o in voice["open"]:
            for c in voice["close"]:
                out.append((f"\"{o} {rep}. {c}\"", (o, c)))
        for c in voice["close"]:
            out.append((f"\"{c}\"", (c,)))
            out.append((f"\"I have been watching you circle {rep}. {c}\"", ("watching", c)))
            out.append((f"\"{stage['this']} {c}\"", (stage["this"], c)))
        for sec in voice["secret"]:
            out.append((f"\"About {rep}? Nobody warns you {sec}.\"", ("nobody warns", sec)))
            out.append((f"\"Here is what nobody told me about {rep}: {sec}.\"", ("nobody told", sec)))
        out.append((f"\"You stand {stage['stand']}, whether you name it or not.\"", ("stand",)))
        out.append((f"\"Ask me about {rep}. Or don't; you already know.\"", ("ask me",)))
        return out

    def _pick_line(self, name: str, cands: list[tuple[str, tuple]]) -> str:
        """A line this NPC has not said in the last six turns, preferring one
        that reuses none of the phrases it leaned on in its last few lines."""
        hist = self.__dict__.setdefault("_npc_lines", {}).setdefault(name, [])
        recent_lines = {ln for t, ln, _ in hist if self._turn - t < _LINE_MEMORY}
        recent_parts = {p for t, _, parts in hist if self._turn - t < _PART_MEMORY
                        for p in parts}
        cands = [c for c in cands if not _hits(c[0], self._ban)] or cands
        # Two people in one scene never share a phrase either.
        scene_parts = self.__dict__.setdefault("_scene_parts", set())
        cands = [c for c in cands if not (set(c[1]) & scene_parts)] or cands
        pool = [c for c in cands if c[0] not in recent_lines
                and not (set(c[1]) & recent_parts)]
        pool = pool or [c for c in cands if c[0] not in recent_lines] or cands
        pool = [c for c in pool if c[0] not in self._recent_text] or pool
        line, parts = self.rng.choice(pool)
        scene_parts.update(parts)
        self._recent_text.append(line)
        hist.append((self._turn, line, parts))
        del hist[:-12]
        return line

    def _voice_for(self, archetype: Archetype) -> dict:
        return _VOICES.get(archetype, _VOICES[Archetype.ALLY])

    def _synchronicity_beat(self, hint: SynchronicityHint) -> SceneBeat:
        # `hint.suggestion` is a staging note for the generator, never prose: the
        # whole point of a synchronicity is that the narration doesn't explain it.
        sym = hint.symbol
        if _hits(sym, self._ban):
            sym = "a small, familiar sign"
        cap = _cap(sym)
        verb = "surface" if _is_plural(sym) else "surfaces"
        is_ = "are" if _is_plural(sym) else "is"
        leads = [
            f"And then: {sym}, just there, as if waiting for this exact moment.",
            f"{cap} {verb} where it should not be. No one else seems to notice.",
            f"For a breath, everything else goes quiet around {sym}.",
            f"You almost miss it: {sym}. When you look again, it {is_} still there."
            if not _is_plural(sym) else
            f"You almost miss them: {sym}. When you look again, they are still there.",
        ]
        return SceneBeat(kind="synchronicity", text=self._fresh(leads), symbol=sym)

    def _shard_beat(self, shards: list[str], theme: str,
                    idn: Optional[IdentitySnapshot] = None) -> Optional[SceneBeat]:
        """Weave one recalled memory as an echo — rationed, and rotating through
        shards so the same memory doesn't surface every cycle.

        Only human-meaningful memories are echoed: seeded memories, lessons,
        beliefs and hoped-for futures. The engine's own outcome records
        ("Title: met the moment — ...") are never shown."""
        if not shards:
            return None
        self._cycle += 1
        shards = [sh for sh in shards if _echoable(sh)]
        fresh = [sh for sh in shards if sh not in self._recent_shards
                 and not _hits(sh, self._ban)]
        on_theme = [sh for sh in fresh if theme.lower() in sh.lower()]
        # Echo when the memory is on-theme, otherwise only every third cycle.
        if not on_theme and self._cycle % 3 != 0:
            return None
        pool = on_theme or fresh
        if not pool:
            return None
        shard = self.rng.choice(pool)
        self._recent_shards.append(shard)
        goals = {g.description.strip().lower(): g for g in (idn.goals if idn else [])}
        clean = " ".join(shard.split()).strip()
        if clean.lower().rstrip(".") in goals or clean.lower() in goals:
            # A hoped-for future (a goal held as a "memory of the future").
            hope = humanize_goal(clean, form="hope")
            if hope is None:
                frames = ["Something here reminds you of what you are hoping for.",
                          "For a moment, what you hope for feels close enough to touch."]
            else:
                frames = [f"You think of {hope}, and here it feels a little nearer.",
                          f"Something here reminds you of {hope}.",
                          f"For a moment, {hope} feels close enough to touch."]
        elif clean.startswith(("Belief: ", "I believe ")):
            body = clean.split(" ", 1)[1] if clean.startswith("Belief: ") else clean[len("I believe "):]
            body = _second_person(body.rstrip("."))
            frames = [f"An old belief stirs: that {body}. Here, it sounds less certain.",
                      f"You catch yourself thinking it again: {body}. Here, it does not quite fit."]
        else:
            # A lesson, a realised vision or a seeded memory, in the person's
            # own words: quoted, never spliced mid-sentence.
            quote = clean if clean[-1:] in ".!?" else clean + "."
            quote = quote[:1].upper() + quote[1:]
            frames = [
                f"A memory rises, unbidden: \"{quote}\" It rhymes with where you stand now.",
                f"Something comes back without asking: \"{quote}\"",
                f"Under the noise, an old echo: \"{quote}\"",
                f"It comes back to you, clear as anything: \"{quote}\"",
            ]
        return SceneBeat(kind="description", text=self._fresh(frames), symbol="echo")

    def _fresh(self, options: list[str]) -> str:
        """Pick an option whose exact text hasn't appeared in recent cycles."""
        options = self._clean(options)
        pool = [o for o in options if o not in self._recent_text] or options
        text = self.rng.choice(pool)
        self._recent_text.append(text)
        return text

    # -- event / directive shaping --------------------------------------
    def _apply_directive(self, text: str, directive: str, theme: str,
                         gentle: bool, stage_key: Optional[str] = None) -> str:
        # The directive is an internal control signal from the loop/alignment layer
        # (e.g. "deliver the trust lesson more directly"). It SHAPES the scene via
        # stage/theme/intensity selection upstream; it must never be printed as
        # player-facing prose. Only the gentle-tone cue surfaces here.
        if gentle:
            # The ordeal is still the ordeal; gentleness there is about pace.
            tails = _GENTLE_ORDEAL_TAILS if stage_key == "ordeal" else _GENTLE_EVENT_TAILS
            text += self._fresh(tails)
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

    def _build_choices(self, stage: dict, theme: str, gentle: bool,
                       tender: bool = False) -> list[str]:
        if tender:
            pool = self._clean(list(_TENDER_CHOICES))
            self.rng.shuffle(pool)
            return pool[:3]
        pool = self._clean(list(stage["choices"]))
        self.rng.shuffle(pool)
        lo = min(2, len(pool))
        n = self.rng.randint(lo, max(lo, min(3, len(pool))))
        choices = pool[:n]
        # Always offer a reflective / self-aligned option, per the blueprint's
        # emphasis on the Identity Core choosing, not just reacting. Its wording
        # rotates: the same phrasing is never offered twice in a row.
        bank = _GENTLE_REFLECTIVE if gentle else _REFLECTIVE
        options = [b.format(theme=theme) for b in bank]
        options = [o for o in options if o != self._last_reflective] or options
        reflective = self.rng.choice(options)
        if _hits(reflective, self._ban):  # theme is never banned; belt and braces
            reflective = "Pause and ask what this moment means to you"
        self._last_reflective = reflective
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
            if s and s not in out and not _hits(s, self._ban):
                out.append(s)
        return out

    def _make_title(self, stage: dict, theme: str, motif: str,
                    tender: bool = False) -> str:
        if tender:
            return self.rng.choice(["A gentler hour", "Somewhere to rest", "Company on the road"])
        patterns = [
            _sentence(stage['label']),
            f"{theme.capitalize()} and {motif}",
            stage["at"],
        ]
        return self.rng.choice(patterns)

    def _make_summary(self, stage: dict, theme: str, world: WorldState,
                      gentle: bool) -> str:
        return stage_summary(stage["label"], world.location, theme, gentle)


def stage_summary(label: str, location: str, theme: str, gentle: bool = False) -> str:
    """'The call to adventure at an unmarked threshold: a scene that turns
    squarely on grief.' The stage label stays in the summary (the arc and the
    eval read it), but always as the subject, never as a place."""
    tone = "gently" if gentle else "squarely"
    return (f"{_sentence(label)} {place_prep(location)} {location}: "
            f"a scene that turns {tone} on {theme}.")


def _sentence(text: str) -> str:
    """Sentence case: 'the call to adventure' -> 'The call to adventure'."""
    return text[:1].upper() + text[1:] if text else text


_cap = _sentence


def _a(word: str) -> str:
    """The indefinite article for ``word``."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def _is_plural(phrase: str) -> bool:
    """Does a motif read as plural ("falling leaves", "open gates", "mirrors
    turned to the wall")? Phrases with an article are singular."""
    words = (phrase or "").lower().split()
    if not words or words[0] in ("a", "an", "one", "the", "this", "that"):
        return False
    for w in words[:2]:
        w = w.strip(",.;:")
        if w in ("that", "which", "who", "of", "on", "in", "to", "with"):
            break
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            return True
    return False


# Outcome records the engine writes into memory ("Title: met the moment — ...").
_OUTCOME_RECORD = re.compile(r":\s*(met|turned from|moved through) the moment\b")


def _echoable(shard: str) -> bool:
    """A memory that may be echoed back in prose: never an outcome record, and
    never an empty or very long string."""
    s = " ".join(str(shard or "").split())
    return (bool(s) and len(s) <= 220 and not _OUTCOME_RECORD.search(s)
            and not _RETREAT_NOTE.search(s))


# The interpreter's notes on turning away ("I turned away from grief this
# time"): true, but not something to echo back at a person as a memory.
_RETREAT_NOTE = re.compile(r"\b(turned away from|turning from)\b|keeps finding me",
                           re.IGNORECASE)


def _second_person(text: str) -> str:
    """A person's own first-person words, said back to them, starting lower-case
    where that is safe ("I am not the kind ..." -> "you are not the kind ...")."""
    from ..identity.core import _you
    words = _you(text.split())
    if words:
        w = words[0]
        if w.lower() in _COMMON_STARTS or w in ("you", "your", "you're", "you've"):
            words[0] = w[:1].lower() + w[1:]
    return " ".join(words)


_COMMON_STARTS = frozenset("""
a an the people everyone everything nobody nothing no one some most many all
love fear grief courage trust it there this that what when if i my me
""".split())


def _humanize(value: str, fallback: str, *, max_words: int = 5) -> str:
    """Turn a raw `represents` tag (a trait key, theme, or goal) into a noun phrase
    that composes grammatically after a transitive voice lead-in.

    Trait keys lose their suffix ("grief_confidence" -> "grief"); goals become a
    humanised noun phrase ("find my purpose" -> "the wish to find your purpose");
    anything longer than ``max_words`` words falls back to ``fallback`` (the
    theme), so prose never reads as a pasted instruction."""
    if not value:
        return fallback
    v = value.strip()
    for suffix in ("_confidence", "_level", "_score"):
        if v.endswith(suffix):
            v = v[: -len(suffix)]
    v = v.replace("_", " ").strip()
    words = v.split()
    if not words:
        return fallback
    if words[0].lower() in _GOAL_VERBS or len(words) >= 3:
        return humanize_goal(v, form="wish", max_words=max_words) or fallback
    return v if len(words) <= max_words else fallback


_GOAL_VERBS = frozenset("""
find become learn be get make build stop start forgive lead feel speak do go
grow heal trust let keep stand take give live love write create finish tell say
""".split())

# How long an NPC remembers its own lines (in story turns).
_LINE_MEMORY = 7
_PART_MEMORY = 3

_QUIET_WHEN_TENDER = (Archetype.SHADOW, Archetype.TRICKSTER)

_TENDER_LINES = [
    "You don't have to carry this alone. I'm right here.",
    "Sit a while. Nothing is asked of you today.",
    "Whatever you are feeling, you are welcome here.",
    "Rest. The road will wait for you.",
    "I'm not going anywhere. Take your time.",
    "You matter here, exactly as you are.",
]
_TENDER_TAILS = [
    "Someone has left a lamp lit for you, and a place to sit beside it.",
    "The light is kind here, and nothing is in a hurry.",
    "A warm cup has been set down within your reach.",
]
_TENDER_EVENTS = [
    "Nothing needs deciding right now. The light stays, and so does the company.",
    "The road can wait. For now there is warmth, and someone beside you.",
    "The world goes quiet and kind around you, and asks nothing at all.",
]
_TENDER_CHOICES = [
    "Rest here a while",
    "Let someone sit with you",
    "Breathe, and take your time",
    "Go on gently when you are ready",
]

# Each archetype's voice: transitive lead-ins (followed by a noun phrase),
# standalone closing lines, and "nobody warns you ..." secrets.
_VOICES: dict[Archetype, dict] = {
    Archetype.MENTOR: {
        "open": ["You already carry", "Nobody masters", "There is no map for",
                 "Sit with me and think about"],
        "close": ["Trust the weight of it.", "Go slowly; you are not late.",
                  "You know more than you think.", "Ask the question you came with.",
                  "I can walk beside you, not for you.", "Begin where your feet are."],
        "secret": ["how heavy a gift is", "that the map is drawn by walking",
                   "how often the teacher is still learning",
                   "that doubt walks beside every honest step"],
    },
    Archetype.SHADOW: {
        "open": ["You keep refusing", "You still flinch from", "You built a wall around",
                 "You keep stepping around"],
        "close": ["I am only what you buried.", "I go where you go.",
                  "You can look at me, you know.", "I was never your enemy.",
                  "I am the part you left at the door.",
                  "Turn around. I am not going anywhere."],
        "secret": ["who you become in the dark", "how long a thing can wait to be seen",
                   "that what you hide keeps its own hours",
                   "how much it costs to keep looking away"],
    },
    Archetype.TRICKSTER: {
        "open": ["Oh, so it's", "Everyone here is bargaining over", "I know a back door into",
                 "Let me tell you a secret about"],
        "close": ["The rules are softer than you think.",
                  "Every wall is a door if you lean on it.",
                  "Laugh. It loosens the knots.", "I lie about many things, but not this.",
                  "You take yourself so seriously."],
        "secret": ["which door is painted on", "how much of the game is bluffing",
                   "that the shortcut is usually the long way", "who is really keeping score"],
    },
    Archetype.THRESHOLD_GUARDIAN: {
        "open": ["None pass who have not reckoned with", "Everyone who comes this way speaks of",
                 "I have watched many people stumble over", "Show me something of"],
        "close": ["Show me, don't tell me.", "The way is open to those who mean it.",
                  "I am not here to stop you, only to ask.", "Every crossing has a price.",
                  "Speak plainly and you may pass."],
        "secret": ["what the toll really is", "that the gate opens from the inside",
                   "how many turn back at the last step",
                   "that the test is never the one you studied for"],
    },
    Archetype.HERALD: {
        "open": ["I came with news about", "Word has gone ahead about",
                 "They are already talking about", "I carry news of"],
        "close": ["It has already begun.", "The bells are ringing for you.",
                  "No one can unhear this.", "Everything changes from here.",
                  "Go on; they are waiting."],
        "secret": ["how little time there was", "how fast news travels on this road",
                   "that it began before you noticed", "how many were waiting for you"],
    },
    Archetype.ALLY: {
        "open": ["I'll walk with you through", "We can carry", "I have been thinking about",
                 "I'll stand with you in"],
        "close": ["You won't do this alone.", "I'm here. That's all.",
                  "I've got the other end of this.", "We go at your pace.",
                  "Say the word and I'm with you."],
        "secret": ["when to lean on someone", "how much lighter it is shared",
                   "that being helped takes its own courage", "how far two people can walk"],
    },
    Archetype.HERO: {
        "open": ["I once ran from", "I ran headlong at", "Nobody gets past"],
        "close": ["It cost more than I'd admit.", "Run at it; that's what I'd do.",
                  "Fear is just the price of the ticket."],
        "secret": ["that courage is mostly fear, walking", "how quiet the bravest moment is",
                   "that the hard part comes after"],
    },
    Archetype.SELF: {
        "open": ["Come home to", "You were never far from", "Rest a moment in"],
        "close": ["You were always this.", "Nothing is missing.", "Breathe. You are here."],
        "secret": ["that the search ends where it began", "how little needs fixing",
                   "that stillness is also a step"],
    },
    Archetype.ANIMA: {
        "open": ["Feel your way toward", "Let yourself soften toward", "Listen underneath"],
        "close": ["The heart knows the map.", "Let it move you.", "Tenderness is not weakness."],
        "secret": ["what tenderness asks of you", "how loud a quiet feeling can be",
                   "that feeling it is part of crossing it"],
    },
    Archetype.ANIMUS: {
        "open": ["Decide, then, about", "Make a plan for", "Draw a line around"],
        "close": ["Resolve is its own answer.", "One clear step is enough.",
                  "Choose, and the rest follows."],
        "secret": ["what conviction leaves behind", "how freeing one decision is",
                   "that a plan is a promise to yourself"],
    },
}
