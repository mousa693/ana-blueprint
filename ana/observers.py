"""Observers — side-channel consumers of each completed cycle.

The Feedback Loop Engine stays pure; anything that wants to watch the journey
(console rendering, JSONL logging, metrics) implements `Observer` and is handed
each `CycleRecord`.
"""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import CycleRecord


class ConsoleObserver:
    """Renders each cycle as readable narrative + a telemetry line."""

    def __init__(self, show_telemetry: bool = True):
        self.show_telemetry = show_telemetry

    def on_cycle(self, record: CycleRecord) -> None:
        s = record.scene
        print(f"\n{'═' * 70}")
        print(f"CYCLE {record.tick}  ·  {s.title or s.theme}")
        if s.is_vision:
            print("  (this is a vision — not a literal event)")
        print(f"{'─' * 70}")
        if s.summary:
            print(s.summary)
        for b in s.beats:
            who = f"{b.speaker}: " if b.speaker else ""
            marker = {"synchronicity": "✦", "event": "⚡", "choice": "?",
                      "dialogue": "“", "description": "·"}.get(b.kind, "·")
            print(f"  {marker} {who}{b.text}")
        act = record.outcome.user_action
        if act:
            print(f"\n  ➤ {act.raw}")
        for ev in record.outcome.npc_events:
            print(f"    · {ev}")
        if record.outcome.lesson:
            print(f"  ✎ lesson: {record.outcome.lesson}")
        if self.show_telemetry:
            r = record.alignment
            print(f"{'─' * 70}")
            print(
                f"  convergence={record.convergence:.2f}  "
                f"reflection={r.reflection_pct:.2f}  stress={r.stress:.2f}  "
                f"next_intensity={r.recommended_intensity:.2f}  "
                f"Ω={record.omega_surprise:.3f} α={record.alpha_surprise:.3f}"
            )
            if r.nudges:
                print(f"  nudge → {r.nudges[0]}")


class JSONLObserver:
    """Appends each cycle to a JSONL file for later inspection/replay."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate on construction so each run starts clean.
        self.path.write_text("")

    def on_cycle(self, record: CycleRecord) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(record.model_dump_json() + "\n")
