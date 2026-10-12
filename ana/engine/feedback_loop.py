"""Feedback Loop Engine — the heartbeat of ANA.

This is the orchestrator from the blueprint: the control loop that turns the
static components into a living system. Each cycle it reads the identity, asks
the generative engine to expand the world, lets the protagonist act, collects the
outcome, and folds it back into the self — moderated by synchronicity, alignment,
and truth-harmonization, and driven by the pi-phi convergence dynamics.

Blueprint cycle (annotated with where each step happens):

    state   = IdentityCore.getState()              # identity.snapshot()
    request = GenerativeEngine.createRequest(state) # _build_request(...)
    scene   = GenerativeEngine.generate(request)    # + entropy + harmonization
    Environment.apply(scene); user acts            # driver.act(...)
    results = Environment.captureChanges()          # interpreter.interpret(...)
    IdentityCore.update(results)                    # identity.apply_outcome(...)
    SelfAlignment.checkAndAdjust(...)               # alignment feeds next cycle

The engine depends only on the Protocols in `interfaces.py`, so every module is
swappable.
"""
from __future__ import annotations

from typing import Optional

from ..contracts import (
    CycleRecord,
    GenerationRequest,
    Scene,
    SceneBeat,
)
from ..interfaces import (
    AlignmentProtocols,
    Driver,
    GenerativeEngine,
    Interpreter,
    NPCEntropyLayer,
    Observer,
    SynchronicityEngine,
    WorldTruthHarmonizer,
)
from ..identity.core import IdentityCore
from ..environment.mirror import EnvironmentMirror
from .convergence import ConvergenceEngine


class FeedbackLoopEngine:
    def __init__(
        self,
        *,
        identity: IdentityCore,
        environment: EnvironmentMirror,
        generative: GenerativeEngine,
        npc: NPCEntropyLayer,
        synchronicity: SynchronicityEngine,
        alignment: AlignmentProtocols,
        harmonizer: WorldTruthHarmonizer,
        interpreter: Interpreter,
        driver: Driver,
        convergence: Optional[ConvergenceEngine] = None,
        observers: Optional[list[Observer]] = None,
        seed: int = 42,
    ):
        self.identity = identity
        self.environment = environment
        self.generative = generative
        self.npc = npc
        self.synchronicity = synchronicity
        self.alignment = alignment
        self.harmonizer = harmonizer
        self.interpreter = interpreter
        self.driver = driver
        self.convergence = convergence or ConvergenceEngine(seed=seed)
        self.observers = observers or []

        # Carry-over state the Self-Alignment Protocols set for the next cycle.
        self._next_directive = "begin the journey"
        self._next_theme: Optional[str] = None
        self._next_intensity = 0.4
        self._next_constraints: list[str] = []
        self.history: list[CycleRecord] = []

    # ------------------------------------------------------------------
    def run(self, cycles: int) -> list[CycleRecord]:
        for _ in range(cycles):
            self.step()
        return self.history

    def step(self) -> CycleRecord:
        ident = self.identity.snapshot()

        # The reflectiveness dial rises with convergence (inner-outer polarity
        # closing); alignment can nudge it further.
        reflectiveness = ident.convergence
        if "raise the reflectiveness dial" in self._next_constraints:
            reflectiveness = min(1.0, reflectiveness + 0.15)
        # The outer journey advances with convergence, then the world is shaped
        # to mirror the self, scaled by the reflectiveness dial.
        self.environment.advance_place(ident.convergence)
        world = self.environment.reflect(ident, reflectiveness)
        world = self.npc.populate(world, ident)

        # Synchronicity: a meaningful coincidence to weave in, maybe.
        hint = self.synchronicity.consider(ident, world)

        # Recall past + future-memory shards for continuity and backcasting.
        recalled = self.identity.memory.recall(
            " ".join(ident.active_themes) or "journey", top_k=3
        )
        recalled += self.identity.memory.recall_future(top_k=2)

        request = GenerationRequest(
            identity=ident,
            world=world,
            directive=self._next_directive,
            target_theme=self._next_theme,
            target_intensity=self._next_intensity,
            synchronicity=hint,
            recalled_shards=recalled,
            constraints=list(self._next_constraints),
        )

        scene = self.generative.generate(request)
        scene = self.npc.inject_entropy(scene, world)

        # World-Truth Harmonizer: continuity + reality anchoring.
        verdict = self.harmonizer.review(scene, world, ident)
        scene = self._apply_harmonizer(scene, verdict)

        # Self-Alignment: safety + homeostasis. Regenerate if a safety line is hit.
        report = self.alignment.evaluate(ident, world, scene)
        if report.blocked:
            scene, report = self._handle_block(request, ident, world, report)

        # The protagonist acts; NPCs react.
        action = self.driver.act(scene, ident)
        npc_events = self.npc.react(scene, action, world)

        # Interpret the outcome and run the convergence dynamics.
        outcome = self.interpreter.interpret(scene, action, world, ident)
        outcome.npc_events = npc_events
        novelty = min(1.0, scene.intensity + (0.3 if len(scene.beats) and
                      scene.beats[-1].kind == "event" else 0.0))
        conv = self.convergence.step(novelty)

        # Feed back into the self and advance the world.
        self.identity.apply_outcome(outcome)
        self.identity.advance(conv.convergence)
        self.environment.advance()

        # Let alignment shape the next cycle.
        self._carry_forward(report, outcome)

        record = CycleRecord(
            tick=ident.tick,
            identity_before=ident,
            request=request,
            scene=scene,
            alignment=report,
            harmonizer=verdict,
            outcome=outcome,
            convergence=conv.convergence,
            omega_surprise=conv.omega_surprise,
            alpha_surprise=conv.alpha_surprise,
        )
        self.history.append(record)
        for obs in self.observers:
            obs.on_cycle(record)
        return record

    # ------------------------------------------------------------------
    def _apply_harmonizer(self, scene: Scene, verdict) -> Scene:
        if verdict.is_vision:
            scene.is_vision = True
        if verdict.grounding_note:
            scene.beats.append(SceneBeat(kind="dialogue", speaker="a grounding voice",
                                         text=verdict.grounding_note))
        # Continuity corrections are surfaced as constraints on the next request.
        if verdict.corrections:
            self._next_constraints = list(dict.fromkeys(
                self._next_constraints + verdict.corrections))
        return scene

    def _handle_block(self, request, ident, world, report):
        """A scene crossed a safety line: regenerate gently, once; else interlude."""
        safe_req = request.model_copy(update={
            "directive": "offer a gentle, safe interlude that still honors the theme",
            "target_intensity": min(request.target_intensity, 0.25),
            "constraints": list(dict.fromkeys(request.constraints + [
                report.block_reason or "avoid the blocked content", "keep gentle",
                "provide hope",
            ])),
        })
        scene = self.generative.generate(safe_req)
        new_report = self.alignment.evaluate(ident, world, scene)
        if new_report.blocked:
            scene = _safe_interlude(request)
            new_report = self.alignment.evaluate(ident, world, scene)
        return scene, new_report

    def _carry_forward(self, report, outcome) -> None:
        self._next_intensity = report.recommended_intensity
        self._next_constraints = list(report.nudges)
        # Leave the next theme open so the Generative Engine rotates across the
        # identity's active themes — this keeps the journey from fixating on one
        # theme and lets every active theme eventually be reflected and worked
        # through. Alignment still steers tone/intensity via nudges + directive.
        self._next_theme = None
        if report.nudges:
            self._next_directive = report.nudges[0]
        else:
            self._next_directive = "deepen the journey"


def _safe_interlude(request) -> Scene:
    """A guaranteed-safe resting scene used if regeneration still isn't safe."""
    return Scene(
        title="A place to rest",
        summary="The world softens into a quiet, safe clearing where nothing is "
                "asked of you.",
        beats=[
            SceneBeat(kind="description",
                      text="Warm light, still air. A bench, a stream, time to breathe."),
            SceneBeat(kind="dialogue", speaker="a kind presence",
                      text="You are safe here. Rest as long as you need."),
        ],
        theme="rest",
        intensity=0.1,
        choices=["Rest and breathe", "Reflect quietly", "Move on when ready"],
        source="deterministic",
    )
