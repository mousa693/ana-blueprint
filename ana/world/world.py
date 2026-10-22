"""
The Habitat's rules: time, weather, fate, other people, and the Harmonizer.

The mind proposes; the world disposes.  Everything that happens is appended to
an event log that is never rewritten (plan §4.3).  The twin learns about the
world only through ``perceive()``; nothing here reaches into the mind.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, TextIO

from .town import (
    ACTIONS,
    DISTANCES,
    NPCS,
    PLACES,
    RENT_PER_WEEK,
    STARTING_MONEY,
    TICKS_PER_HOUR,
    ActionSpec,
    NPCSpec,
)

WEATHER = ("clear", "rain", "fog", "storm")
WEATHER_NEXT = {
    "clear": (0.6, 0.2, 0.15, 0.05),
    "rain": (0.35, 0.35, 0.15, 0.15),
    "fog": (0.45, 0.2, 0.3, 0.05),
    "storm": (0.3, 0.45, 0.1, 0.15),
}


def in_hours(hour: int, span: Optional[tuple[int, int]]) -> bool:
    """True if ``hour`` is inside [start, end); spans may wrap midnight."""
    if span is None:
        return True
    start, end = span
    return start <= hour < end if start < end else hour >= start or hour < end


@dataclass
class WorldEvent:
    tick: int
    day: int
    hour: int
    kind: str
    text: str
    source: str  # "world", "fate", "npc:<id>", "friend", "twin"
    place: Optional[str] = None  # None = everyone knows (weather, rent)
    data: Dict = field(default_factory=dict)


@dataclass
class Option:
    """Something the twin could try right now."""

    spec: ActionSpec
    target: Optional[str] = None  # npc id for social actions
    move_to: Optional[str] = None  # place id for travel

    @property
    def key(self) -> str:
        if self.move_to:
            return f"go:{self.move_to}"
        return f"{self.spec.kind}:{self.target}" if self.target else self.spec.kind


@dataclass
class Outcome:
    ok: bool
    text: str
    effects: Dict[str, float] = field(default_factory=dict)
    social: Optional[str] = None  # "warm" | "brief" | "cold" for social actions
    events: List[WorldEvent] = field(default_factory=list)


@dataclass
class NPCState:
    spec: NPCSpec
    affection: float = 0.0  # how they feel about the twin, -1..1
    mood: float = 0.0  # today, -1..1
    talks: int = 0


@dataclass
class Body:
    """The twin's physical situation.  The world owns this, not the mind."""

    place: str = "home"
    money: float = STARTING_MONEY


def travel(here: str, there: str) -> ActionSpec:
    ticks = DISTANCES[here][there]
    return ActionSpec("go", f"walk to {PLACES[there].name}", None, ticks=ticks,
                      effects={"energy": -0.01 * ticks}, done=f"walked to {PLACES[there].name}")

CIRCUMSTANCES: Dict[str, Dict] = {
    "notice_sailing": {
        "text": "A notice on the harbor wall: sailing lessons, mornings this week, ask Tomas.",
        "place": "harbor",
        "action": ActionSpec(
            "sailing", "take a sailing lesson with Tomas", ("harbor",), ticks=3,
            effects={"competence": 0.12, "novelty": 0.15, "connection": 0.08, "energy": -0.10},
            hours=(9, 12), cost=10, risk=0.05,
        ),
        "days": 6,
    },
    "stranger": {
        "text": "A traveler with a heavy pack has taken the corner table at Mara's. She says she walked the coast road from the north.",
        "place": "cafe",
        "npc": NPCSpec("traveler", "the traveler", "walked here along the coast road; knows where it goes",
                       warmth=0.8, schedule=((8, 20, "cafe"),)),
        "days": 2,
    },
    "found_book": {
        "text": "Someone left a worn book on the library's return cart: 'Lights of the North Coast'.",
        "place": "library",
        "bonus": ("read", {"meaning": 0.08, "novelty": 0.05}),
        "days": 4,
    },
}


class World:
    def __init__(self, seed: int = 7, log: Optional[TextIO] = None) -> None:
        self.rng = random.Random(seed)
        self.tick = 0
        self.weather = "clear"
        self.body = Body()
        self.npcs: Dict[str, NPCState] = {n.id: NPCState(n) for n in NPCS}
        self.log: List[WorldEvent] = []
        self._log_file = log
        self._cursor = 0  # first event the twin hasn't perceived yet
        self.inbox: List[WorldEvent] = []
        self.work_blocked_until = -1
        self.active: Dict[str, Dict] = {}  # circumstance id → {"until": day, ...}
        self.discoveries: List[str] = []

    # ── time ───────────────────────────────────────────────────────────────

    @property
    def day(self) -> int:
        return self.tick // (24 * TICKS_PER_HOUR) + 1

    @property
    def hour(self) -> int:
        return (self.tick // TICKS_PER_HOUR) % 24

    @property
    def clock(self) -> str:
        minutes = (self.tick % TICKS_PER_HOUR) * (60 // TICKS_PER_HOUR)
        return f"day {self.day}, {self.hour:02d}:{minutes:02d}"

    def advance(self) -> None:
        """Move time forward one tick and let the world do its own things."""
        self.tick += 1
        if self.tick % (24 * TICKS_PER_HOUR) == 6 * TICKS_PER_HOUR:
            self._morning()
        self._npc_initiative()

    # ── events ─────────────────────────────────────────────────────────────

    def emit(self, kind: str, text: str, source: str, place: Optional[str] = None, **data) -> WorldEvent:
        event = WorldEvent(self.tick, self.day, self.hour, kind, text, source, place, data)
        self.log.append(event)
        if self._log_file:
            self._log_file.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")
        return event

    def perceive(self) -> List[WorldEvent]:
        """Events since last time that the twin could know about: public, or where it is."""
        new = self.log[self._cursor :]
        self._cursor = len(self.log)
        here = self.body.place
        return [e for e in new if e.source != "twin" and (e.place is None or e.place == here)]

    # ── people ─────────────────────────────────────────────────────────────

    def npc_place(self, npc: NPCSpec) -> Optional[str]:
        for start, end, place in npc.schedule:
            if in_hours(self.hour, (start, end)):
                return place
        return None

    def present(self, place: Optional[str] = None) -> List[NPCState]:
        place = place or self.body.place
        return [s for s in self.npcs.values() if self.npc_place(s.spec) == place]

    def _npc_initiative(self) -> None:
        """Sometimes people who like the twin come to it."""
        for state in self.present():
            if state.affection > 0.25 and self.rng.random() < 0.04 * (1 + state.affection):
                self.emit(
                    "approached", f"{state.spec.name} comes over to talk.", f"npc:{state.spec.id}",
                    self.body.place, npc=state.spec.id,
                )

    # ── fate ───────────────────────────────────────────────────────────────

    def _morning(self) -> None:
        self.weather = self.rng.choices(WEATHER, WEATHER_NEXT[self.weather])[0]
        self.emit("weather", f"The day starts {self._weather_phrase()}.", "world", weather=self.weather)
        for state in self.npcs.values():
            state.mood = max(-1.0, min(1.0, self.rng.gauss(0.1, 0.35)))
        for cid in [c for c, info in self.active.items() if info["until"] < self.day]:
            del self.active[cid]
            if "npc" in CIRCUMSTANCES[cid]:
                self.npcs.pop(CIRCUMSTANCES[cid]["npc"].id, None)
        if self.day % 7 == 0:
            self.body.money -= RENT_PER_WEEK
            self.emit("rent", f"Rent is due: {RENT_PER_WEEK:.0f}. {self.body.money:.0f} left.", "world",
                      money=self.body.money)
        roll = self.rng.random()
        if roll < 0.03:
            self.body.money += 25
            self.emit("windfall", "An envelope under the door: the bakery overcharged last month and sent back 25.", "fate")
        elif roll < 0.06:
            self.body.money -= 15
            self.emit("loss", "Fifteen gone from my coat pocket. Dropped somewhere, or taken.", "fate")
        elif roll < 0.10:
            self.work_blocked_until = self.day + 1
            self.emit("work_cut", "Ivo pins a note on the boatyard gate: no shifts for two days, waiting on timber.", "fate")
        elif roll < 0.15:
            for state in self.npcs.values():
                state.mood = min(1.0, state.mood + 0.4)
            self.emit("festival", "It's the herring festival. Bunting across the square, everyone out.", "fate")

    def _weather_phrase(self) -> str:
        return {"clear": "clear and bright", "rain": "with rain", "fog": "in thick fog", "storm": "with a storm coming in off the sea"}[self.weather]

    # ── circumstances (the creator's subtle channel, via the gateway) ───────

    def add_circumstance(self, cid: str) -> WorldEvent:
        info = CIRCUMSTANCES[cid]
        self.active[cid] = {"until": self.day + info["days"]}
        if "npc" in info:
            self.npcs[info["npc"].id] = NPCState(info["npc"], affection=0.1)
        # Unattributed: to the twin this is just something that happened.
        return self.emit("circumstance", info["text"], "world", info["place"], circumstance=cid)

    def _circumstance_actions(self) -> List[ActionSpec]:
        return [CIRCUMSTANCES[c]["action"] for c in self.active if "action" in CIRCUMSTANCES[c]]

    # ── the Harmonizer ─────────────────────────────────────────────────────

    def options(self) -> List[Option]:
        """What can plausibly be tried here and now (place, hours, people present)."""
        here = self.body.place
        open_now = in_hours(self.hour, PLACES[here].hours)
        out: List[Option] = []
        for spec in [*ACTIONS, *self._circumstance_actions()]:
            if spec.places is not None and (here not in spec.places or not open_now):
                continue
            if not in_hours(self.hour, spec.hours):
                continue
            if spec.social:
                out += [Option(spec, target=s.spec.id) for s in self.present()]
            else:
                out.append(Option(spec))
        # A journey is one decision: walk to wherever is worth going.  Closed doors don't block the way.
        out += [Option(travel(here, p), move_to=p) for p in PLACES if p != here]
        return out

    def attempt(self, option: Option, energy: float) -> Outcome:
        """Validate and resolve an action.  The world, not the mind, decides what happens."""
        spec = option.spec
        if option.move_to:
            self.body.place = option.move_to
            return Outcome(True, spec.done, dict(spec.effects))
        if spec.cost > self.body.money:
            return Outcome(False, f"couldn't afford to {spec.label} ({spec.cost:.0f} needed, {self.body.money:.0f} left)")
        if energy < spec.min_energy:
            return Outcome(False, f"too exhausted to {spec.label}")
        if spec.kind == "work" and self.day <= self.work_blocked_until:
            return Outcome(False, "the boatyard gate is chained; no shifts today")
        if spec.kind == "climb" and self.weather == "storm" and self.rng.random() < 0.5:
            return Outcome(False, "the wind on the lighthouse stairs was too strong; turned back halfway")

        self.body.money -= spec.cost
        effects = dict(spec.effects)
        for cid in self.active:
            bonus = CIRCUMSTANCES[cid].get("bonus")
            if bonus and bonus[0] == spec.kind:
                for k, v in bonus[1].items():
                    effects[k] = effects.get(k, 0.0) + v
        if PLACES[self.body.place].outdoor and self.weather in ("rain", "storm"):
            effects["energy"] = effects.get("energy", 0.0) - 0.04

        if spec.social:
            return self._social(option, effects)
        risk = spec.risk * (3 if self.weather == "storm" else 1)
        if spec.risk and self.rng.random() < risk:
            effects["energy"] = effects.get("energy", 0.0) - 0.15
            for k in ("novelty", "meaning"):
                if k in effects:
                    effects[k] *= 0.4
            return Outcome(True, f"tried to {spec.label}, but slipped on wet rock and hurt an ankle", effects)
        if spec.kind == "work":
            self.body.money += spec.pay
            self.npcs["ivo"].affection = min(1.0, self.npcs["ivo"].affection + 0.03)
            return Outcome(True, f"worked a full shift; earned {spec.pay:.0f}", effects)
        if spec.kind == "explore":
            return self._explore(effects)
        return Outcome(True, spec.done or spec.label, effects)

    def _social(self, option: Option, effects: Dict[str, float]) -> Outcome:
        state = self.npcs[option.target]
        npc = state.spec
        busy = any(in_hours(self.hour, span) for span in npc.busy)
        p_warm = 0.15 + 0.45 * npc.warmth + 0.4 * state.affection + 0.2 * state.mood - (0.3 if busy else 0.0)
        p_warm = max(0.05, min(0.95, p_warm))
        roll = self.rng.random()
        state.talks += 1
        if option.spec.kind == "help":
            if roll < p_warm + 0.1:
                state.affection = min(1.0, state.affection + 0.08)
                return Outcome(True, f"helped {npc.name}; they were glad of it", effects, social="warm")
            state.affection = max(-1.0, state.affection - 0.02)
            return Outcome(True, f"offered to help {npc.name}, who waved me off", {"energy": -0.01}, social="cold")
        if roll < p_warm:
            state.affection = min(1.0, state.affection + 0.06)
            return Outcome(True, f"talked with {npc.name}, and it went somewhere", effects, social="warm")
        if roll < p_warm + (1 - p_warm) * 0.6:
            state.affection = min(1.0, state.affection + 0.01)
            small = {k: v * 0.35 for k, v in effects.items()}
            return Outcome(True, f"exchanged a few words with {npc.name}", small, social="brief")
        state.affection = max(-1.0, state.affection - 0.04)
        return Outcome(True, f"tried to talk with {npc.name}, who barely looked up", {"connection": -0.03}, social="cold")

    def _explore(self, effects: Dict[str, float]) -> Outcome:
        finds = [
            "a cove with a wrecked boat half-buried in the sand",
            "a chapel with no roof and fresh flowers inside",
            "a bench on the cliff with a name carved into it",
            "a farm selling honey from a box with an honesty jar",
            "the place where the road turns inland and the sea disappears",
        ]
        unseen = [f for f in finds if f not in self.discoveries]
        if unseen and self.rng.random() < 0.45:
            found = self.rng.choice(unseen)
            self.discoveries.append(found)
            effects["novelty"] = effects.get("novelty", 0.0) + 0.1
            effects["meaning"] = effects.get("meaning", 0.0) + 0.05
            return Outcome(True, f"followed the coast road and found {found}", effects)
        return Outcome(True, "followed the coast road a long way; wind, gorse, nothing new", {k: v * 0.6 for k, v in effects.items()})
