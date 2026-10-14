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

from dataclasses import dataclass
from typing import Optional

from ..contracts import (
    AlignmentReport,
    CycleRecord,
    GenerationRequest,
    HarmonizerVerdict,
    IdentitySnapshot,
    Outcome,
    Scene,
    SceneBeat,
    UserAction,
    WorldState,
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
from ..subsystems.alignment import has_entropy_shock, mentions_forbidden
from .convergence import ConvergenceEngine
from .arc import JourneyArc

# The safe interlude used when a scene cannot be made safe. Its theme is a
# resting state, not something the self is working on, so it is never learned
# into the identity (see FeedbackLoopEngine._filter_outcome).
INTERLUDE_TITLE = "A place to rest"
INTERLUDE_THEME = "rest"

# Prefix of the request constraints that carry the forbidden list to the
# Generative Engine (it also reads ExperienceEngine.forbidden when present).
NEVER_DEPICT = "never depict:"


@dataclass
class PendingCycle:
    """A cycle whose scene has been dreamed (and passed every safety layer) but
    whose action has not been taken yet: the state ``begin_cycle()`` hands to
    ``complete_cycle()``.

    ``scene`` is the scene to show; ``view`` the safe view of the self the
    driver/person acts from. ``ident`` is the real snapshot (kept for the
    record), ``world`` the live world the NPCs react in."""

    ident: IdentitySnapshot
    view: IdentitySnapshot
    forbidden: list[str]
    stage: Optional[str]
    world: WorldState
    request: GenerationRequest
    scene: Scene
    report: AlignmentReport
    verdict: HarmonizerVerdict


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
        # Harmonizer continuity corrections waiting for the next request. They
        # survive _carry_forward (which rebuilds the constraints from the
        # alignment nudges) and are merged in, then cleared once delivered.
        self._pending_corrections: list[str] = []
        # (scene, verdict) being replaced while _handle_block runs.
        self._rejected: Optional[tuple[Scene, HarmonizerVerdict]] = None
        self.history: list[CycleRecord] = []
        # The journey's arc: which Hero's-journey stage the story is in, the
        # milestones so far, and whether it has come home.
        self.arc = JourneyArc()
        self.closed = False
        # The half-finished cycle between begin_cycle() and complete_cycle().
        self._pending: Optional[PendingCycle] = None

    # ------------------------------------------------------------------
    def run(self, cycles: int, *, until_close: bool = False) -> list[CycleRecord]:
        """Run up to `cycles` heartbeats. With `until_close`, stop as soon as the
        arc says the journey has come home (the return has been lived)."""
        for _ in range(cycles):
            self.step()
            if until_close and self.closed:
                break
        return self.history

    def goal_progress(self) -> float:
        goals = getattr(self.identity, "goals", []) or []
        return max((g.progress for g in goals), default=0.0)

    # ------------------------------------------------------------------
    # Forbidden themes: the safe view
    # ------------------------------------------------------------------
    def forbidden(self) -> list[str]:
        """The forbidden themes, read from the alignment layer (``[]`` when the
        alignment module has none)."""
        return list(getattr(self.alignment, "forbidden_themes", None) or [])

    def _arm_forbidden(self, forbidden: list[str]) -> None:
        """Tell the modules that voice text what they must never voice. Modules
        without a ``forbidden`` attribute are left alone (the request's
        ``never depict:`` constraints and the final gate still apply)."""
        for mod in (self.generative, self.npc, self.harmonizer):
            if hasattr(mod, "forbidden"):
                try:
                    mod.forbidden = list(forbidden)
                except Exception:
                    pass

    @staticmethod
    def _bad(text: Optional[str], forbidden: list[str]) -> bool:
        return bool(text) and mentions_forbidden(text, forbidden, synonyms=True) is not None

    def _safe_view(self, ident: IdentitySnapshot, forbidden: list[str]) -> IdentitySnapshot:
        """The identity as the story may see it: forbidden themes (and lexicon
        synonyms) stripped from active themes, goals, traits, beliefs, recalled
        summaries, insights and symbols. The record keeps the real snapshot as
        ``identity_before``; everything that writes text (environment, cast,
        synchronicity, generator, driver, interpreter) and the alignment
        evaluation get this view. With nothing forbidden it *is* ``ident``."""
        if not forbidden:
            return ident
        bad = lambda t: self._bad(t, forbidden)  # noqa: E731
        goals = [g for g in ident.goals
                 if not bad(g.description) and not any(bad(t) for t in g.themes)]
        return ident.model_copy(deep=True, update={
            "active_themes": [t for t in ident.active_themes if not bad(t)],
            "goals": [g.model_copy(deep=True) for g in goals],
            "traits": [t.model_copy() for t in ident.traits if not bad(t.name)],
            "beliefs": [b for b in ident.beliefs if not bad(b)],
            "recent_shard_summaries": [x for x in ident.recent_shard_summaries if not bad(x)],
            "insights": [x for x in ident.insights if not bad(x)],
            "recurring_symbols": [x for x in ident.recurring_symbols if not bad(x)],
            "callback_symbols": [x for x in ident.callback_symbols if not bad(x)],
            "fulfilled_visions": [x for x in ident.fulfilled_visions if not bad(x)],
        })

    def _safe_world(self, world: WorldState, forbidden: list[str]) -> WorldState:
        """A deep copy of ``world`` without forbidden motifs, and with NPCs that
        neither ``represent`` nor are described by a forbidden theme."""
        snap = world.model_copy(deep=True)
        if not forbidden:
            return snap
        snap.motifs = [m for m in snap.motifs if not self._bad(m, forbidden)]
        for npc in snap.npcs:
            if self._bad(npc.represents, forbidden):
                npc.represents = None
            if self._bad(npc.persona, forbidden):
                head = npc.persona.split(";")[0].strip()
                npc.persona = "" if self._bad(head, forbidden) else head
        return snap

    # ------------------------------------------------------------------
    # One heartbeat, in two halves
    # ------------------------------------------------------------------
    def step(self) -> CycleRecord:
        """One full cycle: dream the scene, let the driver act, resolve it.

        Exactly ``begin_cycle()`` + ``driver.act(...)`` + ``complete_cycle(...)``;
        a turn-based front end (the web runtime) calls the two halves itself
        and supplies the person's action in between."""
        pending = self.begin_cycle()
        action = self.driver.act(pending.scene, pending.view)
        return self.complete_cycle(action)

    @property
    def pending(self) -> Optional["PendingCycle"]:
        """The cycle waiting for an action (``None`` between cycles)."""
        return self._pending

    def begin_cycle(self) -> "PendingCycle":
        """First half of a cycle: everything up to and including the final,
        safety-gated scene (snapshot, safe view, world, synchronicity, recall,
        request, generation, entropy, harmonizer, alignment gate, block
        handling). Returns the scene the protagonist now faces.

        Idempotent while a cycle is pending: calling it again returns the same
        :class:`PendingCycle` without dreaming anything new."""
        if self._pending is not None:
            return self._pending
        ident = self.identity.snapshot()
        forbidden = self.forbidden()
        self._arm_forbidden(forbidden)
        # Everything that dreams or reads the story sees the safe view.
        view = self._safe_view(ident, forbidden)

        # The reflectiveness dial rises with convergence (inner-outer polarity
        # closing); alignment can nudge it further.
        reflectiveness = ident.convergence
        if any(c.startswith("raise the reflectiveness dial") for c in self._next_constraints):
            reflectiveness = min(1.0, reflectiveness + 0.15)
        # The outer journey advances with convergence, then the world is shaped
        # to mirror the self, scaled by the reflectiveness dial.
        stage = self.arc.stage_hint(ident.convergence)
        try:
            self.environment.advance_place(ident.convergence, stage=stage)
        except TypeError:
            self.environment.advance_place(ident.convergence)
        world = self.environment.reflect(view, reflectiveness)
        # The arc proposes the stage this cycle should realise; the cast is
        # populated for it (mentor returns at the ordeal, shadow must be faced...).
        try:
            world = self.npc.populate(world, view, stage=stage)
        except TypeError:  # an NPC layer without stage-aware callbacks
            world = self.npc.populate(world, view)

        # Synchronicity: a meaningful coincidence to weave in, maybe.
        hint = self.synchronicity.consider(view, world)
        if hint is not None and forbidden and any(
                self._bad(x, forbidden) for x in (hint.symbol, hint.trigger, hint.suggestion)):
            hint = None

        # Recall past + future-memory shards for continuity and backcasting.
        recalled = self.identity.memory.recall(
            " ".join(view.active_themes) or "journey", top_k=3
        )
        recalled += self.identity.memory.recall_future(top_k=2)
        recalled = [r for r in dict.fromkeys(recalled) if not self._bad(r, forbidden)]

        # Self-remembering: the journal's meta-analysis steers the story — a
        # meaningful symbol that has gone quiet is brought back (the blueprint's
        # fox callback), and the most-avoided theme becomes the grand test.
        constraints = list(self._next_constraints)
        for sym in getattr(view, "callback_symbols", [])[:1]:
            constraints.append(f"bring back {sym} as a callback")
        # The forbidden list travels with the request too, so any Generative
        # Engine (not only ExperienceEngine.forbidden) can honour it.
        constraints += [f"{NEVER_DEPICT} {f}" for f in forbidden]
        constraints = list(dict.fromkeys(constraints))
        target_theme = self._next_theme
        if target_theme is not None and self._bad(target_theme, forbidden):
            target_theme = None
        journal = getattr(self.identity, "journal", None)
        focus = journal.suggested_focus() if journal is not None else None
        if target_theme is None and focus and focus in view.active_themes \
                and ident.tick % 3 == 2:
            target_theme = focus

        request = GenerationRequest(
            identity=view,
            # Snapshot the world: the live WorldState keeps mutating, and a record
            # must show the world as it was when this scene was dreamed.
            world=self._safe_world(world, forbidden),
            directive=self._next_directive,
            target_theme=target_theme,
            target_intensity=self._next_intensity,
            synchronicity=hint,
            recalled_shards=recalled,
            constraints=constraints,
            stage=stage,
        )

        # Dream the scene, add NPC entropy/callbacks, harmonize, then the
        # Self-Alignment gate runs LAST on the finished scene. A blocked or
        # vetoed scene is replaced (see _handle_block).
        scene, verdict = self._compose(request, world, view)
        report = self.alignment.evaluate(view, request.world, scene)
        if report.blocked or not verdict.approved:
            self._rejected = (scene, verdict)
            scene, report, verdict = self._handle_block(request, view, world, report)
            self._rejected = None
        if verdict.corrections:
            self._pending_corrections = list(dict.fromkeys(
                self._pending_corrections + list(verdict.corrections)))

        self._pending = PendingCycle(
            ident=ident, view=view, forbidden=forbidden, stage=stage, world=world,
            request=request, scene=scene, report=report, verdict=verdict)
        return self._pending

    def complete_cycle(self, action: UserAction) -> CycleRecord:
        """Second half of a cycle: resolve the pending scene with ``action``
        (NPC reactions, interpretation, convergence, identity update,
        carry-forward, record, arc, close check, observers).

        Raises ``RuntimeError`` if no cycle is pending."""
        p = self._pending
        if p is None:
            raise RuntimeError("complete_cycle() called with no pending cycle; "
                               "call begin_cycle() first")
        ident, view, forbidden, stage, world = p.ident, p.view, p.forbidden, p.stage, p.world
        request, scene, report, verdict = p.request, p.scene, p.report, p.verdict

        # The protagonist has acted; NPCs react.
        npc_events = self.npc.react(scene, action, world)

        # Interpret the outcome and run the convergence dynamics.
        outcome = self.interpreter.interpret(scene, action, world, view)
        outcome.npc_events = npc_events
        outcome = self._filter_outcome(outcome, scene, forbidden)
        # Novelty: only a real NPC-layer entropy shock adds surprise — never the
        # generator's own closing stage event.
        novelty = min(1.0, scene.intensity + (0.3 if has_entropy_shock(scene) else 0.0))
        # Convergence is EARNED: the alignment layer turns this cycle's reflection,
        # engagement, integration and shocks into a signal that couples into the
        # pi-phi dynamics (falls back to the time-only step for plain modules).
        signal_for = getattr(self.alignment, "signal_for", None)
        if signal_for is not None and hasattr(self.convergence, "step_with"):
            conv = self.convergence.step_with(signal_for(view, world, scene, outcome), novelty)
        else:
            conv = self.convergence.step(novelty)

        # Feed back into the self and advance the world.
        try:
            self.identity.apply_outcome(outcome, scene_intensity=scene.intensity)
        except TypeError:  # an Identity Core without the intensity kwarg
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
        # The cycle is resolved from here on, even if an observer raises.
        self._pending = None
        self.history.append(record)
        self.arc.record(record, stage=scene.stage or stage)
        if self.arc.should_close(conv.convergence, self.goal_progress()):
            self.closed = True
        for obs in self.observers:
            obs.on_cycle(record)
        return record

    def abandon_cycle(self) -> None:
        """Drop the pending cycle without resolving it (the journey is being
        ended early). Its NPC callbacks are un-marked and the harmonizer
        forgets the scene, as for any composed scene that is never played.
        No-op when nothing is pending."""
        p = self._pending
        if p is None:
            return
        self._pending = None
        self._discard(p.scene, requeue=False)

    # ------------------------------------------------------------------
    def _compose(self, request: GenerationRequest, world: WorldState,
                 view: IdentitySnapshot) -> tuple[Scene, HarmonizerVerdict]:
        """generate -> NPC entropy + callbacks -> harmonizer review/apply."""
        scene = self.generative.generate(request)
        scene = self.npc.inject_entropy(scene, world)
        # World-Truth Harmonizer: continuity + reality anchoring.
        verdict = self.harmonizer.review(scene, world, view)
        scene = self._apply_harmonizer(scene, verdict)
        return scene, verdict

    def _discard(self, scene: Scene, *, requeue: bool) -> None:
        """A composed scene will not be played: hand its NPC callbacks back
        (re-queued for the replacement, or un-marked) and make the harmonizer
        forget it."""
        withdraw = getattr(self.npc, "withdraw", None)
        if withdraw is not None:
            withdraw(scene, requeue=requeue)
        retract = getattr(self.harmonizer, "retract", None)
        if retract is not None:
            retract()

    def _apply_harmonizer(self, scene: Scene, verdict) -> Scene:
        if verdict.is_vision:
            scene.is_vision = True
        if verdict.grounding_note and not self._bad(verdict.grounding_note, self.forbidden()):
            scene.beats.append(SceneBeat(kind="dialogue", speaker="a grounding voice",
                                         text=verdict.grounding_note))
        # Continuity corrections are carried to the next request by step()
        # (via _pending_corrections, for the scene actually played) — they are
        # no longer written straight into _next_constraints, where
        # _carry_forward used to overwrite them.
        return scene

    def _handle_block(self, request, ident, world, report):
        """Replace a scene the safety gate blocked or the harmonizer vetoed.

        The rejected scene and verdict are in ``self._rejected``. Its NPC
        callbacks are re-queued and its harmonizer review retracted; the scene
        is regenerated once — gentler if it was a safety block, with the
        harmonizer's corrections as constraints if it was a veto — and the
        replacement goes through the full pipeline again (entropy/callbacks,
        harmonizer, alignment gate). If that still fails, a safe interlude is
        played (no entropy, no callbacks: those are un-marked so a later cycle
        can deliver them).

        Record contract — the record shows the scene that was PLAYED, plus the
        fact that it is a replacement:

        * safety block: the final ``AlignmentReport`` has ``blocked=True`` and
          ``block_reason="replaced: <original reason>"`` (plus "; replacement
          also blocked: ..." if the interlude was needed). ``blocked=True`` on a
          played scene therefore means "the original was blocked and this is
          its replacement" — the played scene itself passed (or is the
          interlude).
        * harmonizer veto: the final ``HarmonizerVerdict`` lists the original's
          errors as ``continuity_errors`` entries prefixed ``"replaced (vetoed):
          "`` and keeps its corrections; ``approved`` is the verdict on the
          played scene.

        Returns ``(scene, report, verdict)``.
        """
        rejected = getattr(self, "_rejected", None)
        orig_scene, orig_verdict = rejected if rejected else (None, None)
        vetoed = orig_verdict is not None and not orig_verdict.approved
        blocked = bool(report.blocked)
        if orig_scene is not None:
            self._discard(orig_scene, requeue=True)
        retract = getattr(self.alignment, "retract", None)

        extra: list[str] = []
        update: dict = {}
        if blocked:
            extra += [report.block_reason or "avoid the blocked content",
                      "keep gentle", "provide hope"]
            update.update(
                directive="offer a gentle, safe interlude that still honors the theme",
                target_intensity=min(request.target_intensity, 0.25))
        if orig_verdict is not None:
            extra += list(orig_verdict.corrections)
        update["constraints"] = list(dict.fromkeys(list(request.constraints) + extra))
        safe_req = request.model_copy(update=update)

        if retract is not None:
            retract()
        scene, verdict = self._compose(safe_req, world, ident)
        new_report = self.alignment.evaluate(ident, safe_req.world, scene)
        reasons = []
        if new_report.blocked or not verdict.approved:
            reasons.append(new_report.block_reason if new_report.blocked
                           else "replacement vetoed by the harmonizer")
            self._discard(scene, requeue=False)
            # Callbacks still queued would never reach a scene now.
            drop = getattr(self.npc, "drop_pending", None)
            if drop is not None:
                drop()
            scene = _safe_interlude(request, self.forbidden())
            verdict = self.harmonizer.review(scene, world, ident)
            scene = self._apply_harmonizer(scene, verdict)
            if retract is not None:
                retract()
            new_report = self.alignment.evaluate(ident, safe_req.world, scene)

        if blocked:
            why = f"replaced: {report.block_reason or 'blocked'}"
            if reasons:
                why += f"; replacement also failed: {reasons[0]}"
            new_report = new_report.model_copy(update={"blocked": True, "block_reason": why})
        elif new_report.blocked:
            # Only reachable if the interlude itself trips the gate (it should
            # not): say so honestly rather than hide it.
            new_report = new_report.model_copy(update={
                "block_reason": f"interlude played despite: {new_report.block_reason}"})
        if vetoed:
            errs = list(orig_verdict.continuity_errors) or ["scene vetoed"]
            verdict = verdict.model_copy(update={
                "continuity_errors": [f"replaced (vetoed): {e}" for e in errs]
                + list(verdict.continuity_errors),
                "corrections": list(dict.fromkeys(
                    list(orig_verdict.corrections) + list(verdict.corrections))),
            })
        return scene, new_report, verdict

    def _filter_outcome(self, outcome: Outcome, scene: Scene,
                        forbidden: list[str]) -> Outcome:
        """Keep what must not be learned out of the identity: the interlude's
        resting theme (and its ``<theme>_confidence`` trait nudge), and any
        forbidden theme an interpreter reports as touched."""
        drop = set()
        if is_interlude(scene):
            drop.add((scene.theme or "").lower())
        themes = [t for t in outcome.themes_touched
                  if t and t.lower() not in drop and not self._bad(t, forbidden)]
        traits = {k: v for k, v in outcome.trait_updates.items()
                  if not any(k.lower() == f"{d}_confidence" for d in drop if d)}
        if themes != outcome.themes_touched or traits != outcome.trait_updates:
            outcome = outcome.model_copy(update={"themes_touched": themes,
                                                 "trait_updates": traits})
        return outcome

    def _carry_forward(self, report, outcome) -> None:
        self._next_intensity = report.recommended_intensity
        # Alignment nudges plus any harmonizer corrections still owed (these
        # used to be overwritten here). Corrections are delivered once.
        self._next_constraints = list(dict.fromkeys(
            list(report.nudges) + self._pending_corrections))
        self._pending_corrections = []
        # Leave the next theme open so the Generative Engine rotates across the
        # identity's active themes — this keeps the journey from fixating on one
        # theme and lets every active theme eventually be reflected and worked
        # through. Alignment still steers tone/intensity via nudges + directive.
        self._next_theme = None
        if report.nudges:
            self._next_directive = report.nudges[0]
        else:
            self._next_directive = "deepen the journey"


_INTERLUDE_ALT_TITLE = "A quiet clearing"


def is_interlude(scene: Scene) -> bool:
    """Is this the loop's safe interlude?"""
    return (scene.title in (INTERLUDE_TITLE, _INTERLUDE_ALT_TITLE)
            and scene.stage is None and scene.intensity <= 0.1)


def _safe_interlude(request, forbidden: Optional[list[str]] = None) -> Scene:
    """A guaranteed-safe resting scene used if regeneration still isn't safe.

    Marked by ``INTERLUDE_TITLE`` (see :func:`is_interlude`); its theme is
    ``INTERLUDE_THEME`` and is filtered out of the outcome so "rest" is never
    learned into the identity. Lines that would name a forbidden theme are
    swapped for alternatives.
    """
    forbidden = forbidden or []
    ok = lambda t: mentions_forbidden(t, forbidden) is None  # noqa: E731

    def pick(*options: str) -> str:
        return next((o for o in options if ok(o)), "...")

    theme = INTERLUDE_THEME if ok(INTERLUDE_THEME) else "stillness"
    choices = [c for c in ("Rest and breathe", "Reflect quietly", "Move on when ready",
                           "Sit for a while", "Breathe")
               if ok(c)][:3] or ["Wait"]
    return Scene(
        title=INTERLUDE_TITLE if ok(INTERLUDE_TITLE) else _INTERLUDE_ALT_TITLE,
        summary=pick("The world softens into a quiet, safe clearing where nothing is "
                     "asked of you.", "The world grows quiet. Nothing is asked of you."),
        beats=[
            SceneBeat(kind="description",
                      text=pick("Warm light, still air. A bench, a stream, time to breathe.",
                                "Still air. A bench, a stream.", "Quiet.")),
            SceneBeat(kind="dialogue", speaker="a kind presence",
                      text=pick("You are safe here. Rest as long as you need.",
                                "Nothing is needed of you here.", "Take your time.")),
        ],
        theme=theme,
        intensity=0.1,
        choices=choices,
        source="deterministic",
    )
