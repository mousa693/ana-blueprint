"""
Fidelity test: does the Seed Self answer the held-out battery items the way the creator did?

Three numbers per run:
    twin      the model answering *as* the Seed Self
    baseline  the same model answering as "a typical adult" (no seed)
    chance    what uniform random answers would score

Phase 0 exits when twin is clearly above both (plan §11).  Re-run monthly
against the live twin to track fidelity as it grows away from its seed.

Scoring: choice items score 1 for the same option, else 0.  Scale items score
1 - |difference| / (points - 1), so being off by one on a 7-point scale scores 0.83.
"""

from __future__ import annotations

import json
from typing import Dict, List

from pydantic import BaseModel

from ..llm import StructuredLLM
from .battery import Answer, Battery, Item
from .seed import SeedSelf

AS_SEED_SYSTEM = """\
This is you: your history, character, people, loves, fears and voice.

{seed}

Answer each question the way you actually would. Not the wise answer, not the nice one: yours."""

BASELINE_SYSTEM = "Answer each question the way a typical adult would."


class ItemAnswer(BaseModel):
    id: str
    value: int


class AnswerSheet(BaseModel):
    answers: List[ItemAnswer]


def item_score(item: Item, given: int, truth: int, scale_points: int) -> float:
    if item.kind == "choice":
        return 1.0 if given == truth else 0.0
    return 1.0 - abs(given - truth) / (scale_points - 1)


def chance_score(item: Item, truth: int, scale_points: int) -> float:
    if item.kind == "choice":
        return 1.0 / len(item.options)
    return sum(item_score(item, v, truth, scale_points) for v in range(1, scale_points + 1)) / scale_points


def questionnaire(items: List[Item], scale_points: int) -> str:
    blocks = []
    for item in items:
        blocks.append(f"[{item.id}] {item.render(scale_points)}")
    return (
        "Answer every question below. For each, return its id and the number of your answer "
        "(the option number, or the point on the scale).\n\n" + "\n\n".join(blocks)
    )


def _answer(llm: StructuredLLM, system: str, items: List[Item], scale_points: int) -> Dict[str, int]:
    sheet = llm.structured(
        system=system,
        messages=[{"role": "user", "content": questionnaire(items, scale_points)}],
        schema=AnswerSheet,
        effort="medium",
        max_tokens=8000,
    )
    return {a.id: a.value for a in sheet.answers}


def evaluate(llm: StructuredLLM, seed: SeedSelf, battery: Battery, truth: Dict[str, Answer]) -> Dict:
    items = [i for i in battery.holdout_items() if i.id in truth]
    if not items:
        raise ValueError("no held-out items answered yet: finish the battery first")
    seed_json = json.dumps(seed.model_dump(exclude={"open_questions"}), indent=1, ensure_ascii=False)
    as_twin = _answer(llm, AS_SEED_SYSTEM.format(seed=seed_json), items, battery.scale_points)
    as_baseline = _answer(llm, BASELINE_SYSTEM, items, battery.scale_points)

    per_item = []
    by_domain: Dict[str, List[Dict]] = {}
    for item in items:
        t = truth[item.id].value
        row = {
            "id": item.id,
            "domain": item.domain,
            "prompt": item.prompt,
            "creator": t,
            "twin": as_twin.get(item.id),
            "baseline": as_baseline.get(item.id),
            "twin_score": _safe_score(item, as_twin.get(item.id), t, battery.scale_points),
            "baseline_score": _safe_score(item, as_baseline.get(item.id), t, battery.scale_points),
            "chance_score": chance_score(item, t, battery.scale_points),
        }
        per_item.append(row)
        by_domain.setdefault(item.domain, []).append(row)

    def summarize(rows: List[Dict]) -> Dict:
        n = len(rows)
        return {
            "n": n,
            "twin": round(sum(r["twin_score"] for r in rows) / n, 3),
            "baseline": round(sum(r["baseline_score"] for r in rows) / n, 3),
            "chance": round(sum(r["chance_score"] for r in rows) / n, 3),
        }

    return {
        "overall": summarize(per_item),
        "by_domain": {d: summarize(rows) for d, rows in sorted(by_domain.items())},
        "items": per_item,
    }


def _safe_score(item: Item, given: int | None, truth: int, scale_points: int) -> float:
    """A missing or out-of-range answer scores 0."""
    if given is None:
        return 0.0
    limit = len(item.options) if item.kind == "choice" else scale_points
    if not 1 <= given <= limit:
        return 0.0
    return item_score(item, given, truth, scale_points)
