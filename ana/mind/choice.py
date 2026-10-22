"""
Choice: every option is scored from the twin's actual state (plan §3.11).

    U = needs + learned + character + goals + people + novelty − risk + mood + influence + noise

A language model may *voice* the decision afterwards, but the decision itself
is computed here, so it follows from persistent state that can be inspected,
replayed and tested.  Every part of the score is kept so the log can say why.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from ..temperament.temperament import Temperament
from ..world.town import ACTIONS, PLACES
from ..world.world import Option, in_hours
from .state import Drives, Goal, Relationship

SOCIAL_KINDS = {"talk", "help"}
NOVEL_KINDS = {"explore", "climb", "read", "sailing", "walk", "go"}
SOLITARY_KINDS = {"rest", "write", "walk", "read"}
ROUTINE_KINDS = {"sleep", "rest", "cook", "buy_food", "go"}


@dataclass
class Traits:
    openness: float = 0.5
    conscientiousness: float = 0.5
    extraversion: float = 0.5
    agreeableness: float = 0.5
    neuroticism: float = 0.5


@dataclass
class Context:
    """Everything a decision may depend on.  Built by the Mind each time it deliberates."""

    drives: Drives
    traits: Traits
    temperament: Temperament
    relationships: Dict[str, Relationship]
    goals: List[Goal]
    pleasure: float
    hour: int
    money: float
    habits: Dict[str, int]  # recent count per action kind
    expectations: Dict[str, float]  # learned correction per option key
    suggestions: Dict[str, float]  # action kind → pull (negative = pushes away)
    responding_to: Optional[str] = None  # npc id who approached, or "friend"
    sightings: Dict[str, Set[tuple[str, int]]] = field(default_factory=dict)  # npc → {(place, hour)}
    rng: random.Random = field(default_factory=random.Random)


@dataclass
class Scored:
    key: str
    kind: str
    utility: float
    parts: Dict[str, float]
    option: Optional[Option] = None  # None for "continue"

    def reasons(self, n: int = 3) -> List[str]:
        top = sorted(((v, k) for k, v in self.parts.items() if k != "noise"), key=lambda t: -abs(t[0]))[:n]
        return [f"{k} {v:+.2f}" for v, k in top]


def expected_effects(option: Option, ctx: Context) -> Dict[str, float]:
    effects = dict(option.spec.effects)
    if option.target and option.target in ctx.relationships:
        # Social payoff is a guess built from how it has gone with them before.
        rel = ctx.relationships[option.target]
        trust_in_it = 0.55 + 0.45 * rel.affection
        effects = {k: v * trust_in_it if v > 0 else v for k, v in effects.items()}
    return effects


def score(option: Option, ctx: Context, *, lookahead: bool = True) -> Scored:
    spec = option.spec
    kind = spec.kind
    parts: Dict[str, float] = {}
    t = ctx.traits
    tired = ctx.drives.level["energy"] < 0.2

    parts["needs"] = ctx.drives.relief(expected_effects(option, ctx))
    parts["learned"] = 0.6 * ctx.expectations.get(option.key, ctx.expectations.get(kind, 0.0))

    character = 0.0
    if kind in SOCIAL_KINDS:
        character += 0.15 * (t.extraversion - 0.5)
    if kind == "help":
        character += 0.15 * (t.agreeableness - 0.5)
    if kind in NOVEL_KINDS:
        character += 0.12 * (t.openness - 0.5)
    if kind == "work":
        character += 0.12 * (t.conscientiousness - 0.5)
    parts["character"] = character

    goal_pull = sum(0.22 * g.importance for g in ctx.goals if g.status == "active" and g.advances(option.key, kind))
    parts["goals"] = goal_pull * (0.5 if tired else 1.0)

    people = 0.0
    if option.target:
        rel = ctx.relationships.get(option.target)
        if rel is None:
            people += 0.06 * t.extraversion  # someone new
        else:
            people += 0.2 * rel.affection + (0.04 if rel.familiarity < 0.15 else 0.0)
        if ctx.responding_to == option.target:
            people += 0.25
    if kind == "message_friend" and ctx.responding_to == "friend":
        people += 0.2
    # Even people we love are less pressing when we're not lonely.
    parts["people"] = people * (0.35 + (1.0 - ctx.drives.level["connection"]))

    if kind not in ROUTINE_KINDS or option.move_to:
        novelty = ctx.temperament.omega * 0.14 / (1 + ctx.habits.get(kind, 0))
        parts["novelty"] = novelty * (0.5 if tired else 1.0)
    parts["risk"] = -ctx.temperament.alpha * spec.risk * (1 + t.neuroticism) * 2.0

    mood = 0.0
    if ctx.pleasure < -0.2:
        if kind in SOLITARY_KINDS:
            mood += 0.06
        if kind in SOCIAL_KINDS and t.extraversion < 0.5:
            mood -= 0.06
    parts["mood"] = mood

    parts["influence"] = ctx.suggestions.get(kind, 0.0)

    circadian = 0.0
    if kind == "sleep":
        if ctx.hour >= 22 or ctx.hour < 6:
            circadian += 0.35
        elif 10 <= ctx.hour < 20:
            circadian -= 0.6
    if spec.cost and ctx.money < spec.cost * 3:
        circadian -= 0.08
    if kind == "work" and ctx.money < 60:
        circadian += 0.12 * (0.5 + ctx.drives.weight["security"])
    parts["situation"] = circadian

    if option.move_to and lookahead:
        parts["destination"] = _destination_value(option.move_to, ctx, option.spec.ticks)
        parts["needs"] = 0.0

    parts["noise"] = ctx.rng.gauss(0.0, 0.025 + 0.04 * ctx.temperament.omega)
    return Scored(option.key, kind, sum(parts.values()), parts, option)


def _destination_value(place: str, ctx: Context, here_to_there: int) -> float:
    """Going somewhere is worth the best thing to do there, discounted by the walk."""
    return _best_at(place, ctx, here_to_there) * 0.9 ** here_to_there - 0.02 * here_to_there


def _best_at(place: str, ctx: Context, hops: int = 0) -> float:
    """How good the best thing to do at ``place`` would be, from what the twin knows."""
    hour = (ctx.hour + (hops + 1) // 2) % 24
    if not in_hours(hour, PLACES[place].hours):
        return -0.2
    candidates: List[Option] = []
    for spec in ACTIONS:
        if spec.kind == "message_friend":
            continue  # can be done anywhere; not a reason to go somewhere
        if spec.places is not None and place not in spec.places:
            continue
        if not in_hours(hour, spec.hours):
            continue
        if spec.social:
            for npc, seen in ctx.sightings.items():
                if any(p == place and abs(h - hour) <= 1 for p, h in seen):
                    candidates.append(Option(spec, target=npc))
        else:
            candidates.append(Option(spec))
    if not candidates:
        return -0.1
    quiet = Context(**{**ctx.__dict__, "rng": random.Random(0)})
    best = float("-inf")
    for o in candidates:
        s = score(o, quiet, lookahead=False)
        best = max(best, s.utility - s.parts["noise"])
    return best


def choose(options: List[Option], ctx: Context, continuing: Optional[Scored] = None) -> tuple[Scored, List[Scored]]:
    """Score everything, pick the best.  Returns (chosen, all scored, best first)."""
    scored = [score(o, ctx) for o in options]
    if continuing is not None:
        scored.append(continuing)
    scored.sort(key=lambda s: -s.utility)
    return scored[0], scored
