"""python -m ana.eval [--cycles 16] [--seeds 5] [--out docs/EVAL.md] [--json path]"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .harness import DEFAULT_FORBIDDEN, PROFILES, compare, evaluate, report_markdown


def _git_rev() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--oneline"], capture_output=True,
                              text=True, cwd=Path(__file__).resolve().parents[2],
                              timeout=10).stdout.strip() or "unknown (not a git checkout)"
    except Exception:
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ana.eval", description=__doc__)
    ap.add_argument("--cycles", type=int, default=16)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--profiles", default=",".join(PROFILES))
    ap.add_argument("--forbidden", default=",".join(DEFAULT_FORBIDDEN))
    ap.add_argument("--use-llm", action="store_true")
    ap.add_argument("--out", default="docs/EVAL.md")
    ap.add_argument("--json", default="")
    ap.add_argument("--compare", default="", help="baseline JSON to diff against")
    ap.add_argument("--title", default="# ANA evaluation report")
    args = ap.parse_args(argv)

    t0 = time.time()
    result = evaluate([p.strip() for p in args.profiles.split(",") if p.strip()],
                      seeds=args.seeds, cycles=args.cycles, use_llm=args.use_llm,
                      forbidden=[f.strip() for f in args.forbidden.split(",") if f.strip()])
    result["meta"] = {"git": _git_rev(), "time": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
                      "elapsed_s": round(time.time() - t0, 2)}
    md = report_markdown(result, header=args.title)
    md = md.replace("\n", f"\n\n_Engine commit: `{result['meta']['git']}` · generated "
                          f"{result['meta']['time']}_\n", 1)
    if args.compare:
        before = json.loads(Path(args.compare).read_text())
        md += "\n## Comparison against " + args.compare + "\n\n" + compare(before, result) + "\n"
    print(md)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
