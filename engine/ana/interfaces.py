"""Protocols for ANA's pluggable modules.

Each major component implements one of these. The Feedback Loop Engine depends
only on these interfaces, so any module can be swapped (e.g. a deterministic
Generative Engine for an LLM-backed one) without touching the orchestration.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import (
    AlignmentReport,
    CycleRecord,
    GenerationRequest,
    HarmonizerVerdict,
    IdentitySnapshot,
    Outcome,
    Scene,
    SynchronicityHint,
    UserAction,
    WorldState,
)


@runtime_checkable
class GenerativeEngine(Protocol):
    """The imagination of ANA: turns a request into a Scene."""

    def generate(self, request: GenerationRequest) -> Scene: ...


@runtime_checkable
class NPCEntropyLayer(Protocol):
    """Autonomous NPC behaviour + controlled random events."""

    def populate(self, world: WorldState, identity: IdentitySnapshot) -> WorldState:
        """Seed/adjust NPCs for the current world and identity."""
        ...

    def inject_entropy(self, scene: Scene, world: WorldState) -> Scene:
        """Possibly add an unplanned, context-aware event to a scene."""
        ...

    def react(self, scene: Scene, action: UserAction, world: WorldState) -> list[str]:
        """Return NPC reactions (as event strings) to the user's action."""
        ...


@runtime_checkable
class SynchronicityEngine(Protocol):
    """Orchestrates meaningful coincidences with probability + cooldown."""

    def consider(self, identity: IdentitySnapshot, world: WorldState) -> SynchronicityHint | None: ...


@runtime_checkable
class AlignmentProtocols(Protocol):
    """Homeostatic guidance: keeps the journey constructive and safe."""

    def evaluate(self, identity: IdentitySnapshot, world: WorldState, scene: Scene) -> AlignmentReport: ...


@runtime_checkable
class WorldTruthHarmonizer(Protocol):
    """Reality anchoring, continuity, and symbolism calibration."""

    def review(self, scene: Scene, world: WorldState, identity: IdentitySnapshot) -> HarmonizerVerdict: ...


@runtime_checkable
class Interpreter(Protocol):
    """Turns a played-out scene + action into an Outcome fed back to identity."""

    def interpret(self, scene: Scene, action: UserAction, world: WorldState,
                  identity: IdentitySnapshot) -> Outcome: ...


@runtime_checkable
class Driver(Protocol):
    """Supplies the user's action for a scene (human, scripted, or autonomous)."""

    def act(self, scene: Scene, identity: IdentitySnapshot) -> UserAction: ...


@runtime_checkable
class Observer(Protocol):
    """Receives each completed cycle record (for logging, UI, metrics)."""

    def on_cycle(self, record: CycleRecord) -> None: ...
