"""Identity Core ("Me") — the digital psyche ANA maintains.

Answers "who is the protagonist right now?" and provides the snapshot the whole
system builds the world from. It holds structured self-state (traits, beliefs,
goals, emotion) and owns the Memory Shard System. Every cycle's Outcome is
folded back in here — this is where learning and adaptation live.

Depth added on top of the basic loop:

  * **Meaning-aware goals.** Each goal carries theme keywords derived at
    creation from a small lexicon ("become someone who can lead" -> leadership)
    plus its own content words. Progress moves when outcomes touch those
    themes, weighted by success, scene intensity and whether a lesson was
    learned; avoidance does not advance (and slightly regresses near-term goals).
  * **Self-remembering.** A `Journal` records every cycle; its meta-analysis
    ("growing in leadership (3 of 4 encounters met)") and recurring symbols are
    surfaced in the snapshot alongside the most relevant Prime lessons.
  * **Memories of the future, fulfilled.** Future-memory goals are strengthened
    in memory each time the self moves toward them; when a goal crosses the
    fulfilment threshold it is marked realised, its original future shard is
    reinforced and an "I became ..." lesson shard is written and linked to it.
"""
from __future__ import annotations

import re
from typing import Optional

from ..contracts import (
    EmotionalState,
    Goal,
    IdentitySnapshot,
    Outcome,
    Trait,
)
from .journal import Journal
from .memory import ShardMemory

# ─────────────────────────────────────────────────────────────
# Theme lexicon — word stems that point at a canonical theme
# (single words match as prefixes of tokens; multi-word cues as substrings)
# ─────────────────────────────────────────────────────────────

THEME_LEXICON: dict[str, tuple[str, ...]] = {
    "leadership": ("lead", "follow", "charge", "command", "captain", "responsib",
                   "organiz", "organis", "guide"),
    "purpose": ("purpose", "meaning", "calling", "useful", "mission", "vocation"),
    "courage": ("courage", "brave", "fear", "afraid", "bold", "dare", "daring"),
    "trust": ("trust", "believe", "belief", "rely", "reliable", "faith", "depend"),
    "home": ("home", "belong", "family", "roots", "hearth"),
    "connection": ("connect", "friend", "love", "together", "companion", "lonel",
                   "alone", "community"),
    "freedom": ("free", "release", "let go", "escape", "unbound"),
    "grief": ("grief", "griev", "loss", "mourn"),
    "wisdom": ("wisdom", "wise", "learn", "understand", "insight", "listen"),
    "shame": ("shame", "ashamed", "guilt", "hiding", "worthless", "worthy"),
    "identity": ("myself", "who i am", "identity", "authentic"),
    "creativity": ("create", "creativ", "build", "write", "invent"),
    "forgiveness": ("forgiv", "mercy", "reconcil"),
    "healing": ("heal", "recover", "mend"),
}

_STOPWORDS = frozenset("""
a an the and or but of to in on at for with by from into onto about as is are be
been being was were am who whom whose that this these those it its my me i you your
we our they their them he she his her can could will would should shall may might
must do does did done have has had become becomes became becoming someone something
anyone everyone one some more most very really just own get got make made find
found way able want wants wanted let lets than then so not no yes over under again
""".split())

# Progress weights.
_BASE_STEP = {True: 0.12, None: 0.04, False: 0.0}
_HORIZON_PACE = {"near": 1.25, "mid": 1.0, "life": 0.7}
_NEAR_REGRESS = 0.03
_LESSON_BONUS = 0.04
FULFILMENT_THRESHOLD = 0.6


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z']*", text.lower())


def canonical_themes(text: str) -> list[str]:
    """Canonical themes a piece of text points at, via the lexicon."""
    low = text.lower().replace("_", " ").replace("-", " ")
    toks = _tokens(low)
    out: list[str] = []
    for theme, cues in THEME_LEXICON.items():
        hit = theme in toks
        if not hit:
            for cue in cues:
                if " " in cue:
                    hit = cue in low
                else:
                    hit = any(tok.startswith(cue) for tok in toks)
                if hit:
                    break
        if hit:
            out.append(theme)
    return out


def derive_goal_themes(description: str) -> list[str]:
    """Theme keywords for a goal: canonical lexicon themes first, then the goal's
    own content words (non-stopwords, length >= 3)."""
    themes = canonical_themes(description)
    for tok in _tokens(description):
        tok = tok.strip("'")
        if len(tok) >= 3 and tok not in _STOPWORDS and tok not in themes:
            themes.append(tok)
    return themes


def _match_strength(goal_themes: list[str], touched: str) -> float:
    """How strongly a touched theme bears on a goal: 1.0 for a canonical theme
    match, 0.6 for a content-word match, 0 otherwise."""
    t = touched.lower().strip()
    if not t:
        return 0.0
    canon = set(canonical_themes(t))
    gset = set(goal_themes)
    if canon & gset:
        return 1.0
    words = {w for w in _tokens(t) if w not in _STOPWORDS}
    if t in gset or words & gset:
        return 0.6
    return 0.0


_PAST = {"become": "became", "find": "found", "be": "was", "learn": "learned",
         "lead": "led", "build": "built", "make": "made", "feel": "felt",
         "forgive": "forgave", "go": "went", "get": "got", "grow": "grew",
         "know": "knew", "stand": "stood", "speak": "spoke", "take": "took",
         "give": "gave", "come": "came", "trust": "trusted", "heal": "healed",
         "return": "returned", "protect": "protected", "face": "faced",
         "accept": "accepted", "belong": "belonged", "overcome": "overcame",
         "earn": "earned", "keep": "kept", "let": "let", "hold": "held"}


def first_person_past(description: str) -> str:
    """'become someone who can lead' -> 'I became someone who can lead.'"""
    d = description.strip().rstrip(".")
    low = d.lower()
    for prefix in ("i will ", "i want to ", "i'll ", "to "):
        if low.startswith(prefix):
            d = d[len(prefix):]
            break
    parts = d.split(" ", 1)
    verb = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if verb in _PAST:
        return f"I {_PAST[verb]} {rest}".strip() + "."
    return f"I lived into my vision: {d}."


# ─────────────────────────────────────────────────────────────


class IdentityCore:
    def __init__(self, *, name: str = "the protagonist",
                 memory: Optional[ShardMemory] = None):
        self.name = name
        self.tick = 0
        self.traits: dict[str, Trait] = {}
        self.beliefs: list[str] = []
        self.goals: list[Goal] = []
        self.emotion = EmotionalState()
        self.active_themes: list[str] = []
        self.convergence = 0.0
        self.memory = memory or ShardMemory()
        self.journal = Journal()
        # Fulfilment statements ("I became ...") in the order they happened.
        self._fulfilled: list[str] = []

    # -- seeding ---------------------------------------------------------
    def set_trait(self, name: str, value: float, confidence: float = 0.6) -> None:
        self.traits[name] = Trait(name=name, value=max(0.0, min(1.0, value)),
                                  confidence=confidence)

    def add_belief(self, belief: str) -> None:
        self.beliefs.append(belief)
        self.memory.record_identity(f"Belief: {belief}", tag="kind:belief")

    def add_goal(self, description: str, horizon: str = "mid",
                 is_future_memory: bool = False) -> None:
        themes = derive_goal_themes(description)
        goal = Goal(description=description, horizon=horizon,
                    is_future_memory=is_future_memory, themes=themes)
        self.goals.append(goal)
        if is_future_memory:
            canon = [t for t in themes if t in THEME_LEXICON]
            self.memory.record_future_memory(
                description, theme=canon[0] if canon else _first_theme(description))

    def seed_memory(self, summary: str, *, themes: list[str] | None = None,
                    symbols: list[str] | None = None) -> None:
        self.memory.record_identity(summary)
        if themes:
            for t in themes:
                if t not in self.active_themes:
                    self.active_themes.append(t)

    # -- self-remembering ------------------------------------------------
    def insights(self, limit: int = 4) -> list[str]:
        """Top meta-analysis statements over the journey so far."""
        return self.journal.meta_analysis(limit=limit)

    def fulfilled_visions(self) -> list[str]:
        """First-person statements of every vision realised so far
        ("I became someone who can lead.")."""
        return list(self._fulfilled)

    def goal_for_theme(self, theme: str) -> Optional[Goal]:
        """The unrealised goal a theme bears on most strongly (if any)."""
        best, best_s = None, 0.0
        for g in self.goals:
            if g.realised:
                continue
            s = _match_strength(g.themes or derive_goal_themes(g.description), theme)
            if s > best_s:
                best, best_s = g, s
        return best

    # -- snapshot --------------------------------------------------------
    def snapshot(self) -> IdentitySnapshot:
        """The read-only picture handed to the rest of the system each cycle."""
        query = " ".join(self.active_themes) or "who am i"
        lessons = self.memory.recall_lessons(query, top_k=2)
        recalled = self.memory.recall(query, top_k=4)
        summaries = list(dict.fromkeys(lessons + recalled))[:5]
        return IdentitySnapshot(
            tick=self.tick,
            traits=list(self.traits.values()),
            beliefs=list(self.beliefs),
            goals=[g.model_copy(deep=True) for g in self.goals],
            emotion=self.emotion.model_copy(),
            active_themes=list(self.active_themes),
            recent_shard_summaries=summaries,
            convergence=self.convergence,
            insights=self.insights(limit=4),
            recurring_symbols=[s for s, _ in self.journal.recurring_symbols(min_count=2)[:5]],
            callback_symbols=self.journal.callback_symbols(),
            fulfilled_visions=self.fulfilled_visions(),
        )

    # -- learning --------------------------------------------------------
    def apply_outcome(self, outcome: Outcome, *,
                      scene_intensity: float | None = None) -> None:
        """Fold a cycle's consequences back into the self (the feedback half).

        `scene_intensity` (0..1) weights goal progress; when omitted it is
        inferred from the outcome's arousal delta (the deterministic interpreter
        sets arousal = 0.1 + 0.3 * intensity), else a moderate 0.4.
        """
        # emotion
        d = outcome.emotion_delta
        self.emotion.valence += d.valence
        self.emotion.arousal += d.arousal
        self.emotion.stress += d.stress
        self.emotion.clamp()

        # traits
        for name, delta in outcome.trait_updates.items():
            cur = self.traits.get(name)
            base = cur.value if cur else 0.5
            self.set_trait(name, base + delta,
                           confidence=min(1.0, (cur.confidence if cur else 0.5) + 0.05))

        # themes
        for theme in outcome.themes_touched:
            if theme not in self.active_themes:
                self.active_themes.append(theme)

        # memory: record the event, and promote a lesson if one was learned
        self.memory.record_event(
            outcome.summary or "an experience",
            themes=outcome.themes_touched,
            symbols=outcome.symbols,
            emotion=self.emotion.dominant.value,
        )
        if outcome.lesson:
            self.memory.record_lesson(outcome.lesson, themes=outcome.themes_touched)

        # goal progress (meaning-aware) — after the lesson so "I became ..."
        # lands as the newest Prime shard.
        intensity = _intensity(outcome, scene_intensity)
        advanced = self._advance_goals(outcome, intensity)

        # journal
        self.journal.record(
            tick=self.tick,
            themes=outcome.themes_touched,
            success=outcome.success,
            lesson=outcome.lesson,
            symbols=outcome.symbols,
            trait_deltas=outcome.trait_updates,
            valence=self.emotion.valence,
            arousal=self.emotion.arousal,
            stress=self.emotion.stress,
            emotion=self.emotion.dominant.value,
            goals_advanced=advanced,
            intensity=intensity,
        )

    def _advance_goals(self, outcome: Outcome, intensity: float) -> list[str]:
        """Move goals whose themes the outcome touched. Returns advanced goals."""
        advanced: list[str] = []
        if not outcome.themes_touched:
            return advanced
        for goal in self.goals:
            if not goal.themes:
                goal.themes = derive_goal_themes(goal.description)
            strength = max((_match_strength(goal.themes, t) for t in outcome.themes_touched),
                           default=0.0)
            if strength <= 0.0:
                continue
            if outcome.success is False:
                # Avoidance never advances; near-term goals slip a little.
                if goal.horizon == "near" and not goal.realised:
                    goal.progress = max(0.0, goal.progress - _NEAR_REGRESS * strength)
                continue
            step = _BASE_STEP[outcome.success]
            if outcome.lesson:
                step += _LESSON_BONUS
            step *= strength * (0.75 + 0.5 * intensity)
            step *= _HORIZON_PACE.get(goal.horizon, 1.0)
            before = goal.progress
            goal.progress = min(1.0, goal.progress + step)
            if goal.progress > before:
                goal.advanced_at.append(self.tick)
                advanced.append(goal.description)
                if goal.is_future_memory and not goal.realised:
                    # The vision is being lived toward: strengthen it.
                    self.memory.reinforce_future(goal.description)
                self._check_fulfilment(goal, before)
        return advanced

    def _check_fulfilment(self, goal: Goal, before: float) -> None:
        themes = [t for t in goal.themes if t in THEME_LEXICON] or goal.themes[:1]
        if not goal.realised and goal.progress >= FULFILMENT_THRESHOLD:
            goal.realised = True
            goal.realised_at = self.tick
            statement = first_person_past(goal.description)
            self.memory.record_fulfilment(goal.description, statement, themes=themes)
            self._fulfilled.append(statement)
        if before < 1.0 <= goal.progress:
            # Fully embodied: a second, deeper lesson.
            whole = f"{first_person_past(goal.description)[:-1]}, and it is who I am now."
            self.memory.record_lesson(whole, themes=themes)

    def advance(self, convergence: float) -> None:
        self.tick += 1
        self.convergence = convergence

    def close(self) -> None:
        self.memory.close()


def _intensity(outcome: Outcome, explicit: float | None) -> float:
    if explicit is not None:
        return max(0.0, min(1.0, float(explicit)))
    a = outcome.emotion_delta.arousal
    if a > 0.0:
        return max(0.0, min(1.0, (a - 0.1) / 0.3))
    return 0.4


def _first_theme(text: str) -> str:
    return text.strip().split()[0].lower() if text.strip() else "self"
