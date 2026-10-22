"""
The first Habitat: a small coastal town (plan §4.1).

Pure data.  Places, what can be done where, and who lives here.  The rules
that turn these into outcomes live in ``world.py``.

Drive effects are per completed action, in drive-level units (0-1).  The world
decides what actually happens; the mind only sees these numbers as
*expectations* it learns to correct from experience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

TICK_MINUTES = 30
TICKS_PER_HOUR = 60 // TICK_MINUTES


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    description: str
    neighbors: Tuple[str, ...]
    hours: Optional[Tuple[int, int]] = None  # open hours [start, end); None = always
    outdoor: bool = False


@dataclass(frozen=True)
class ActionSpec:
    kind: str
    label: str
    places: Optional[Tuple[str, ...]]  # None = anywhere
    ticks: int
    effects: Dict[str, float] = field(default_factory=dict)
    hours: Optional[Tuple[int, int]] = None
    cost: float = 0.0
    pay: float = 0.0
    social: bool = False  # needs someone present to do it with
    risk: float = 0.0  # chance the world turns it against you
    min_energy: float = 0.0
    done: str = ""  # how the twin would say it afterwards; defaults to the label


PLACES: Dict[str, Place] = {
    p.id: p
    for p in [
        Place("home", "the rented room above the bakery", "A narrow room with a window that faces the sea.", ("cafe", "market")),
        Place("cafe", "Mara's café", "Six tables, a radio that only plays old songs, salt on the windows.", ("home", "harbor", "library"), hours=(7, 21)),
        Place("market", "the market square", "Stalls in the morning; pigeons and wind after noon.", ("home", "boatyard"), hours=(7, 14)),
        Place("library", "the library", "Too many books for a town this small, most about the sea.", ("cafe", "lighthouse"), hours=(9, 18)),
        Place("harbor", "the harbor", "Fishing boats, nets drying, gulls arguing.", ("cafe", "boatyard", "lighthouse"), outdoor=True),
        Place("boatyard", "Ivo's boatyard", "Hulls on trestles, the smell of tar and cut oak.", ("harbor", "market", "road"), hours=(8, 18)),
        Place("lighthouse", "the lighthouse", "Unlit for years. The door is never locked.", ("harbor", "library"), outdoor=True),
        Place("road", "the coast road out of town", "It runs north along the cliffs. Nobody says where it ends up.", ("boatyard",), outdoor=True),
    ]
}

ACTIONS: List[ActionSpec] = [
    ActionSpec("sleep", "sleep", ("home",), ticks=16, effects={"energy": 0.85}, hours=(21, 10), done="slept"),
    ActionSpec("rest", "rest for a while", ("home",), ticks=2, effects={"energy": 0.12, "autonomy": 0.02}, done="lay down for a while"),
    ActionSpec("cook", "cook something", ("home",), ticks=2, effects={"energy": 0.10, "security": 0.04, "competence": 0.02}, cost=4, done="cooked something simple"),
    ActionSpec("write", "write in my notebook", ("home", "cafe"), ticks=3, effects={"meaning": 0.08, "autonomy": 0.05, "competence": 0.03}, done="wrote in my notebook for a long time"),
    ActionSpec("message_friend", "write to my old friend", None, ticks=1, effects={"connection": 0.06, "meaning": 0.02}, done="wrote to my old friend"),
    ActionSpec("coffee", "have a coffee", ("cafe",), ticks=1, effects={"energy": 0.05, "connection": 0.03}, cost=3, done="had a coffee by the window"),
    ActionSpec("talk", "talk with someone", None, ticks=2, effects={"connection": 0.14, "novelty": 0.03}, social=True),
    ActionSpec("help", "help someone with what they're doing", None, ticks=3, effects={"meaning": 0.08, "connection": 0.08, "competence": 0.04, "energy": -0.06}, social=True),
    ActionSpec("buy_food", "buy food for the week", ("market",), ticks=1, effects={"security": 0.12}, cost=12, done="bought food for the week"),
    ActionSpec("work", "work a shift at the boatyard", ("boatyard",), ticks=8, effects={"competence": 0.14, "security": 0.10, "meaning": 0.04, "energy": -0.20, "autonomy": -0.06}, hours=(8, 17), pay=48, min_energy=0.25),
    ActionSpec("read", "read", ("library",), ticks=3, effects={"novelty": 0.10, "meaning": 0.05, "energy": -0.02}, done="read for hours"),
    ActionSpec("walk", "walk along the water", ("harbor", "road"), ticks=2, effects={"novelty": 0.04, "energy": -0.03, "autonomy": 0.03}, done="walked along the water"),
    ActionSpec("climb", "climb the lighthouse", ("lighthouse",), ticks=2, effects={"novelty": 0.10, "meaning": 0.06, "energy": -0.06}, risk=0.04, done="climbed the lighthouse and looked out at the sea"),
    ActionSpec("explore", "follow the coast road further than before", ("road",), ticks=6, effects={"novelty": 0.28, "autonomy": 0.10, "energy": -0.18}, risk=0.12, min_energy=0.3),
]

ACTIONS_BY_KIND: Dict[str, ActionSpec] = {a.kind: a for a in ACTIONS}


def _distances(start: str) -> Dict[str, int]:
    dist = {start: 0}
    frontier = [start]
    while frontier:
        nxt = []
        for p in frontier:
            for nb in PLACES[p].neighbors:
                if nb not in dist:
                    dist[nb] = dist[p] + 1
                    nxt.append(nb)
        frontier = nxt
    return dist


# Walking distance in ticks between any two places.
DISTANCES: Dict[str, Dict[str, int]] = {p: _distances(p) for p in PLACES}


@dataclass(frozen=True)
class NPCSpec:
    """Someone who lives here.  They have their own day and their own moods."""

    id: str
    name: str
    who: str
    warmth: float  # base disposition toward newcomers, 0-1
    schedule: Tuple[Tuple[int, int, str], ...]  # (start_hour, end_hour, place); else home (unreachable)
    busy: Tuple[Tuple[int, int], ...] = ()  # hours they'd rather not be interrupted
    values_competence: bool = False  # warms faster to people who work hard


NPCS: List[NPCSpec] = [
    NPCSpec(
        "mara", "Mara", "runs the café, talks to everyone, remembers every order",
        warmth=0.75, schedule=((7, 21, "cafe"),), busy=((7, 10),),
    ),
    NPCSpec(
        "ivo", "Ivo", "owns the boatyard; few words, exacting, fair",
        warmth=0.30, schedule=((8, 18, "boatyard"), (19, 21, "cafe")), busy=((8, 12),), values_competence=True,
    ),
    NPCSpec(
        "lena", "Lena", "the librarian; shy, sharp, reads three books at once",
        warmth=0.45, schedule=((9, 18, "library"), (18, 20, "harbor")),
    ),
    NPCSpec(
        "tomas", "Tomas", "an old fisherman with too many stories and not enough listeners",
        warmth=0.65, schedule=((5, 9, "harbor"), (16, 19, "harbor"), (19, 21, "cafe")),
    ),
]

STARTING_MONEY = 80.0
RENT_PER_WEEK = 70.0
