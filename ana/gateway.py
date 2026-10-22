"""
The Influence Gateway (plan §5): how the creator reaches the twin.

This module is the whole of the creator's power over the twin's life, and it
only knows about the *world*.  It cannot see or touch the mind; there is no
write path into it (plan §1).  Everything here turns into an event the twin
may perceive, misread, resist or embrace.
"""

from __future__ import annotations

from typing import Optional

from .world.town import ACTIONS_BY_KIND
from .world.world import CIRCUMSTANCES, World, WorldEvent

WHISPERS_PER_DAY = 2  # beyond this a passing thought becomes an intrusion


class Gateway:
    def __init__(self, world: World, friend_name: str) -> None:
        self.world = world
        self.friend_name = friend_name
        self._whispers: dict[int, int] = {}
        self._circumstance_day: Optional[int] = None

    def message(self, text: str, suggest: Optional[str] = None) -> WorldEvent:
        """A message from the old friend.  ``suggest`` names an action kind the message nudges toward."""
        if suggest is not None and suggest not in ACTIONS_BY_KIND:
            raise ValueError(f"unknown action kind {suggest!r}")
        return self.world.emit("message", f"A message from {self.friend_name}: \"{text}\"", "friend",
                               body=text, suggest=suggest)

    def gift(self, amount: float, note: str = "") -> WorldEvent:
        self.world.body.money += amount
        text = f"{self.friend_name} sent {amount:.0f}" + (f" with a note: \"{note}\"" if note else ".")
        return self.world.emit("gift", text, "friend", amount=amount, note=note)

    def circumstance(self, cid: str) -> WorldEvent:
        """Shape the world itself.  Unattributed; at most one per world day."""
        if cid not in CIRCUMSTANCES:
            raise ValueError(f"unknown circumstance {cid!r}; options: {sorted(CIRCUMSTANCES)}")
        if self._circumstance_day == self.world.day:
            raise RuntimeError("one circumstance per day: a world that bends too often stops feeling like a world")
        self._circumstance_day = self.world.day
        return self.world.add_circumstance(cid)

    def whisper(self, action_kind: str) -> WorldEvent:
        """A passing thought.  Weak; past WHISPERS_PER_DAY it starts to feel intrusive."""
        if action_kind not in ACTIONS_BY_KIND:
            raise ValueError(f"unknown action kind {action_kind!r}")
        n = self._whispers.get(self.world.day, 0) + 1
        self._whispers[self.world.day] = n
        return self.world.emit("thought", f"The thought of {ACTIONS_BY_KIND[action_kind].label} keeps coming back.",
                               "world", None, suggest=action_kind, intrusive=n > WHISPERS_PER_DAY)
