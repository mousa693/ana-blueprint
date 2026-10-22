"""The properties plan §1 says make agency real, each checked directly."""

import ast
import asyncio
import copy
import random
from pathlib import Path

from ana.genesis.seed import SeedSelf
from ana.life.__main__ import run_life
from ana.life.demo import DEMO_SEED, demo_seed
from ana.mind.choice import choose
from ana.mind.mind import Mind
from ana.world.town import ACTIONS_BY_KIND
from ana.world.world import Option


def test_no_write_path_into_the_mind():
    """The gateway is all the creator has, and it cannot reach the mind."""
    tree = ast.parse(Path("ana/gateway.py").read_text())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and "mind" in m for m in imported)


def options_at_cafe_evening():
    return [
        Option(ACTIONS_BY_KIND["talk"], target="mara"),
        Option(ACTIONS_BY_KIND["coffee"]),
        Option(ACTIONS_BY_KIND["write"]),
    ]


def test_choice_follows_state_not_script():
    """Same situation, different inner state, different choice."""
    lonely, worn = Mind(demo_seed(), random.Random(0)), Mind(demo_seed(), random.Random(0))
    for m in (lonely, worn):
        m.perceive([], 0, "cafe", {"mara": "Mara"})
        m.goals = []  # needs alone; goals are tested below
    lonely.drives.level.update(connection=0.05, meaning=0.9)
    worn.drives.level.update(connection=0.95, meaning=0.1)
    pick = lambda m: choose(options_at_cafe_evening(), m.context(40, 19, 100))[0].kind
    assert pick(lonely) == "talk"
    assert pick(worn) == "write"


def test_a_goal_it_holds_tips_the_balance():
    with_goal, without = Mind(demo_seed(), random.Random(0)), Mind(demo_seed(), random.Random(0))
    for m in (with_goal, without):
        m.perceive([], 0, "cafe", {"mara": "Mara"})
        m.drives.level.update(connection=0.95, meaning=0.1)
        m.goals = []
    with_goal._add_goal("Get to know Mara properly", "connection", ["talk:mara"], 0.9, "self", "r", 1)
    pick = lambda m: choose(options_at_cafe_evening(), m.context(40, 19, 100))[0].kind
    assert pick(without) == "write"
    assert pick(with_goal) == "talk"


def test_history_changes_how_it_sees_people():
    warm, burned = Mind(demo_seed(), random.Random(0)), Mind(demo_seed(), random.Random(0))
    for m in (warm, burned):
        m.perceive([], 0, "cafe", {"mara": "Mara"})
        m.drives.level["connection"] = 0.4
    for t in range(6):
        warm.relationships["mara"].update(0.6, t, "kind")
        burned.relationships["mara"].update(-0.5, t, "cold")
    score = lambda m: next(s.utility for s in choose(options_at_cafe_evening(), m.context(40, 19, 100))[1] if s.kind == "talk")
    assert score(warm) > score(burned) + 0.1
    assert burned.relationships["mara"].stance in ("wary of", "resents")


def test_betrayal_hits_harder_than_kindness_heals():
    m = Mind(demo_seed())
    r = m._rel("x", "X")
    r.update(0.5, 0, "kind")
    up = r.affection
    r.update(-0.5, 1, "unkind")
    assert r.affection < 0 < up


def test_it_makes_its_own_goals_and_lets_them_go():
    m = Mind(demo_seed())
    for day in (1, 2):
        rec = m.day_record(day)
        rec.ticks = 1
        rec.drive_sum = {d: 0.8 for d in rec.drive_sum} | {"novelty": 0.2}
    m._rethink_goals(2)
    mine = [g for g in m.goals if g.origin == "self"]
    assert [g.text for g in mine] == ["Find out where the coast road goes"]
    m._rethink_goals(2)
    assert len([g for g in m.goals if g.origin == "self"]) == 1  # no duplicates
    rec = m.day_record(3)
    rec.ticks = 1
    rec.drive_sum = {d: 0.8 for d in rec.drive_sum}
    m._rethink_goals(3)
    assert mine[0].status == "abandoned" and mine[0].closed_reason


def test_pushing_too_hard_backfires():
    """Three steering messages in two days: the third pushes it away."""
    from ana.world.world import WorldEvent

    m = Mind(demo_seed())
    for i, tick in enumerate((10, 20, 30)):
        e = WorldEvent(tick, 1, 5, "message", "msg", "friend", None, {"body": "go climb", "suggest": "climb"})
        m.perceive([e], tick, "home", {})
    pulls = [r["pull"] for r in m.influence_log]
    assert pulls[0] > 0 and pulls[1] > 0 and pulls[2] < 0
    assert any(e.name == "irritation" for e in m.affect.emotions)


def test_unasked_help_can_sting():
    from ana.world.world import WorldEvent

    m = Mind(demo_seed())  # Rui: autonomy 0.8, hates being managed
    for tick in (10, 60):
        m.perceive([WorldEvent(tick, 1, 5, "gift", "money", "friend", None, {"amount": 40, "note": ""})], tick, "home", {})
    assert any(e.name == "discomfort" for e in m.affect.emotions)


def test_influence_moves_it_without_deciding_for_it():
    msg = [{"day": 2, "hour": 9, "message": "Climbed the lighthouse yet?", "suggest": "climb"}]

    def rate(autonomy, script):
        d = copy.deepcopy(DEMO_SEED)
        d["character"]["drives"]["autonomy"] = autonomy
        seed = SeedSelf(**d)
        lives = [asyncio.run(run_life(seed, 3, ws, None, [dict(e) for e in script])) for ws in range(30)]
        return sum(any(x.kind == "climb" and x.day >= 2 for x in l.mind.decisions) for l in lives) / 30

    proud_base, proud = rate(0.8, []), rate(0.8, msg)
    easy = rate(0.3, msg)
    assert proud > proud_base  # it listens
    assert proud < 0.7  # but it isn't obeying
    assert easy > proud  # and how much it listens depends on who it is
