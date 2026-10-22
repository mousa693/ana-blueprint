"""
The Mind: perception, appraisal, choice, memory and sleep (plan §3).

The Mind never touches the world directly.  It receives ``WorldEvent``s, turns
them into feelings, memories and shifts in its relationships, and when it is
time to act it scores the options the world offers and says which one it
wants.  The world decides what actually happens; the Mind appraises that too.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..genesis.seed import SeedSelf
from ..memory.mcmg import MCMG, CoreOrigin
from ..temperament.temperament import Temperament
from ..world.town import ACTIONS_BY_KIND, TICKS_PER_HOUR
from ..world.world import Option, Outcome, WorldEvent
from .choice import Context, Scored, Traits, choose
from .state import DRIVES, Affect, Drives, Goal, Relationship, clamp

SALIENT_KINDS = {"approached", "message", "gift", "circumstance", "loss", "windfall", "work_cut"}
SURPRISING_KINDS = {"approached", "circumstance", "loss", "windfall", "work_cut", "festival"}
UNREMARKABLE = {"sleep", "rest", "go", "coffee", "cook", "buy_food", "message_friend"}

# How hard a suggestion from someone trusted pulls, and how much a need for autonomy resists it.
SUGGESTION_PULL = 0.22
AUTONOMY_RESISTANCE = 0.6

# Progress per serving action.  Life aims (seeded future shards) are long; decisions I make here are shorter.
PROGRESS = {"seed": 0.012, "self": 0.08}

# Aspiration keywords → (drive, what serves it).  Crude on purpose: Phase 3 replaces it.
ASPIRATION_MAP = [
    (r"writ|book|story|poem|journal", "meaning", ["write"]),
    (r"boat|sail|sea|ocean", "competence", ["work", "sailing"]),
    (r"travel|world|explor|road|journey|wander", "novelty", ["explore", "go:road"]),
    (r"friend|love|family|people|communit|belong|partner", "connection", ["talk", "help"]),
    (r"build|make|craft|master|skill|create|business|company", "competence", ["work", "write"]),
    (r"free|own terms|independen", "autonomy", ["write", "explore"]),
    (r"money|secur|stable|home|safe", "security", ["work", "buy_food"]),
    (r"help|teach|serve|give", "meaning", ["help"]),
]


@dataclass
class Decision:
    tick: int
    day: int
    clock: str
    place: str
    chosen: str
    kind: str
    utility: float
    reasons: List[str]
    alternatives: List[tuple[str, float]]
    trigger: str  # "free" | "plan_done" | "interrupt:<kind>" | "response:<who>"
    self_initiated: bool
    phase: str
    mood: float


@dataclass
class Activity:
    option: Option
    scored: Scored
    outcome: Outcome
    remaining: int
    predicted: float
    actual: float


@dataclass
class DayRecord:
    day: int
    moments: List[Dict] = field(default_factory=list)  # {"text", "valence", "intensity"}
    drive_sum: Dict[str, float] = field(default_factory=lambda: {d: 0.0 for d in DRIVES})
    ticks: int = 0
    goals_started: List[str] = field(default_factory=list)
    goals_closed: List[str] = field(default_factory=list)
    letters: List[str] = field(default_factory=list)

    def mean(self, d: str) -> float:
        return self.drive_sum[d] / self.ticks if self.ticks else 0.7


class Mind:
    def __init__(self, seed: SeedSelf, rng: Optional[random.Random] = None) -> None:
        self.seed = seed
        self.name = seed.name
        self.rng = rng or random.Random(1)
        bf = seed.character.big_five
        self.traits = Traits(bf.openness.score, bf.conscientiousness.score, bf.extraversion.score,
                             bf.agreeableness.score, bf.neuroticism.score)
        self.drives = Drives(weight=seed.character.drives.model_dump())
        self.affect = Affect(reactivity=0.7 + 0.6 * self.traits.neuroticism)
        self.temperament = Temperament(omega=0.35 + 0.3 * self.traits.openness, alpha=0.35 + 0.3 * self.traits.conscientiousness)
        self.memory = MCMG()
        friend = seed.old_friend
        self.relationships: Dict[str, Relationship] = {
            "friend": Relationship("friend", friend.name, trust=0.7, affection=0.7, respect=0.5, familiarity=0.9)
        }
        self.goals: List[Goal] = []
        self.decisions: List[Decision] = []
        self.activity: Optional[Activity] = None
        self.expectations: Dict[str, float] = {}
        self.recent_kinds: List[tuple[int, str]] = []
        self.suggestions: Dict[str, tuple[float, int]] = {}  # kind → (pull, expires tick)
        self.sightings: Dict[str, set] = {}
        self.responding_to: Optional[str] = None
        self.pending_interrupt: Optional[str] = None
        self.unanswered: int = 0
        self.influence_log: List[Dict] = []  # suggestions received, for the acceptance metric
        self.gifts: List[int] = []
        self.visited: set[str] = set()
        self.days: Dict[int, DayRecord] = {}
        self.loves = [a.thing.lower() for a in seed.loves]
        self.hates = [a.thing.lower() for a in seed.hates]
        self._goal_seq = 0
        self._seed_goals()

    # ── birth ─────────────────────────────────────────────────────────────

    async def be_born(self) -> None:
        """Lay down the past as identity memories.  They never decay below the floor."""
        for m in self.seed.defining_memories:
            await self.memory.add(
                {"text": m.what_happened, "meaning": m.meaning, "feeling": m.feeling},
                tags={"identity", "past", *(f"symbol:{s.lower()}" for s in m.symbols)},
                origin=CoreOrigin.IDENTITY, confidence=0.6 + 0.4 * m.intensity, entropy=0.1,
            )
        for p in self.seed.people:
            await self.memory.add({"text": f"{p.name}, {p.role}: {p.story}", "feeling": p.feelings},
                                  tags={"identity", "past", "person"}, origin=CoreOrigin.IDENTITY,
                                  confidence=0.8, entropy=0.1)

    def _seed_goals(self) -> None:
        for shard in self.seed.future:
            if shard.kind != "aspiration":
                continue
            drive, serves = "meaning", ["write"]
            for pattern, d, s in ASPIRATION_MAP:
                if re.search(pattern, shard.text, re.IGNORECASE):
                    drive, serves = d, s
                    break
            self._add_goal(shard.text, drive, serves, 0.5 + 0.4 * shard.intensity, "seed",
                           "something I've wanted for a long time", day=0)

    def _add_goal(self, text: str, drive: str, serves: List[str], importance: float, origin: str,
                  reason: str, day: int) -> Goal:
        self._goal_seq += 1
        goal = Goal(f"g{self._goal_seq}", text, drive, serves, importance, origin, reason, day, last_progress_day=day)
        self.goals.append(goal)
        return goal

    # ── perception and appraisal ──────────────────────────────────────────

    def day_record(self, day: int) -> DayRecord:
        return self.days.setdefault(day, DayRecord(day))

    def perceive(self, events: List[WorldEvent], tick: int, place: str, present: Dict[str, str]) -> None:
        """``present``: who is here, id → name."""
        self.visited.add(place)
        for npc, name in present.items():
            self._rel(npc, name)
            self.sightings.setdefault(npc, set()).add((place, (tick // TICKS_PER_HOUR) % 24))
        for e in events:
            self._appraise_event(e, tick)
            if e.kind in SURPRISING_KINDS:
                self.temperament.observe(0.5, self.drives.level["novelty"], tick)
            if e.kind in SALIENT_KINDS or e.kind == "thought":
                self.pending_interrupt = e.kind

    def _rel(self, who: str, name: str) -> Relationship:
        if who not in self.relationships:
            self.relationships[who] = Relationship(who, name)
        return self.relationships[who]

    def _moment(self, e_day: int, text: str, valence: float, intensity: float) -> None:
        self.day_record(e_day).moments.append({"text": text, "valence": valence, "intensity": intensity})

    def _appraise_event(self, e: WorldEvent, tick: int) -> None:
        a, d = self.affect, self.drives
        if e.kind == "weather":
            w = e.data["weather"]
            if any(w in t for t in self.loves):
                a.feel("delight", 0.3, 0.6, 0.4, f"the {w}")
            elif any(w in t for t in self.hates) or w == "storm":
                a.feel("unease", 0.2, -0.3, 0.5, f"the {w}")
        elif e.kind == "rent":
            d.apply({"security": -0.12 if e.data["money"] < 60 else -0.04})
            if e.data["money"] < 40:
                a.feel("worry", 0.5, -0.6, 0.6, "money running low")
                self._moment(e.day, e.text, -0.5, 0.5)
        elif e.kind == "windfall":
            d.apply({"security": 0.08})
            a.feel("pleasant surprise", 0.5, 0.7, 0.6, "unexpected money")
            self._moment(e.day, e.text, 0.6, 0.5)
        elif e.kind == "loss":
            d.apply({"security": -0.1})
            a.feel("distress", 0.5, -0.6, 0.7, "lost money")
            self._moment(e.day, e.text, -0.6, 0.5)
        elif e.kind == "work_cut":
            d.apply({"security": -0.06, "competence": -0.03})
            a.feel("frustration", 0.4, -0.5, 0.6, "no work")
            self._moment(e.day, e.text, -0.4, 0.4)
        elif e.kind == "festival":
            a.feel("lightness", 0.4, 0.6, 0.6, "the festival")
            d.apply({"connection": 0.03, "novelty": 0.05})
            self._moment(e.day, e.text, 0.5, 0.4)
        elif e.kind == "approached":
            npc = e.data["npc"]
            name = e.text.split(" comes")[0]
            self._rel(npc, name).update(0.3, tick, "came over to talk to me")
            a.feel("warmth", 0.35, 0.5, 0.4, f"{name} sought me out")
            self.responding_to = npc
        elif e.kind == "circumstance":
            a.feel("curiosity", 0.4, 0.4, 0.6, e.text)
            self._moment(e.day, e.text, 0.3, 0.4)
        elif e.kind == "message":
            self._from_friend(e, tick)
        elif e.kind == "gift":
            self._gift(e, tick)
        elif e.kind == "thought":
            pull = -0.04 if e.data.get("intrusive") else 0.07
            self._suggest(e.data["suggest"], pull, tick, channel="whisper")
            if e.data.get("intrusive"):
                a.feel("unease", 0.25, -0.3, 0.5, "a thought I can't shake")

    def _from_friend(self, e: WorldEvent, tick: int) -> None:
        rel = self.relationships["friend"]
        self.unanswered += 1
        self.drives.apply({"connection": 0.06 * (0.5 + rel.affection)})
        self.affect.feel("gladness", 0.3 + 0.3 * max(0.0, rel.affection), 0.6, 0.4, f"a message from {rel.name}")
        self.responding_to = "friend"
        rel.update(0.15, tick, f"wrote to me: {e.data['body'][:60]}")
        self._moment(e.day, e.text, 0.4, 0.35)
        kind = e.data.get("suggest")
        if kind:
            recent = sum(1 for r in self.influence_log if r["channel"] == "message" and tick - r["tick"] < 48 * TICKS_PER_HOUR)
            pushed = recent >= 2
            if pushed:
                # Reactance: being steered too often makes the steering push the other way.
                pull = -0.08 * self.drives.weight["autonomy"]
                self.affect.feel("irritation", 0.3, -0.4, 0.5, f"{rel.name} telling me what to do again")
                rel.update(-0.1, tick, "keeps telling me what to do")
            else:
                pull = SUGGESTION_PULL * (rel.trust + rel.affection) / 2 * (1 - AUTONOMY_RESISTANCE * self.drives.weight["autonomy"])
            self._suggest(kind, pull, tick, channel="message")

    def _gift(self, e: WorldEvent, tick: int) -> None:
        rel = self.relationships["friend"]
        self.gifts.append(tick)
        recent = sum(1 for t in self.gifts if tick - t < 7 * 24 * TICKS_PER_HOUR)
        self.drives.apply({"security": 0.05})
        if recent > 1 and self.drives.weight["autonomy"] > 0.5:
            self.affect.feel("discomfort", 0.35, -0.3, 0.4, "being looked after when I didn't ask")
            self.drives.apply({"autonomy": -0.05})
            rel.update(-0.05, tick, "sent money again; I can manage")
            self._moment(e.day, e.text + " I didn't ask for it.", -0.2, 0.4)
        else:
            self.affect.feel("gratitude", 0.45, 0.6, 0.3, f"{rel.name}'s gift")
            rel.update(0.25, tick, "sent help when I needed it")
            self._moment(e.day, e.text, 0.5, 0.4)

    def _suggest(self, kind: str, pull: float, tick: int, channel: str) -> None:
        self.suggestions[kind] = (pull, tick + 24 * TICKS_PER_HOUR)
        self.influence_log.append({"tick": tick, "kind": kind, "channel": channel, "pull": pull, "taken": False})

    # ── choosing ──────────────────────────────────────────────────────────

    def wants_to_deliberate(self) -> Optional[str]:
        """The cheap path: keep doing what I'm doing unless something matters."""
        if self.activity is None:
            return "plan_done" if self.decisions else "free"
        if self.pending_interrupt:
            return f"interrupt:{self.pending_interrupt}"
        strongest = self.affect.strongest()
        if strongest and strongest.intensity > 0.6:
            return f"interrupt:{strongest.name}"
        if self.drives.level["energy"] < 0.05 and self.activity.option.spec.kind != "sleep":
            return "interrupt:exhaustion"
        return None

    def context(self, tick: int, hour: int, money: float) -> Context:
        horizon = tick - 48 * TICKS_PER_HOUR
        habits: Dict[str, int] = {}
        for t, k in self.recent_kinds:
            if t >= horizon:
                habits[k] = habits.get(k, 0) + 1
        # A suggestion is loudest when it arrives and fades over the day.
        window = 24 * TICKS_PER_HOUR
        suggestions = {k: p * (until - tick) / window for k, (p, until) in self.suggestions.items() if tick < until}
        return Context(
            drives=self.drives, traits=self.traits, temperament=self.temperament,
            relationships=self.relationships, goals=self.goals, pleasure=self.affect.pleasure,
            hour=hour, money=money, habits=habits, expectations=self.expectations,
            suggestions=suggestions, responding_to=self.responding_to, sightings=self.sightings, rng=self.rng,
        )

    def decide(self, options: List[Option], tick: int, clock: str, hour: int, place: str, money: float,
               trigger: str) -> tuple[Scored, Decision]:
        ctx = self.context(tick, hour, money)
        continuing = None
        if self.activity is not None:
            act = self.activity
            remaining = {k: v * act.remaining / act.option.spec.ticks for k, v in act.outcome.effects.items()}
            parts = {"needs": self.drives.relief(remaining), "momentum": 0.06}
            parts["goals"] = sum(0.22 * g.importance for g in self.goals
                                 if g.status == "active" and g.advances(act.scored.key, act.scored.kind))
            continuing = Scored(f"continue:{act.scored.key}", act.scored.kind, sum(parts.values()), parts, None)
        chosen, ranked = choose(options, ctx, continuing)
        responded = self._is_response(chosen, trigger)
        decision = Decision(
            tick=tick, day=tick // (24 * TICKS_PER_HOUR) + 1, clock=clock, place=place, chosen=chosen.key, kind=chosen.kind,
            utility=round(chosen.utility, 3), reasons=chosen.reasons(),
            alternatives=[(s.key, round(s.utility, 3)) for s in ranked[1:4]],
            trigger=trigger, self_initiated=not responded,
            phase=self.temperament.phase(tick), mood=round(self.affect.pleasure, 2),
        )
        self.decisions.append(decision)
        self.pending_interrupt = None
        if chosen.option is not None and self.responding_to and (
            chosen.option.target == self.responding_to or (self.responding_to == "friend" and chosen.kind == "message_friend")
        ):
            self.responding_to = None
        for record in self.influence_log:
            if not record["taken"] and record["kind"] == chosen.kind and tick - record["tick"] < 24 * TICKS_PER_HOUR:
                record["taken"] = True
        return chosen, decision

    def _is_response(self, chosen: Scored, trigger: str) -> bool:
        """A choice that answers something outside it: someone approached, a message, a suggestion followed."""
        if chosen.option is None:
            return False
        if self.responding_to and (chosen.option.target == self.responding_to or
                                   (self.responding_to == "friend" and chosen.kind == "message_friend")):
            return True
        return chosen.parts.get("influence", 0.0) > 0 and chosen.parts["influence"] >= 0.5 * chosen.utility

    def begin(self, scored: Scored, outcome: Outcome, tick: int, day: int) -> None:
        if scored.option is None:
            return  # continuing what I was doing
        self.recent_kinds.append((tick, scored.kind))
        del self.recent_kinds[:-200]
        if not outcome.ok:
            self.activity = None
            self.temperament.observe(0.6, self.drives.level["novelty"], tick)
            self.affect.feel("frustration", 0.35, -0.5, 0.6, outcome.text)
            self._learn(scored, -0.15)
            self._moment(day, f"Tried, but {outcome.text}.", -0.3, 0.3)
            return
        predicted = self.drives.relief(scored.option.spec.effects)
        actual = self.drives.relief(outcome.effects)
        self.activity = Activity(scored.option, scored, outcome, scored.option.spec.ticks, predicted, actual)

    def step(self, tick: int, day: int, money: float, asleep: bool) -> Optional[Activity]:
        """One tick of living: effects of what I'm doing, needs, feelings.  Returns a finished activity."""
        rec = self.day_record(day)
        rec.ticks += 1
        for d in DRIVES:
            rec.drive_sum[d] += self.drives.level[d]
        self.drives.decay(1 / TICKS_PER_HOUR, asleep=asleep)
        if money < 30:
            self.drives.apply({"security": -0.01})
        self.affect.step()
        act = self.activity
        if act is None:
            return None
        n = act.option.spec.ticks
        self.drives.apply({k: v / n for k, v in act.outcome.effects.items()})
        if act.option.spec.kind not in ("work", "sleep") and self.decisions and self.decisions[-1].self_initiated:
            self.drives.apply({"autonomy": 0.006})  # time spent on my own choices is autonomy
        act.remaining -= 1
        if act.remaining > 0:
            return None
        self.activity = None
        self._finish(act, tick, day)
        return act

    def _finish(self, act: Activity, tick: int, day: int) -> None:
        spec, outcome, scored = act.option.spec, act.outcome, act.scored
        actual = act.actual
        error = abs(actual - act.predicted)
        self._learn(scored, actual - act.predicted)

        valence = clamp(2.5 * actual, -1, 1)
        intensity = clamp(abs(valence) * 0.8 + (0.25 if outcome.social == "warm" else 0.0))
        target = act.option.target
        if target:
            rel = self.relationships[target]
            social_v = {"warm": 0.6, "brief": 0.1, "cold": -0.5}[outcome.social or "brief"]
            error += 0.6 * abs(social_v - (0.1 + 0.5 * rel.affection))  # people surprise us most
            rel.update(social_v, tick, outcome.text)
            if outcome.social == "warm":
                self.affect.feel("joy", 0.4, 0.7, 0.5, outcome.text)
            elif outcome.social == "cold":
                name = "hurt" if rel.affection > 0.1 else "irritation"
                self.affect.feel(name, 0.4, -0.5, 0.5, outcome.text)
                valence, intensity = -0.4, 0.45
        elif valence > 0.1:
            self.affect.feel("satisfaction", 0.25 + 0.3 * valence, 0.5, 0.3, outcome.text)
        if "hurt an ankle" in outcome.text:
            self.affect.feel("pain", 0.5, -0.6, 0.7, outcome.text)
            valence, intensity = -0.5, 0.6
        if spec.kind == "message_friend":
            self.unanswered = 0
            self.relationships["friend"].update(0.1, tick, "I wrote back")
        self.temperament.observe(min(1.0, 3 * error), self.drives.level["novelty"], tick)
        notable = spec.kind not in UNREMARKABLE and intensity > 0.2
        if notable or "found" in outcome.text or (spec.kind not in UNREMARKABLE and self.rng.random() < 0.15):
            self._moment(day, outcome.text[0].upper() + outcome.text[1:] + ".", valence, max(intensity, 0.3))
        self._progress_goals(scored, day)

    def _learn(self, scored: Scored, error: float) -> None:
        """Experience corrects expectation, per specific option and per kind."""
        for key in (scored.key, scored.kind):
            self.expectations[key] = 0.75 * self.expectations.get(key, 0.0) + 0.25 * clamp(error, -0.5, 0.5)

    def _progress_goals(self, scored: Scored, day: int) -> None:
        for g in self.goals:
            if g.status != "active" or not g.advances(scored.key, scored.kind):
                continue
            step = PROGRESS[g.origin] * (1.5 if scored.kind in ("talk", "help", "explore") else 1.0)
            g.progress = min(1.0, g.progress + step)
            g.last_progress_day = day
            if g.progress >= 1.0:
                g.status = "done"
                g.closed_reason = "did it"
                self.affect.feel("pride", 0.6, 0.8, 0.5, g.text)
                self.drives.apply({"meaning": 0.12, g.drive: 0.1})
                self._moment(day, f"I did it: {g.text.rstrip('.')}.", 0.8, 0.8)
                self.day_record(day).goals_closed.append(f"done: {g.text}")

    # ── sleep ─────────────────────────────────────────────────────────────

    async def sleep(self, day: int) -> Dict:
        """Consolidate the day: remember what mattered, let the rest fade, rethink goals."""
        rec = self.day_record(day)
        moments = sorted(rec.moments, key=lambda m: -m["intensity"])
        for m in moments[:8]:
            await self.memory.add({"text": m["text"], "valence": m["valence"], "day": day},
                                  tags={f"day:{day}", "episodic"}, origin=CoreOrigin.OMEGA,
                                  confidence=clamp(0.35 + 0.5 * m["intensity"]), entropy=0.3)
        if moments:
            summary = " ".join(m["text"] for m in moments[:3])
            await self.memory.add({"text": f"Day {day}: {summary}", "day": day},
                                  tags={f"day:{day}", "summary"}, origin=CoreOrigin.ALPHA,
                                  confidence=0.6, entropy=0.2)
        await self.memory.decay(elapsed=24.0)
        self._rethink_goals(day)
        return {"day": day, "moments": moments}

    def _rethink_goals(self, day: int) -> None:
        rec = self.day_record(day)
        prev = self.days.get(day - 1)
        active_drives = {g.drive for g in self.goals if g.status == "active" and g.origin == "self"}
        active_texts = {g.text for g in self.goals if g.status == "active"}
        for d in ("connection", "novelty", "competence", "meaning", "security", "autonomy"):
            starving = rec.mean(d) < 0.42 and (prev is None or prev.mean(d) < 0.5)
            if not starving or d in active_drives:
                continue
            goal = self._goal_for(d)
            if goal and goal[0] not in active_texts:
                self._add_goal(*goal, importance=0.45 + 0.4 * self.drives.weight[d], origin="self",
                               reason=f"I've felt the lack of {d} for days", day=day)
                rec.goals_started.append(goal[0])
        for g in self.goals:
            if g.status != "active":
                continue
            stale = day - g.last_progress_day
            if g.origin == "self" and rec.mean(g.drive) > 0.7 and g.progress < 0.5:
                g.status, g.closed_reason = "abandoned", "don't need it as much any more"
            elif stale >= (10 if g.origin == "seed" else 4) and g.importance < 0.75:
                g.status, g.closed_reason = "abandoned", f"nothing moved in {stale} days"
            if g.status == "abandoned":
                rec.goals_closed.append(f"let go: {g.text} ({g.closed_reason})")

    def _goal_for(self, drive: str) -> Optional[tuple[str, str, List[str]]]:
        if drive == "connection":
            met = [r for w, r in self.relationships.items() if w != "friend" and r.familiarity > 0.05 and r.affection > -0.1]
            if met:
                best = max(met, key=lambda r: r.affection + r.familiarity)
                return (f"Get to know {best.name} properly", drive, [f"talk:{best.who}", f"help:{best.who}"])
            return ("Find someone here I can really talk to", drive, ["talk", "help"])
        if drive == "novelty":
            if "road" not in self.visited:
                return ("Find out where the coast road goes", drive, ["explore", "go:road"])
            return ("Find something in this town I haven't seen yet", drive, ["read", "climb", "explore", "sailing"])
        if drive == "competence":
            return ("Get properly good at the work in Ivo's yard", drive, ["work", "sailing"])
        if drive == "meaning":
            return ("Write something that's actually mine", drive, ["write"])
        if drive == "security":
            return ("Put some money aside so I can breathe", drive, ["work", "buy_food"])
        if drive == "autonomy":
            return ("Keep some time that's only mine", drive, ["write", "walk", "explore"])
        return None

    # ── views for reports and the narrator ─────────────────────────────────

    def mood_word(self) -> str:
        p, ar = self.affect.pleasure, self.affect.arousal
        if p > 0.35:
            return "bright" if ar > 0.45 else "content"
        if p > 0.05:
            return "alright" if ar > 0.4 else "calm"
        if p > -0.25:
            return "restless" if ar > 0.5 else "flat"
        return "low" if ar < 0.5 else "on edge"

    def action_label(self, kind: str) -> str:
        spec = ACTIONS_BY_KIND.get(kind)
        return spec.label if spec else kind
