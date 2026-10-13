"""Journey arc + debrief — giving the Hero's Journey a shape and an ending.

The Feedback Loop Engine can run forever; a *journey* cannot. The blueprint asks
the Generative Experience Engine to bring forth "the call to adventure, the
challenges, the mentors, the transformation moments, and the return home", and
(under "User Control and Transparency") to offer the person "a debrief or
summary ... at certain milestones" — key memory shards shown back "in a
narrative form". This module supplies both halves:

* :class:`JourneyArc` watches the cycles go by and tracks which Hero's-Journey
  stage each one realised (``call -> threshold -> mentor -> ordeal -> reward ->
  return``), together with convergence, success, lessons and the NPCs met. It
  names the current chapter, lists milestones ("crossed the threshold at cycle
  4"), tells the loop when the story has earned its close
  (:meth:`JourneyArc.should_close`), and offers a :meth:`JourneyArc.stage_hint`
  so generation keeps moving forward — the story reaches ordeal, reward and
  return *in order*, never sliding back more than one stage.
* :func:`build_debrief` turns a run's :class:`~ana.contracts.CycleRecord` list
  into a :class:`Debrief`: a warm, specific reflection addressed to the person
  (not a log dump). The deterministic version always works; with
  ``use_llm=True`` the model rewrites it via ``gateway.structured`` against the
  strict :data:`DEBRIEF_SCHEMA`, and any failure (``None``, bad shape, exception)
  falls back to the deterministic text.

Cycle numbers in milestones and chapters are 1-based positions in the journey
(the first recorded cycle is "cycle 1"), which is how a person counts.

Pure stdlib + pydantic; no network unless a gateway is supplied *and* asked for.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from ..contracts import Archetype, CycleRecord, IdentitySnapshot

# ─────────────────────────────────────────────────────────────
# Stage vocabulary
# ─────────────────────────────────────────────────────────────

STAGES: tuple[str, ...] = ("call", "threshold", "mentor", "ordeal", "reward", "return")

# Human labels (kept in sync with ana.generative.engine.HERO_JOURNEY, duplicated
# here so this module never depends on the generator's internals).
STAGE_LABELS: dict[str, str] = {
    "call": "the call to adventure",
    "threshold": "crossing the threshold",
    "mentor": "meeting the mentor",
    "ordeal": "the ordeal",
    "reward": "seizing the reward",
    "return": "the return",
}

# Symbols the deterministic generator stamps on each stage's event beat.
STAGE_SYMBOLS: dict[str, str] = {
    "call": "the threshold-bell",
    "threshold": "the crossing",
    "mentor": "the given key",
    "ordeal": "the dark water",
    "reward": "the found thing",
    "return": "the long road",
}

# Extra phrases that identify a stage in free (e.g. LLM-written) titles/summaries.
_STAGE_PHRASES: dict[str, tuple[str, ...]] = {
    "call": ("call to adventure", "the call", "a summons", "summoned"),
    "threshold": ("crossing the threshold", "the threshold", "cross over", "crossing"),
    "mentor": ("meeting the mentor", "the mentor", "a mentor"),
    "ordeal": ("the ordeal", "ordeal", "trial by", "the abyss"),
    "reward": ("seizing the reward", "the reward", "reward", "the prize", "the boon"),
    "return": ("the return", "return home", "homeward", "road home", "returning"),
}

# Short chapter names for the debrief / current_chapter().
CHAPTER_NAMES: dict[str, str] = {
    "call": "The Call",
    "threshold": "Crossing the Threshold",
    "mentor": "Meeting the Mentor",
    "ordeal": "The Ordeal",
    "reward": "The Reward",
    "return": "The Return",
}

_STAGE_ALIASES: dict[str, str] = {
    "call to adventure": "call", "the call": "call", "calling": "call",
    "crossing": "threshold", "crossing the threshold": "threshold",
    "meeting the mentor": "mentor", "mentorship": "mentor",
    "the ordeal": "ordeal", "trial": "ordeal",
    "seizing the reward": "reward", "boon": "reward",
    "the return": "return", "return home": "return", "home": "return",
}


def normalize_stage(stage: Optional[str]) -> Optional[str]:
    """Map a free stage string ("Ordeal", "the return", "call to adventure") to a
    canonical key in :data:`STAGES`, or ``None`` if it is not recognisable."""
    if not stage:
        return None
    s = str(stage).strip().lower().replace("_", " ").replace("-", " ")
    if s in STAGES:
        return s
    if s in _STAGE_ALIASES:
        return _STAGE_ALIASES[s]
    for key, label in STAGE_LABELS.items():
        if s == label:
            return key
    for key in STAGES:
        if re.search(rf"\b{key}\b", s):
            return key
    return None


def infer_stage(record: CycleRecord) -> Optional[str]:
    """Infer the Hero's-Journey stage a cycle realised from its scene.

    Looks, in order of reliability, for a full stage label in the title/summary
    ("crossing the threshold"), the stage's signature event symbol ("the dark
    water"), then looser stage phrases. Returns ``None`` if nothing matches.
    """
    scene = record.scene
    text = f"{scene.title} \n {scene.summary}".lower()
    # 1. Exact labels — the most specific signal. Pick the earliest occurrence so
    #    "the return" in a title wins over a stray word later in a summary.
    hits: list[tuple[int, str]] = []
    for key, label in STAGE_LABELS.items():
        i = text.find(label)
        if i >= 0:
            hits.append((i, key))
    if hits:
        return min(hits)[1]
    # 2. Signature symbols on the scene / its event beats.
    syms = {s.lower() for s in scene.symbols}
    syms |= {(b.symbol or "").lower() for b in scene.beats if b.symbol}
    for key, sym in STAGE_SYMBOLS.items():
        if sym in syms:
            return key
    # 3. Looser phrases.
    for key, phrases in _STAGE_PHRASES.items():
        for p in phrases:
            if re.search(rf"\b{re.escape(p)}\b", text):
                return key
    return None


# ─────────────────────────────────────────────────────────────
# JourneyArc
# ─────────────────────────────────────────────────────────────

_FACE_WORDS = ("face", "confront", "stand up to", "stand against", "stand my ground",
               "challenge", "turn toward", "look it in the eye")
_AVOID_WORDS = ("flee", "run", "hide", "ignore", "avoid", "refuse", "turn back",
                "leave", "wait", "hesitate")


@dataclass
class ArcEntry:
    """One cycle as the arc sees it."""

    cycle: int                    # 1-based position in the journey
    tick: int
    stage: str
    convergence: float
    success: Optional[bool]
    lesson: Optional[str]
    title: str
    theme: str
    action: str
    npcs: list[str] = field(default_factory=list)
    shadows: list[str] = field(default_factory=list)
    mentors: list[str] = field(default_factory=list)
    location: str = ""


class JourneyArc:
    """Tracks the shape of one Hero's Journey across feedback-loop cycles.

    Args:
        max_chapters: guard for :meth:`should_close` — once the journey has run
            through this many chapters (consecutive same-stage runs), it closes
            even if the return was never reached.
        max_cycles: a second guard on raw cycle count (``None`` disables it).
        close_convergence: convergence at which a journey that has reached the
            return may close.
        close_goals: goal progress that also lets a returned journey close, even
            if convergence is a little short.
        min_return_cycles: how many cycles must be spent in the return before
            closing (gives "coming home" at least one scene).
        max_return_cycles: once this many cycles have been lived in the return,
            the homecoming is complete and the journey closes whatever the
            convergence (``None`` disables it).
        stage_dwell: how many cycles :meth:`stage_hint` lets the story linger in
            its furthest stage before nudging it one stage onward.
    """

    def __init__(self, *, max_chapters: int = 9, max_cycles: Optional[int] = 30,
                 close_convergence: float = 0.75, close_goals: float = 0.8,
                 min_return_cycles: int = 1, max_return_cycles: Optional[int] = 3,
                 stage_dwell: int = 3) -> None:
        self.max_chapters = max_chapters
        self.max_cycles = max_cycles
        self.close_convergence = close_convergence
        self.close_goals = close_goals
        self.min_return_cycles = max(1, min_return_cycles)
        self.max_return_cycles = max_return_cycles
        self.stage_dwell = max(1, stage_dwell)
        self.entries: list[ArcEntry] = []
        self._seen_ticks: set[tuple[int, str]] = set()

    # -- recording -------------------------------------------------------
    def record(self, record: CycleRecord, stage: Optional[str] = None) -> ArcEntry:
        """Fold one cycle into the arc.

        ``stage`` (any spelling :func:`normalize_stage` accepts) wins; otherwise
        the stage is inferred from the scene; failing that, the previous stage is
        carried forward (or ``"call"`` for the very first cycle). Recording the
        same record twice is a no-op that returns the existing entry.
        """
        key = (record.tick, record.scene.id)
        if key in self._seen_ticks:
            for e in self.entries:
                if e.tick == record.tick:
                    return e
        st = normalize_stage(stage) or infer_stage(record)
        if st is None:
            st = self.entries[-1].stage if self.entries else "call"

        npcs, shadows, mentors = _npcs_in(record)
        action = record.outcome.user_action.raw if record.outcome.user_action else ""
        entry = ArcEntry(
            cycle=len(self.entries) + 1,
            tick=record.tick,
            stage=st,
            convergence=float(record.convergence),
            success=record.outcome.success,
            lesson=record.outcome.lesson,
            title=record.scene.title,
            theme=record.scene.theme,
            action=action,
            npcs=npcs,
            shadows=shadows,
            mentors=mentors,
            location=record.request.world.location if record.request else "",
        )
        self.entries.append(entry)
        self._seen_ticks.add(key)
        return entry

    def extend(self, records: Iterable[CycleRecord]) -> "JourneyArc":
        for r in records:
            self.record(r)
        return self

    @classmethod
    def from_records(cls, records: Iterable[CycleRecord], **kw: Any) -> "JourneyArc":
        return cls(**kw).extend(records)

    # -- reading ---------------------------------------------------------
    @property
    def stage(self) -> Optional[str]:
        """The stage of the latest recorded cycle."""
        return self.entries[-1].stage if self.entries else None

    @property
    def furthest_stage(self) -> Optional[str]:
        """The furthest stage the journey has reached (its high-water mark)."""
        if not self.entries:
            return None
        return STAGES[max(STAGES.index(e.stage) for e in self.entries)]

    def chapters(self) -> list[dict]:
        """Consecutive same-stage runs: ``[{stage, name, start, end, cycles}]``."""
        out: list[dict] = []
        for e in self.entries:
            if out and out[-1]["stage"] == e.stage:
                out[-1]["end"] = e.cycle
                out[-1]["cycles"].append(e.cycle)
            else:
                out.append({"stage": e.stage, "name": CHAPTER_NAMES[e.stage],
                            "start": e.cycle, "end": e.cycle, "cycles": [e.cycle]})
        return out

    def current_chapter(self) -> str:
        """E.g. ``"Chapter 3: Meeting the Mentor"`` (``"Prologue"`` before any cycle)."""
        ch = self.chapters()
        if not ch:
            return "Prologue"
        return f"Chapter {len(ch)}: {ch[-1]['name']}"

    def reached(self, stage: str) -> bool:
        st = normalize_stage(stage)
        return any(e.stage == st for e in self.entries)

    def first_cycle_at(self, stage: str) -> Optional[int]:
        st = normalize_stage(stage)
        for e in self.entries:
            if e.stage == st:
                return e.cycle
        return None

    def npcs_met(self) -> list[str]:
        seen: list[str] = []
        for e in self.entries:
            for n in e.npcs:
                if n not in seen:
                    seen.append(n)
        return seen

    def milestones(self) -> list[str]:
        """Plain-language milestones in the order they happened."""
        events: list[tuple[int, int, str]] = []  # (cycle, order, text)
        verbs = {
            "call": "heard the call",
            "threshold": "crossed the threshold",
            "mentor": "met the mentor",
            "ordeal": "entered the ordeal",
            "reward": "seized the reward",
            "return": "turned for home",
        }
        for i, st in enumerate(STAGES):
            c = self.first_cycle_at(st)
            if c is not None:
                events.append((c, i, f"{verbs[st]} at cycle {c}"))

        first_lesson = next((e for e in self.entries
                             if e.lesson and e.success is not False), None)
        if first_lesson:
            events.append((first_lesson.cycle, 10, f"first lesson at cycle {first_lesson.cycle}"))
        first_win = next((e for e in self.entries if e.success is True), None)
        if first_win:
            events.append((first_win.cycle, 11, f"first step taken in earnest at cycle {first_win.cycle}"))
        first_retreat = next((e for e in self.entries if e.success is False), None)
        if first_retreat:
            events.append((first_retreat.cycle, 12, f"first time you turned away, at cycle {first_retreat.cycle}"))

        faced = self._faced_shadow()
        if faced is not None:
            events.append((faced, 13, f"faced the Shadow at cycle {faced}"))
        through = next((e for e in self.entries if e.stage == "ordeal" and e.success is True), None)
        if through:
            events.append((through.cycle, 14, f"came through the ordeal at cycle {through.cycle}"))
        mirror = next((e for e in self.entries if e.convergence >= 0.5), None)
        if mirror:
            events.append((mirror.cycle, 15,
                           f"inner and outer worlds began to mirror each other at cycle {mirror.cycle}"))
        events.sort()
        return [t for _, _, t in events]

    def _faced_shadow(self) -> Optional[int]:
        """First cycle where a Shadow was present and met rather than fled.

        Prefers the ordeal; a direct confrontation of a Shadow at any stage also
        counts.
        """
        for e in self.entries:
            if not e.shadows:
                continue
            act = e.action.lower()
            confronted = any(w in act for w in _FACE_WORDS) and not any(
                w in act for w in _AVOID_WORDS)
            if e.stage == "ordeal" and e.success is not False:
                return e.cycle
            if confronted and e.success is True:
                return e.cycle
        return None

    # -- control ---------------------------------------------------------
    def cycles_in(self, stage: str) -> int:
        st = normalize_stage(stage)
        return sum(1 for e in self.entries if e.stage == st)

    def should_close(self, convergence: float, goals_progress: float = 0.0) -> bool:
        """True when the journey has earned its ending.

        The story closes once the *return* has been reached (and lived in for at
        least ``min_return_cycles``) with high convergence — or with goals mostly
        fulfilled — and in any case after ``max_return_cycles`` cycles of
        homecoming. Independently, a guard closes it after ``max_chapters``
        chapters or ``max_cycles`` cycles so a journey can never run forever.
        """
        if not self.entries:
            return False
        if len(self.chapters()) >= self.max_chapters:
            return True
        if self.max_cycles is not None and len(self.entries) >= self.max_cycles:
            return True
        home = self.cycles_in("return")
        if home >= self.min_return_cycles:
            if convergence >= self.close_convergence or goals_progress >= self.close_goals:
                return True
        if self.max_return_cycles is not None and home >= self.max_return_cycles:
            return True
        return False

    def stage_hint(self, convergence: float) -> Optional[str]:
        """The stage the *next* scene should realise, or ``None`` before the
        journey has begun (let the generator open freely).

        Rules, in order:

        * The hint never skips a stage: it is at most ``furthest + 1``, so
          ordeal, reward and return always arrive in that order.
        * It moves forward as soon as convergence says the self is ready
          (the natural stage is ``int(convergence * 6)``, as in the generator).
        * Pacing: once the story has spent ``stage_dwell`` cycles in its
          furthest stage it is nudged one stage on, so a journey reaches the
          return within ``len(STAGES) * stage_dwell`` cycles even when
          convergence rises slowly.
        * It only looks back when the self actually slid back (convergence fell
          since the previous cycle and is below the furthest stage), and then
          by exactly one stage, once — the next hint goes forward again. It
          never returns more than one stage behind the furthest point reached.
        """
        if not self.entries:
            return None
        conv = max(0.0, min(0.999, float(convergence)))
        natural = int(conv * len(STAGES))
        furthest = STAGES.index(self.furthest_stage)  # type: ignore[arg-type]
        last_entry = self.entries[-1]
        last = STAGES.index(last_entry.stage)
        top = len(STAGES) - 1

        first_at_furthest = self.first_cycle_at(STAGES[furthest]) or 1
        dwell = len(self.entries) - first_at_furthest + 1
        if natural > furthest or dwell >= self.stage_dwell:
            return STAGES[min(top, furthest + 1)]

        prev_conv = self.entries[-2].convergence if len(self.entries) > 1 else conv
        slid_back = conv < prev_conv - 1e-9 and natural < furthest
        # The homecoming does not unwind: once the return has been lived, a dip
        # is part of coming home, not a reason to go back out.
        if slid_back and last == furthest and 0 < furthest < len(STAGES) - 1:
            return STAGES[furthest - 1]
        return STAGES[furthest]

    def to_dict(self) -> dict:
        return {
            "chapter": self.current_chapter(),
            "stage": self.stage,
            "furthest_stage": self.furthest_stage,
            "chapters": [{k: v for k, v in c.items() if k != "cycles"} for c in self.chapters()],
            "milestones": self.milestones(),
            "npcs_met": self.npcs_met(),
            "cycles": len(self.entries),
        }


def _npcs_in(record: CycleRecord) -> tuple[list[str], list[str], list[str]]:
    """NPC names that actually took part in a cycle (spoke or reacted).

    The world object is shared and mutated across cycles, so its NPC list is used
    only as a name dictionary; participation comes from the scene's dialogue
    speakers and the NPC reaction lines.
    """
    world_npcs = list(record.request.world.npcs) if record.request else []
    by_name = {n.name: n for n in world_npcs}
    names: list[str] = []
    shadows: list[str] = []
    mentors: list[str] = []

    def add(name: str, arch: Optional[Archetype]) -> None:
        if name not in names:
            names.append(name)
        if arch == Archetype.SHADOW and name not in shadows:
            shadows.append(name)
        if arch == Archetype.MENTOR and name not in mentors:
            mentors.append(name)

    for b in record.scene.beats:
        if b.kind == "dialogue" and b.speaker and b.speaker in by_name:
            add(b.speaker, b.archetype or by_name[b.speaker].archetype)
    events = " \n ".join(record.outcome.npc_events)
    # Longest names first so "Sefa 2" is not swallowed by "Sefa".
    for name in sorted(by_name, key=len, reverse=True):
        if re.search(rf"(^|\n|\s){re.escape(name)}(\s|$)", events):
            add(name, by_name[name].archetype)
    return names, shadows, mentors


# ─────────────────────────────────────────────────────────────
# Debrief
# ─────────────────────────────────────────────────────────────


class Debrief(BaseModel):
    """A reflection handed back to the person when a journey (or a milestone)
    closes — the blueprint's "debrief or summary ... in a narrative form"."""

    title: str
    arc_summary: str
    chapters: list[str] = Field(default_factory=list)
    turning_points: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    how_you_changed: list[str] = Field(default_factory=list)
    recurring_symbols: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    closing_image: str = ""
    companions: list[str] = Field(default_factory=list)   # who walked with you
    source: str = "deterministic"                         # or "llm"

    def to_markdown(self) -> str:
        out: list[str] = [f"# {self.title}", "", self.arc_summary.strip(), ""]

        def section(head: str, items: list[str]) -> None:
            if items:
                out.append(f"## {head}")
                out.append("")
                out.extend(f"- {i}" for i in items)
                out.append("")

        section("The chapters", self.chapters)
        section("Turning points", self.turning_points)
        section("What you learned", self.lessons)
        section("How you changed", self.how_you_changed)
        section("Who walked with you", self.companions)
        section("Symbols that kept returning", self.recurring_symbols)
        section("Questions to carry home", self.open_questions)
        if self.closing_image:
            out.append(f"> {self.closing_image.strip()}")
            out.append("")
        return "\n".join(out).rstrip() + "\n"


_STR_LIST = {"type": "array", "items": {"type": "string"}}

DEBRIEF_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "arc_summary": {"type": "string"},
        "chapters": _STR_LIST,
        "turning_points": _STR_LIST,
        "lessons": _STR_LIST,
        "how_you_changed": _STR_LIST,
        "recurring_symbols": _STR_LIST,
        "open_questions": _STR_LIST,
        "closing_image": {"type": "string"},
        "companions": _STR_LIST,
    },
    "required": ["title", "arc_summary", "chapters", "turning_points", "lessons",
                 "how_you_changed", "recurring_symbols", "open_questions",
                 "closing_image", "companions"],
    "additionalProperties": False,
}

_DEBRIEF_SYSTEM = """\
You are the reflective voice of ANA, a simulation in which a person's inner
identity and a symbolic world shaped each other across a Hero's Journey. The
journey has reached a milestone and you are writing the debrief the person reads
afterwards — the way a wise companion would talk them through what happened.

You are given a factual digest of the journey and a plain draft debrief. Rewrite
it so it reads as a warm, specific reflection addressed to the person as "you":
- Stay strictly faithful to the facts: the same chapters in the same order, the
  same lessons, the same numeric changes, the same symbols and people. Invent no
  events, names or numbers.
- Be concrete — name the places, the people and the objects that recurred — and
  tender without flattering. Setbacks are part of the story, not failures.
- Never diagnose, label, or give clinical advice. Speak in the language of the
  story.
- `arc_summary` is one paragraph of 3-6 sentences. List items are one sentence
  each. `open_questions` are genuine questions the person might carry home.
  `closing_image` is a single sensory sentence to end on.
"""


def _themes(records: list[CycleRecord]) -> list[str]:
    counts: dict[str, int] = {}
    for r in records:
        for t in [r.scene.theme] + list(r.outcome.themes_touched):
            if t and t not in ("rest",):
                counts[t] = counts.get(t, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _symbol_counts(records: list[CycleRecord]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    order: dict[str, int] = {}
    for i, r in enumerate(records):
        per = {s for s in list(r.scene.symbols) + list(r.outcome.symbols) if s}
        for s in per:
            counts[s] = counts.get(s, 0) + 1
            order.setdefault(s, i)
    return sorted(counts.items(), key=lambda kv: (-kv[1], order[kv[0]]))


def _nice(name: str) -> str:
    v = name
    for suffix in ("_confidence", "_level", "_score"):
        if v.endswith(suffix):
            v = v[: -len(suffix)] + " " + suffix[1:]
    return v.replace("_", " ").strip()


def _how_changed(first: IdentitySnapshot, last: IdentitySnapshot) -> list[str]:
    out: list[str] = []
    before = {t.name: t.value for t in first.traits}
    after = {t.name: t.value for t in last.traits}
    deltas: list[tuple[float, str]] = []
    for name in list(dict.fromkeys(list(before) + list(after))):
        b, a = before.get(name), after.get(name)
        if b is None and a is not None:
            deltas.append((a, f"Something new took root: {_nice(name)} appeared and "
                              f"grew to {a:.2f}."))
            continue
        if a is None or b is None:
            continue
        d = a - b
        if abs(d) < 0.01:
            continue
        verb = "grew" if d > 0 else "softened"
        deltas.append((abs(d), f"Your {_nice(name)} {verb} from {b:.2f} to {a:.2f} "
                               f"({d:+.2f})."))
    deltas.sort(key=lambda x: -x[0])
    out.extend(t for _, t in deltas)

    e0, e1 = first.emotion, last.emotion
    dv = e1.valence - e0.valence
    if abs(dv) >= 0.05:
        out.append(
            f"Your mood {'brightened' if dv > 0 else 'darkened'} "
            f"(valence {e0.valence:+.2f} to {e1.valence:+.2f})."
        )
    ds = e1.stress - e0.stress
    if abs(ds) >= 0.05:
        out.append(
            f"You carried {'less' if ds < 0 else 'more'} strain "
            f"(stress {e0.stress:.2f} to {e1.stress:.2f})."
        )
    if e0.dominant != e1.dominant:
        out.append(f"The feeling at the front shifted from {e0.dominant.value} "
                   f"to {e1.dominant.value}.")
    dc = last.convergence - first.convergence
    if dc >= 0.05:
        out.append(f"Inner and outer drew closer together: convergence rose from "
                   f"{first.convergence:.2f} to {last.convergence:.2f}.")
    new_beliefs = [b for b in last.beliefs if b not in first.beliefs]
    for b in new_beliefs[:2]:
        out.append(f"You came to hold a new belief: \"{b}\".")
    for g0 in first.goals:
        g1 = next((g for g in last.goals if g.description == g0.description), None)
        if g1 and g1.progress - g0.progress >= 0.05:
            out.append(f"You moved toward \"{g0.description}\" "
                       f"({g0.progress:.0%} to {g1.progress:.0%}).")
    if not out:
        out.append("On the surface little moved — sometimes a journey's work is "
                   "quiet, and shows itself later.")
    return out


def _cast_lines(cast: Optional[list[dict]]) -> list[str]:
    lines: list[str] = []
    for c in cast or []:
        name = c.get("name", "someone")
        role = c.get("role", "")
        met = c.get("times_met", 0)
        bits = [f"{name}"]
        if role:
            bits.append(f"the {role}")
        line = ", ".join(bits)
        trend = c.get("trend")
        notes: list[str] = []
        if met:
            notes.append(f"met {met} time{'s' if met != 1 else ''}")
        if trend == "warmer":
            notes.append("grew warmer toward you")
        elif trend == "cooler":
            notes.append("grew more distant")
        if c.get("fled"):
            notes.append("once fled when you called")
        if c.get("faced"):
            notes.append("was faced, not fled")
        if c.get("callbacks"):
            notes.append("returned at " + ", ".join(c["callbacks"]))
        if notes:
            line += " — " + "; ".join(notes)
        lines.append(line + ".")
    return lines


def build_debrief(records: list[CycleRecord], gateway: Any = None,
                  use_llm: bool = False, *, arc: Optional[JourneyArc] = None,
                  cast: Optional[list[dict]] = None,
                  stages: Optional[list[Optional[str]]] = None) -> Debrief:
    """Turn a journey's cycle records into a :class:`Debrief`.

    Args:
        records: the run's cycle records, in order.
        gateway: optional model gateway (anything with
            ``structured(system, user, schema, effort=...)``).
        use_llm: ask the model to rewrite the deterministic debrief. Any
            ``None``/malformed reply falls back to the deterministic text.
        arc: an already-maintained :class:`JourneyArc` (else one is built from
            ``records``; explicit ``stages`` per record may be supplied).
        cast: ``NPCDirector.cast_summary()`` output, used for companions.
    """
    det = _deterministic_debrief(records, arc=arc, cast=cast, stages=stages)
    if not (use_llm and gateway is not None and records):
        return det
    if getattr(gateway, "available", True) is False:
        return det
    try:
        data = gateway.structured(
            _DEBRIEF_SYSTEM,
            _debrief_prompt(records, det, arc or _arc_for(records, stages)),
            DEBRIEF_SCHEMA,
            effort="medium",
        )
    except Exception:
        data = None
    if not isinstance(data, dict):
        return det
    return _merge_llm(det, data)


def _arc_for(records: list[CycleRecord], stages: Optional[list[Optional[str]]]) -> JourneyArc:
    arc = JourneyArc(max_cycles=None, max_chapters=10_000)
    for i, r in enumerate(records):
        arc.record(r, stages[i] if stages and i < len(stages) else None)
    return arc


def _deterministic_debrief(records: list[CycleRecord], *, arc: Optional[JourneyArc],
                           cast: Optional[list[dict]],
                           stages: Optional[list[Optional[str]]]) -> Debrief:
    if not records:
        return Debrief(
            title="A journey not yet begun",
            arc_summary="Nothing has happened yet — the road is still waiting for "
                        "your first step.",
            closing_image="A door, unopened, with light underneath it.",
        )
    arc = arc if arc is not None and arc.entries else _arc_for(records, stages)
    first, last = records[0], records[-1]
    themes = _themes(records)
    symbols = _symbol_counts(records)
    recurring = [s for s, n in symbols if n >= 2]
    n = len(records)

    # Title — the most persistent non-stage symbol + the leading themes.
    stage_syms = set(STAGE_SYMBOLS.values())
    emblem = next((s for s, c in symbols if s not in stage_syms and c >= 2), None)
    theme_phrase = " and ".join(themes[:2]) if themes else "becoming"
    title = (f"{emblem[0].upper() + emblem[1:]}: a journey through {theme_phrase}"
             if emblem else f"A journey through {theme_phrase}")

    # Chapters.
    chapter_lines: list[str] = []
    by_cycle = {e.cycle: (e, records[e.cycle - 1]) for e in arc.entries
                if e.cycle - 1 < n}
    for i, ch in enumerate(arc.chapters(), 1):
        span = (f"cycle {ch['start']}" if ch["start"] == ch["end"]
                else f"cycles {ch['start']}-{ch['end']}")
        entries = [by_cycle[c][0] for c in ch["cycles"] if c in by_cycle]
        recs = [by_cycle[c][1] for c in ch["cycles"] if c in by_cycle]
        places = list(dict.fromkeys(r.request.world.location for r in recs
                                    if r.request))
        wins = sum(1 for e in entries if e.success is True)
        turns = sum(1 for e in entries if e.success is False)
        people = list(dict.fromkeys(p for e in entries for p in e.npcs))[:2]
        desc = _chapter_prose(ch["stage"], wins, turns, people)
        where = ""
        if recs:
            loc = recs[0].scene.summary
            m = re.match(r"At (.+?), the protagonist", loc)
            where = f" at {m.group(1)}" if m else ""
        chapter_lines.append(f"Chapter {i}, {ch['name']} ({span}){where}: {desc}")

    # Turning points — milestones made personal, plus the sharpest moments.
    turning: list[str] = []
    for m in arc.milestones():
        turning.append(m[0].upper() + m[1:] + ".")
    peak = max(records, key=lambda r: (r.scene.intensity, -r.tick))
    pi = records.index(peak) + 1
    peak_action = peak.outcome.user_action.raw if peak.outcome.user_action else ""
    if peak.scene.intensity >= 0.45:
        turning.append(
            f"The most charged moment came at cycle {pi}, \"{peak.scene.title}\""
            + (f" — and what you did was: \"{peak_action}\"" if peak_action else "")
            + ".")
    surprise = max(records, key=lambda r: r.omega_surprise)
    if surprise.omega_surprise > 0.0 and surprise is not peak:
        si = records.index(surprise) + 1
        turning.append(f"Cycle {si} surprised the world most — \"{surprise.scene.title}\" "
                       f"did not go the way the pattern expected.")

    # Lessons — only the ones earned; retreats become open questions.
    lessons: list[str] = []
    retreats: list[str] = []
    for r in records:
        les = r.outcome.lesson
        if not les:
            continue
        if r.outcome.success is False or "turned away" in les.lower():
            if les not in retreats:
                retreats.append(les)
        elif les not in lessons:
            lessons.append(les)

    changed = _how_changed(first.identity_before, last.identity_before)

    # Open questions.
    questions: list[str] = []
    for les in retreats:
        m = re.search(r"turned away from (.+?) this time", les)
        if m:
            questions.append(f"What made you turn away from {m.group(1)} — and what "
                             f"would let you stay with it next time?")
    for g in last.identity_before.goals:
        if g.progress < 0.95:
            questions.append(f"\"{g.description}\" is {g.progress:.0%} of the way — "
                             f"what would the next honest step toward it look like?")
    if arc.reached("ordeal") and arc._faced_shadow() is None:
        questions.append("The Shadow was glimpsed but not yet faced. What is it "
                         "still asking of you?")
    if not arc.reached("return"):
        questions.append("The road home has not been walked yet. What would you "
                         "need to carry back with you?")
    low = sorted(last.identity_before.traits, key=lambda t: t.value)
    if low and low[0].value < 0.4:
        questions.append(f"Your {_nice(low[0].name)} is still finding its feet. "
                         f"Where in ordinary life could it practise?")
    questions = list(dict.fromkeys(questions))[:5]

    # Arc summary — the reflection itself.
    start_place = first.request.world.location if first.request else "the beginning"
    start_mood = first.identity_before.emotion.dominant.value
    stages_seen = [CHAPTER_NAMES[c["stage"]].lower() for c in arc.chapters()]
    path = ", ".join(dict.fromkeys(stages_seen))
    wins = sum(1 for r in records if r.outcome.success is True)
    turns = sum(1 for r in records if r.outcome.success is False)
    parts = [f"You set out from {start_place} carrying {start_mood} more than "
             f"certainty, and over {n} cycle{'s' if n != 1 else ''} the story "
             f"moved through {path}."]
    if themes:
        parts.append(f"Again and again the world asked you about "
                     f"{' and '.join(themes[:2])}.")
    if wins or turns:
        txt = f"You stepped toward it {wins} time{'s' if wins != 1 else ''}"
        if turns:
            txt += (f" and turned away {turns} time{'s' if turns != 1 else ''} — "
                    f"both are part of how a person learns where their edges are")
        parts.append(txt + ".")
    if emblem:
        cnt = dict(symbols)[emblem]
        parts.append(f"{emblem[0].upper() + emblem[1:]} kept appearing — in {cnt} of "
                     f"{n} scenes — as if the world wanted you to look at it.")
    if lessons:
        parts.append(f"The clearest thing you said to yourself was: \"{lessons[0]}\"")
    if changed and "grew" in changed[0]:
        parts.append(changed[0].replace("Your ", "Your ", 1))
    summary = " ".join(parts)

    end_place = last.request.world.location if last.request else "the road"
    tail_sym = next((s for s, _ in symbols if s not in stage_syms), None)
    if arc.reached("return"):
        closing = (f"At {end_place}, the road bends homeward; "
                   f"{tail_sym or 'what you found'} goes with you, and it looks "
                   f"different than it did at the start.")
    else:
        closing = (f"For now you rest at {end_place}, "
                   f"{tail_sym or 'the next door'} still within sight, the story "
                   f"waiting for you to pick it up again.")

    companions = _cast_lines(cast) if cast else [
        f"{name}, who was there along the way." for name in arc.npcs_met()[:5]]

    return Debrief(
        title=title,
        arc_summary=summary,
        chapters=chapter_lines,
        turning_points=turning,
        lessons=lessons,
        how_you_changed=changed,
        recurring_symbols=recurring,
        open_questions=questions,
        closing_image=closing,
        companions=companions,
        source="deterministic",
    )


def _chapter_prose(stage: str, wins: int, turns: int, people: list[str]) -> str:
    base = {
        "call": "something called to you, and you had to decide whether to listen",
        "threshold": "the familiar fell behind and the way narrowed to a single door",
        "mentor": "help arrived in a form you did not expect",
        "ordeal": "what you had been avoiding gathered in one place and would not be deferred",
        "reward": "the thing you came for was finally within reach",
        "return": "the road turned homeward, and you carried the journey with you",
    }[stage]
    if wins and not turns:
        tail = "you met it"
    elif turns and not wins:
        tail = "you held back, and that too told you something"
    elif wins and turns:
        tail = "you met it some days and stepped back on others"
    else:
        tail = "you moved through it, watching"
    with_ = f", with {' and '.join(people)} nearby" if people else ""
    return f"{base}{with_}; {tail}."


def _debrief_prompt(records: list[CycleRecord], det: Debrief, arc: JourneyArc) -> str:
    digest = []
    for i, r in enumerate(records, 1):
        e = arc.entries[i - 1] if i - 1 < len(arc.entries) else None
        digest.append({
            "cycle": i,
            "stage": e.stage if e else None,
            "title": r.scene.title,
            "location": r.request.world.location if r.request else None,
            "theme": r.scene.theme,
            "action": r.outcome.user_action.raw if r.outcome.user_action else None,
            "success": r.outcome.success,
            "lesson": r.outcome.lesson,
            "npc_reactions": r.outcome.npc_events[:3],
            "symbols": r.scene.symbols[:5],
            "convergence": round(r.convergence, 2),
        })
    draft = det.model_dump(exclude={"source"})
    return ("Journey digest (facts, in order):\n"
            + json.dumps(digest, ensure_ascii=False, indent=1)
            + "\n\nMilestones: " + json.dumps(arc.milestones(), ensure_ascii=False)
            + "\n\nDraft debrief to rewrite (keep every fact):\n"
            + json.dumps(draft, ensure_ascii=False, indent=1))


def _merge_llm(det: Debrief, data: dict) -> Debrief:
    """Take the model's fields where they are well-formed, else keep ours."""

    def s(key: str) -> str:
        v = data.get(key)
        return v.strip() if isinstance(v, str) and v.strip() else getattr(det, key)

    def lst(key: str) -> list[str]:
        v = data.get(key)
        if isinstance(v, list):
            items = [str(x).strip() for x in v if isinstance(x, (str, int, float)) and str(x).strip()]
            if items:
                return items
        return list(getattr(det, key))

    return Debrief(
        title=s("title"),
        arc_summary=s("arc_summary"),
        chapters=lst("chapters"),
        turning_points=lst("turning_points"),
        lessons=lst("lessons"),
        how_you_changed=lst("how_you_changed"),
        recurring_symbols=lst("recurring_symbols"),
        open_questions=lst("open_questions"),
        closing_image=s("closing_image"),
        companions=lst("companions"),
        source="llm",
    )


__all__ = [
    "STAGES", "STAGE_LABELS", "CHAPTER_NAMES", "JourneyArc", "ArcEntry",
    "Debrief", "DEBRIEF_SCHEMA", "build_debrief", "infer_stage", "normalize_stage",
]
