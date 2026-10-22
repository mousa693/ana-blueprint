"""
The decision battery: 120 dilemmas and preferences across 12 areas of life.

The creator answers every item.  A fixed 20% (two per domain, picked by hash
so the split never changes) is held out.  Held-out answers never reach
distillation; they exist only to test, later, whether the twin answers the
way the creator does (``fidelity.py``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib import resources
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel

from .store import BATTERY_ANSWERS, Store

HOLDOUT_PER_DOMAIN = 2


class Item(BaseModel):
    id: str
    domain: str
    kind: str  # "choice" | "scale"
    prompt: str
    options: List[str] = []
    anchors: List[str] = []  # [low, high] for scale items

    def render(self, scale_points: int) -> str:
        if self.kind == "choice":
            lines = [f"  {i}. {opt}" for i, opt in enumerate(self.options, 1)]
            return "\n".join([self.prompt, *lines])
        low, high = self.anchors
        return f"{self.prompt}\n  1 = {low}  …  {scale_points} = {high}"

    def describe_answer(self, answer: "Answer", scale_points: int) -> str:
        """Human-readable answer, used when feeding answers to distillation."""
        if self.kind == "choice":
            text = self.options[answer.value - 1]
        else:
            low, high = self.anchors
            text = f"{answer.value}/{scale_points} (1 = {low}, {scale_points} = {high})"
        if answer.note:
            text += f"  [note: {answer.note}]"
        return text


class Answer(BaseModel):
    value: int  # 1-based option index, or scale point
    note: str = ""


@dataclass
class Battery:
    items: List[Item]
    scale_points: int

    @classmethod
    def load(cls) -> "Battery":
        raw = json.loads(resources.files("ana.genesis").joinpath("data/battery.json").read_text(encoding="utf-8"))
        return cls(items=[Item(**i) for i in raw["items"]], scale_points=raw["scale_points"])

    def by_id(self) -> Dict[str, Item]:
        return {i.id: i for i in self.items}

    def holdout_ids(self) -> set[str]:
        """Two items per domain, chosen by a stable hash of the id."""
        by_domain: Dict[str, List[Item]] = {}
        for item in self.items:
            by_domain.setdefault(item.domain, []).append(item)
        held: set[str] = set()
        for domain_items in by_domain.values():
            ranked = sorted(domain_items, key=lambda i: hashlib.sha256(i.id.encode()).hexdigest())
            held.update(i.id for i in ranked[:HOLDOUT_PER_DOMAIN])
        return held

    def seed_items(self) -> List[Item]:
        held = self.holdout_ids()
        return [i for i in self.items if i.id not in held]

    def holdout_items(self) -> List[Item]:
        held = self.holdout_ids()
        return [i for i in self.items if i.id in held]

    def parse(self, item: Item, raw: str) -> Optional[Answer]:
        """Parse '3' or '3 because …' into an Answer; None if invalid."""
        raw = raw.strip()
        head, _, note = raw.partition(" ")
        if not head.isdigit():
            return None
        value = int(head)
        limit = len(item.options) if item.kind == "choice" else self.scale_points
        if not 1 <= value <= limit:
            return None
        return Answer(value=value, note=note.strip())


def load_answers(store: Store) -> Dict[str, Answer]:
    return {k: Answer(**v) for k, v in store.read(BATTERY_ANSWERS, {}).items()}


def save_answers(store: Store, answers: Dict[str, Answer]) -> None:
    store.write(BATTERY_ANSWERS, {k: v.model_dump() for k, v in answers.items()})


def run(
    store: Store,
    battery: Battery,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> Dict[str, Answer]:
    """Ask every unanswered item, saving after each one.  ``q`` stops; rerun to resume."""
    answers = load_answers(store)
    pending = [i for i in battery.items if i.id not in answers]
    say(
        f"{len(answers)}/{len(battery.items)} answered. "
        "Type a number, optionally followed by a short note. 'q' to stop and resume later.\n"
        "Answer as you actually are, not as you'd like to be.\n"
    )
    for item in pending:
        while True:
            raw = ask(f"[{len(answers) + 1}/{len(battery.items)}] {item.render(battery.scale_points)}\n> ")
            if raw.strip().lower() == "q":
                say("Saved. Run again to continue.")
                return answers
            answer = battery.parse(item, raw)
            if answer is not None:
                break
            say("  Didn't catch that. Enter one of the numbers shown.")
        answers[item.id] = answer
        save_answers(store, answers)
    say("Battery complete.")
    return answers
