"""Golden journeys for the step() -> begin_cycle()/complete_cycle() refactor.

The JSONL files next to this script were generated from the pre-refactor
engine (git HEAD 0682ed0) with::

    python3 tests/golden/make_golden.py

Each line is one normalised CycleRecord (ids and timestamps masked). The test
in tests/test_runtime.py re-runs the same journeys through the refactored loop
and asserts byte equality. Do NOT regenerate them unless the loop's behaviour
is meant to change.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHORT_ID = re.compile(r'"(scene|npc)_[0-9a-f]{12}"')
_TS = re.compile(r'"(ts|timestamp|created|created_at|last_accessed)": [0-9.e+-]+')

SCRIPT = [
    "I explore the town and talk to the fishermen.",
    "I climb toward the lighthouse to see what's wrong.",
    "I organize a rescue and signal the stranded ship.",
    "I speak honestly to the council about what happened.",
    "I accept the role they offer, uneasy but willing.",
]

# (name, seed, driver kind, forbidden themes)
CASES = [(f"{kind}_s{seed}{'_forbid' if forbid else ''}", seed, kind, forbid)
         for seed in (1, 2, 3)
         for kind, forbid in (("scripted", []), ("auto", []), ("auto", ["leadership"]))]


def norm_record(r) -> str:
    s = json.dumps(r.model_dump(mode="json"), sort_keys=True, default=str)
    s = _UUID.sub("UUID", s)
    s = _SHORT_ID.sub('"ID"', s)
    return _TS.sub(r'"\1": 0', s)


def build(seed: int, kind: str, forbid: list[str]):
    from ana.cli import _seed_lighthouse_identity
    from ana.config import ANAConfig
    from ana.engine.drivers import ScriptedDriver
    from ana.session import build_session

    cfg = ANAConfig(use_llm=False, max_cycles=30, seed=seed, forbidden_themes=list(forbid))
    driver = ScriptedDriver(SCRIPT) if kind == "scripted" else None
    return build_session(cfg, identity=_seed_lighthouse_identity(), driver=driver)


def journey_lines(seed: int, kind: str, forbid: list[str]) -> list[str]:
    sess = build(seed, kind, forbid)
    try:
        recs = sess.run(30, until_close=True)
        lines = [norm_record(r) for r in recs]
        lines.append(json.dumps({"closed": sess.engine.closed,
                                 "chapter": sess.arc.current_chapter()}, sort_keys=True))
        return lines
    finally:
        sess.close()


def main() -> None:
    for name, seed, kind, forbid in CASES:
        lines = journey_lines(seed, kind, forbid)
        (HERE / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(name, len(lines) - 1, "records")


if __name__ == "__main__":
    main()
