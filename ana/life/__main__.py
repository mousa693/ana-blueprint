"""
Run a life.

    python -m ana.life run --days 14                       the demo twin, offline
    python -m ana.life run --seed genesis_data/seed_self.json --days 14
    python -m ana.life run --influence plan.json           with scripted influence (see below)
    python -m ana.life run --narrator claude               journal and letters in its own voice
    python -m ana.life experiment                          does influence work without controlling?

An influence script is a JSON list, each entry one of:
    {"day": 2, "hour": 10, "message": "Have you been up the lighthouse?", "suggest": "climb"}
    {"day": 3, "hour": 9,  "gift": 40, "note": "for rent"}
    {"day": 4, "hour": 8,  "circumstance": "notice_sailing"}
    {"day": 5, "hour": 15, "whisper": "explore"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Dict, List

from ..genesis.seed import SeedSelf
from .demo import demo_seed
from .loop import Life
from .narrator import ClaudeNarrator
from .report import write


def load_seed(path: Path | None) -> SeedSelf:
    if path is None:
        return demo_seed()
    return SeedSelf(**json.loads(Path(path).read_text(encoding="utf-8")))


def apply_influence(life: Life, script: List[Dict]) -> None:
    """Fire every scripted influence whose time has come."""
    w, g = life.world, life.gateway
    for entry in script:
        if entry.get("_done") or (entry["day"], entry["hour"]) > (w.day, w.hour):
            continue
        entry["_done"] = True
        if "message" in entry:
            g.message(entry["message"], entry.get("suggest"))
        elif "gift" in entry:
            g.gift(entry["gift"], entry.get("note", ""))
        elif "circumstance" in entry:
            g.circumstance(entry["circumstance"])
        elif "whisper" in entry:
            g.whisper(entry["whisper"])


async def run_life(seed: SeedSelf, days: int, world_seed: int, out: Path | None, script: List[Dict],
                   narrator=None) -> Life:
    life = Life(seed, world_seed=world_seed, out_dir=out, narrator=narrator)
    await life.run(days, before_tick=lambda l: apply_influence(l, script))
    life.close()
    return life


def cmd_run(args: argparse.Namespace) -> int:
    seed = load_seed(args.seed)
    script = json.loads(Path(args.influence).read_text()) if args.influence else []
    narrator = None
    if args.narrator == "claude":
        from ..llm import LLM

        narrator = ClaudeNarrator(LLM(), calls_per_day=args.llm_calls_per_day)
    out = Path(args.out)
    life = asyncio.run(run_life(seed, args.days, args.world_seed, out, script, narrator))
    s = write(life, out)
    print((out / "summary.md").read_text())
    print(f"Wrote {out}/ (summary.md, days/, letters.md, decisions.jsonl, world_events.jsonl)")
    return 0 if all(s["phase1"].values()) else 2


def cmd_experiment(args: argparse.Namespace) -> int:
    """Same twin, same suggestion, many worlds: influence should move it, not decide for it."""
    seed = load_seed(args.seed)
    suggestion = [{"day": 2, "hour": 9, "message": "Have you climbed that lighthouse yet? I bet the view is something.",
                   "suggest": "climb"}]
    took = base = 0
    for ws in range(args.worlds):
        with_msg = asyncio.run(run_life(seed, 3, ws, None, [dict(e) for e in suggestion]))
        without = asyncio.run(run_life(seed, 3, ws, None, []))
        climbed = any(d.kind == "climb" and 2 <= d.day <= 3 for d in with_msg.mind.decisions)
        climbed_anyway = any(d.kind == "climb" and 2 <= d.day <= 3 for d in without.mind.decisions)
        took += climbed
        base += climbed_anyway
    print(f"Across {args.worlds} worlds, the twin climbed the lighthouse on days 2-3:")
    print(f"  without the message: {base}/{args.worlds}")
    print(f"  after the message:   {took}/{args.worlds}")
    print("Influence is working if the second number is clearly higher but well short of all of them.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ana.life", description="Let the twin live.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--seed", type=Path, help="seed_self.json from Genesis (default: the fictional demo twin)")
    run.add_argument("--days", type=int, default=14)
    run.add_argument("--world-seed", type=int, default=7, help="same number, same world")
    run.add_argument("--out", default="life_data/run")
    run.add_argument("--influence", help="JSON influence script")
    run.add_argument("--narrator", choices=["template", "claude"], default="template")
    run.add_argument("--llm-calls-per-day", type=int, default=3)
    exp = sub.add_parser("experiment")
    exp.add_argument("--seed", type=Path)
    exp.add_argument("--worlds", type=int, default=30)
    args = parser.parse_args(argv)
    return {"run": cmd_run, "experiment": cmd_experiment}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
