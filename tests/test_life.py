import asyncio
import json

from ana.genesis.leakage import scan
from ana.life.__main__ import main, run_life
from ana.life.demo import demo_seed
from ana.life.loop import Life
from ana.life.narrator import ClaudeNarrator, Voiced
from ana.life.report import summarize
from ana.memory.mcmg import MCMG, CoreOrigin
from ana.temperament.temperament import Temperament

from conftest import FakeLLM


def live(days=14, world_seed=7):
    life = Life(demo_seed(), world_seed=world_seed)
    asyncio.run(life.run(days))
    return life


def test_fourteen_days_unattended_meets_phase_one():
    s = summarize(live())
    assert s["days"] == 14
    assert all(s["phase1"].values()), s["phase1"]
    assert s["llm_calls"] == 0
    assert 0.5 < s["cheap_path"] < 0.95  # mostly just living, deliberating when it matters


def test_same_world_same_life_different_world_different_life():
    a, b, c = live(7, 7), live(7, 7), live(7, 8)
    keys = lambda l: [d.chosen for d in l.mind.decisions]
    assert keys(a) == keys(b)
    assert keys(a) != keys(c)


def test_every_decision_says_why_and_what_it_passed_over():
    for d in live(3).mind.decisions:
        assert d.reasons and d.alternatives and d.trigger


def test_its_words_never_break_the_cover_story():
    life = live()
    assert scan(list(life.journal.values()) + [l["text"] for l in life.letters]) == []
    assert len(life.journal) >= 13


def test_scripted_influence_reaches_it_as_perception():
    script = [{"day": 1, "hour": 12, "message": "Thinking of you.", "suggest": None},
              {"day": 2, "hour": 9, "gift": 30}]
    life = asyncio.run(run_life(demo_seed(), 3, 7, None, script))
    kinds = [e.kind for e in life.world.log]
    assert "message" in kinds and "gift" in kinds
    history = life.mind.relationships["friend"].history
    assert any(h.startswith("wrote to me: Thinking of you") for h in history)
    assert any("sent help" in h or "sent money" in h for h in history)


def test_cli_run_writes_a_life(tmp_path):
    out = tmp_path / "run"
    assert main(["run", "--days", "14", "--out", str(out)]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["days"] == 14
    assert (out / "days" / "day-01.md").exists() and (out / "letters.md").exists()
    assert sum(1 for _ in open(out / "decisions.jsonl")) == s["decisions"]


def test_temperament_explores_when_surprised_and_gets_restless_when_flat():
    t = Temperament()
    for i in range(50):
        t.observe(0.9, 0.8, i)
    assert t.phase(50) == "exploring"
    t = Temperament()
    for i in range(300):
        t.observe(0.0, 0.2, i)
    assert t.restless_until > 0


def test_mcmg_decays_in_world_time():
    async def go():
        g = MCMG()
        nid = await g.add("a storm", origin=CoreOrigin.OMEGA, confidence=0.5)
        await g.decay(elapsed=10.0)
        return g.get(nid).confidence

    assert abs(asyncio.run(go()) - (0.5 - 0.012 * 10)) < 1e-9


def test_claude_narrator_budget_and_leak_fallback():
    replies = ["Quiet day. Mara laughed at me, kindly.", "I'm only a program, really.", "unused"]
    llm = FakeLLM(lambda schema, m, s: Voiced(text=replies.pop(0)))
    life = Life(demo_seed(), world_seed=7)
    asyncio.run(life.run(1))
    narrator = ClaudeNarrator(llm, calls_per_day=2)
    from ana.life.narrator import day_facts

    facts = day_facts(life.mind, 1, "rain", 50)
    first = narrator.journal(life.mind, 5, facts)
    second = narrator.journal(life.mind, 5, facts)  # leaks → template
    third = narrator.journal(life.mind, 5, facts)  # over budget → template, no call
    assert first.startswith("Quiet day")
    assert "program" not in second and second.startswith("Day 5")
    assert third.startswith("Day 5") and len(llm.calls) == 2
    assert "Rui" in llm.calls[0]["system"]


def test_resume_continues_the_same_life(tmp_path):
    async def go():
        straight = Life(demo_seed(), world_seed=7)
        await straight.run(9)
        first = Life(demo_seed(), world_seed=7)
        await first.run(4)
        await first.save(tmp_path / "snap")
        resumed = await Life.load(tmp_path / "snap")
        await resumed.run(5)
        return straight, resumed

    straight, resumed = asyncio.run(go())
    assert [d.chosen for d in straight.mind.decisions] == [d.chosen for d in resumed.mind.decisions]
    assert straight.journal == resumed.journal
    assert len(resumed.mind.memory) == len(straight.mind.memory)


def test_it_remembers_its_life_here_not_just_its_past():
    life = live(28)
    nodes = list(life.mind.memory)
    weeks = [n for n in nodes if "week" in n.tags]
    assert len(weeks) == 4
    assert any(n.origin == CoreOrigin.ALPHA for n in nodes)
    assert any("reminded me" in j for j in life.journal.values())


def test_cli_resume_appends_to_the_same_life(tmp_path):
    out = tmp_path / "run"
    assert main(["run", "--days", "7", "--out", str(out)]) in (0, 2)
    first = sum(1 for _ in open(out / "decisions.jsonl"))
    assert main(["run", "--resume", str(out), "--days", "7"]) == 0
    s = json.loads((out / "summary.json").read_text())
    assert s["days"] == 14
    assert sum(1 for _ in open(out / "decisions.jsonl")) == s["decisions"] > first
