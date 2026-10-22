"""
What a life looked like, in numbers and in its own words.

``summarize`` computes the agency metrics from plan §12 and checks the Phase 1
exit criteria from plan §11.  ``write`` puts a run on disk: a summary, one page
per day (the twin's journal plus what it did), and its letters to the friend.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Dict, List

from ..mind.state import DRIVES
from ..world.town import TICKS_PER_HOUR
from .loop import Life

PHASE1 = {"days": 14, "self_initiated": 0.40, "self_goals": 1, "max_same_hour": 0.70}


def predictability(life: Life) -> float:
    """Share of hours spent doing the same thing as at that hour the day before (sleep excluded)."""
    same = total = 0
    for (day, hour), kind in life.timeline.items():
        prev = life.timeline.get((day - 1, hour))
        if prev is None or kind == "sleep" or prev == "sleep":
            continue
        total += 1
        same += kind == prev
    return same / total if total else 0.0


def summarize(life: Life) -> Dict:
    m = life.mind
    decisions = m.decisions
    n = len(decisions) or 1
    days = life.world.tick // (24 * TICKS_PER_HOUR)  # full days lived
    self_goals = [g for g in m.goals if g.origin == "self"]
    taken = [r for r in m.influence_log if r["taken"]]
    summary = {
        "name": m.name,
        "days": days,
        "ticks": life.ticks,
        "decisions": len(decisions),
        "self_initiated": round(sum(d.self_initiated for d in decisions) / n, 3),
        "cheap_path": round(1 - life.deliberations / max(1, life.ticks), 3),
        "llm_calls": life.narrator.calls,
        "collapses": life.collapses,
        "same_hour_as_yesterday": round(predictability(life), 3),
        "actions": dict(Counter(d.kind for d in decisions).most_common()),
        "drive_means": {d: round(sum(r.mean(d) for r in m.days.values()) / max(1, len(m.days)), 3) for d in DRIVES},
        "money": round(life.world.body.money),
        "phase": m.temperament.phase(life.world.tick),
        "temperament": {"omega": round(m.temperament.omega, 3), "alpha": round(m.temperament.alpha, 3)},
        "goals": [
            {"text": g.text, "origin": g.origin, "status": g.status, "progress": round(g.progress, 2),
             "reason": g.reason, "closed": g.closed_reason, "day": g.created_day}
            for g in m.goals
        ],
        "relationships": [
            {"name": r.name, "stance": r.stance, "affection": round(r.affection, 2), "trust": round(r.trust, 2),
             "warm": r.warm, "cold": r.cold}
            for r in sorted(m.relationships.values(), key=lambda r: -r.affection)
        ],
        "influence": {"received": len(m.influence_log), "taken": len(taken),
                      "rate": round(len(taken) / len(m.influence_log), 3) if m.influence_log else None},
        "discoveries": list(life.world.discoveries),
    }
    summary["phase1"] = {
        f"ran {PHASE1['days']}+ days unattended": days >= PHASE1["days"],
        f"≥{PHASE1['self_initiated']:.0%} of actions self-initiated": summary["self_initiated"] >= PHASE1["self_initiated"],
        "started at least one goal nobody gave it": len(self_goals) >= PHASE1["self_goals"],
        f"unpredictable (≤{PHASE1['max_same_hour']:.0%} same as yesterday)": summary["same_hour_as_yesterday"] <= PHASE1["max_same_hour"],
    }
    return summary


def _markdown(summary: Dict) -> str:
    s = summary
    lines = [f"# {s['name']}: {s['days']} days", ""]
    lines += ["## Phase 1 exit criteria", ""]
    lines += [f"- {'✅' if ok else '❌'} {name}" for name, ok in s["phase1"].items()]
    lines += ["- ⬜ self-story stays free of contradictions (needs the language-model narrator; not checked offline)", ""]
    lines += ["## Agency", "",
              f"- Decisions: {s['decisions']} ({s['self_initiated']:.0%} self-initiated)",
              f"- Cheap path (kept going without deliberating): {s['cheap_path']:.0%} of ticks",
              f"- Same activity as the same hour yesterday: {s['same_hour_as_yesterday']:.0%}",
              f"- Temperament: {s['phase']} (ω {s['temperament']['omega']}, α {s['temperament']['alpha']})",
              f"- Influence: {s['influence']['taken']}/{s['influence']['received']} suggestions acted on",
              f"- Language-model calls: {s['llm_calls']}", ""]
    lines += ["## Goals", ""]
    for g in s["goals"]:
        tail = f": {g['closed']}" if g["closed"] else ""
        lines.append(f"- [{g['status']}] {g['text']} ({g['origin']}, day {g['day']}, {g['progress']:.0%}){tail}")
    lines += ["", "## People", ""]
    lines += [f"- {r['name']}: {r['stance']} (affection {r['affection']:+.2f}, {r['warm']} warm / {r['cold']} cold)"
              for r in s["relationships"]]
    lines += ["", "## Needs (mean level over the run)", ""]
    lines += [f"- {d}: {v:.2f}" for d, v in s["drive_means"].items()]
    if s["discoveries"]:
        lines += ["", "## Found along the coast road", ""] + [f"- {d}" for d in s["discoveries"]]
    return "\n".join(lines) + "\n"


def write(life: Life, out_dir: Path) -> Dict:
    out_dir = Path(out_dir)
    days_dir = out_dir / "days"
    days_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(life)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "summary.md").write_text(_markdown(summary), encoding="utf-8")
    by_day: Dict[int, List] = {}
    for d in life.mind.decisions:
        by_day.setdefault(d.day, []).append(d)
    for day, rec in sorted(life.mind.days.items()):
        lines = [f"# Day {day}", "", "## Journal", "", life.journal.get(day, "(no entry)"), "", "## What it chose", ""]
        for d in by_day.get(day, []):
            if d.kind == "go":
                continue
            lines.append(f"- {d.clock.split(', ')[1]} at {d.place}: **{d.chosen}** ({', '.join(d.reasons)}); "
                         f"over {', '.join(k for k, _ in d.alternatives[:2])}")
        (days_dir / f"day-{day:02d}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    letters = [f"**{l['clock']}**\n\n{l['text']}\n" for l in life.letters]
    (out_dir / "letters.md").write_text("# Letters to the old friend\n\n" + "\n".join(letters), encoding="utf-8")
    return summary
