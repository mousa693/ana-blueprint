"""
The Life Loop (plan §3.11): the world moves, the twin perceives, and when it
matters, it chooses.

Each 30-minute tick:
    world.advance()          time, weather, fate, other people
    mind.perceive(events)    appraisal → feelings, relationships, memories
    mind.wants_to_deliberate()  the cheap path: usually "keep going"
    mind.decide(options)     scored from state; logged with reasons
    world.attempt(option)    the Harmonizer decides what actually happens
    mind.step()              needs, feelings, progress
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Dict, List, Optional, TextIO

from ..gateway import Gateway
from ..genesis.seed import SeedSelf
from ..mind.choice import Scored
from ..mind.mind import Activity, Mind
from ..world.town import ACTIONS_BY_KIND, TICKS_PER_HOUR
from ..world.world import Option, Outcome, World
from .narrator import Narrator, TemplateNarrator, day_facts


class Life:
    def __init__(
        self,
        seed: SeedSelf,
        world_seed: int = 7,
        out_dir: Optional[Path] = None,
        narrator: Optional[Narrator] = None,
    ) -> None:
        self.out_dir = Path(out_dir) if out_dir else None
        self._files: List[TextIO] = []
        world_log = self._open("world_events.jsonl")
        self._decision_log = self._open("decisions.jsonl")
        self.world = World(seed=world_seed, log=world_log)
        self.mind = Mind(seed, rng=random.Random(world_seed * 31 + 1))
        self.gateway = Gateway(self.world, seed.old_friend.name)
        self.narrator: Narrator = narrator or TemplateNarrator()
        self.journal: Dict[int, str] = {}
        self.letters: List[Dict] = []
        self.awake_day = 1
        self.ticks = 0
        self.deliberations = 0
        self.collapses = 0
        self.timeline: Dict[tuple[int, int], str] = {}  # (day, hour) → what it was doing
        self._born = False

    def _open(self, name: str) -> Optional[TextIO]:
        if not self.out_dir:
            return None
        self.out_dir.mkdir(parents=True, exist_ok=True)
        f = open(self.out_dir / name, "w", encoding="utf-8")
        self._files.append(f)
        return f

    def close(self) -> None:
        for f in self._files:
            f.close()

    async def run(self, days: int, before_tick: Optional[Callable[["Life"], None]] = None) -> None:
        """Live ``days`` more days.  ``before_tick`` lets a caller act on the world (e.g. scripted influence)."""
        if not self._born:
            await self.mind.be_born()
            self._born = True
        end = self.world.tick + days * 24 * TICKS_PER_HOUR
        while self.world.tick < end:
            if before_tick:
                before_tick(self)
            await self.tick()

    async def tick(self) -> None:
        w, m = self.world, self.mind
        w.advance()
        self.ticks += 1
        present = {s.spec.id: s.spec.name for s in w.present()}
        m.perceive(w.perceive(), w.tick, w.body.place, present)

        trigger = m.wants_to_deliberate()
        if trigger:
            self.deliberations += 1
            scored, decision = m.decide(w.options(), w.tick, w.clock, w.hour, w.body.place, w.body.money, trigger)
            if self._decision_log:
                self._decision_log.write(json.dumps(asdict(decision), ensure_ascii=False) + "\n")
            if scored.option is not None:
                await self._do(scored)

        if m.activity is None and m.drives.level["energy"] < 0.02:
            await self._collapse()

        asleep = m.activity is not None and m.activity.option.spec.kind == "sleep"
        if w.tick % TICKS_PER_HOUR == 0:
            self.timeline[(w.day, w.hour)] = m.activity.option.spec.kind if m.activity else "idle"
        finished = m.step(w.tick, w.day, w.body.money, asleep=asleep)
        if finished and finished.option.spec.kind == "sleep":
            self.awake_day = w.day

    async def _do(self, scored: Scored) -> None:
        w, m = self.world, self.mind
        m.activity = None  # whatever I was doing, I've stopped
        outcome = w.attempt(scored.option, m.drives.level["energy"])
        w.emit("action", outcome.text, "twin", w.body.place, key=scored.key, ok=outcome.ok, social=outcome.social)
        m.begin(scored, outcome, w.tick, w.day)
        if not outcome.ok:
            return
        if scored.kind == "sleep":
            await self._sleep()
        elif scored.kind == "message_friend":
            text = self.narrator.letter(m, w.day, day_facts(m, w.day, w.weather, w.body.money))
            self.letters.append({"clock": w.clock, "text": text})
            w.emit("message_out", text, "twin", None, to="friend")

    async def _collapse(self) -> None:
        """Too long without sleep: the body decides."""
        w, m = self.world, self.mind
        self.collapses += 1
        spec = ACTIONS_BY_KIND["sleep"]
        outcome = Outcome(True, f"fell asleep where I was, at {w.body.place}", {"energy": 0.6, "security": -0.04})
        w.emit("collapsed", outcome.text, "twin", w.body.place)
        option = Option(spec)
        scored = Scored("sleep", "sleep", 0.0, {"exhaustion": 1.0}, option)
        m.activity = Activity(option, scored, outcome, spec.ticks, 0.0, m.drives.relief(outcome.effects))
        m._moment(w.day, "Fell asleep where I was. My body made the decision.", -0.4, 0.5)
        await self._sleep()

    async def _sleep(self) -> None:
        w, m = self.world, self.mind
        day = self.awake_day
        if day in self.journal or 10 <= w.hour < 20:
            return  # a daytime collapse or a nap; the day isn't over
        await m.sleep(day)
        self.journal[day] = self.narrator.journal(m, day, day_facts(m, day, w.weather, w.body.money))
