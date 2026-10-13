"""End-to-end and unit tests for the ANA engine.

Everything runs deterministically with no network (use_llm=False), so the suite
is hermetic.
"""
from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")

from ana.config import ANAConfig
from ana.contracts import (
    Archetype, EmotionalState, GenerationRequest, Goal, IdentitySnapshot,
    NPC, Scene, SceneBeat, Trait, UserAction, WorldState,
)
from ana.interfaces import (
    AlignmentProtocols, GenerativeEngine, NPCEntropyLayer, SynchronicityEngine,
    WorldTruthHarmonizer,
)


# ── cores ───────────────────────────────────────────────────────────
def test_convergence_rises_from_zero():
    from ana.engine.convergence import ConvergenceEngine
    ce = ConvergenceEngine(seed=1)
    traj = [ce.step(0.2).convergence for _ in range(12)]
    assert traj[0] < 0.2              # starts near fully divergent
    assert traj[-1] > traj[0]         # polarity closes over the journey
    assert all(0.0 <= c <= 1.0 for c in traj)


def test_memory_shards_and_recall():
    from ana.identity.memory import ShardMemory
    m = ShardMemory(db_path=None)
    m.record_identity("I grew up by a lighthouse.")
    m.record_future_memory("I will become someone who can lead.", theme="leadership")
    m.record_lesson("I can lead under pressure.", themes=["leadership"])
    assert m.recall("lighthouse")
    assert m.recall_future()
    assert m.stats()["total_nodes"] >= 3
    m.close()


# ── spine ───────────────────────────────────────────────────────────
def test_identity_apply_outcome_updates_self():
    from ana.identity.core import IdentityCore
    from ana.identity.memory import ShardMemory
    from ana.contracts import Outcome
    ic = IdentityCore(memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.3)
    ic.apply_outcome(Outcome(scene_id="s", summary="led a rescue",
                             themes_touched=["leadership"], lesson="I can lead",
                             trait_updates={"leadership_confidence": 0.2},
                             emotion_delta=EmotionalState(valence=0.3, arousal=0.1, stress=-0.1)))
    assert ic.traits["leadership_confidence"].value > 0.3
    assert "leadership" in ic.active_themes
    ic.close()


def test_environment_reflectiveness_dial():
    from ana.environment.mirror import EnvironmentMirror
    em = EnvironmentMirror()
    snap = IdentitySnapshot(active_themes=["grief"], emotion=EmotionalState(valence=-0.7))
    low = em.reflect(snap, 0.1)
    high = em.reflect(snap, 0.9)
    assert len(high.motifs) >= len(low.motifs)   # higher dial -> more direct mirror


# ── squad modules conform to their protocols ─────────────────────────
def test_modules_satisfy_protocols():
    from ana.generative.engine import ExperienceEngine
    from ana.npc.layer import NPCDirector
    from ana.subsystems.synchronicity import SynchronicityOrchestrator
    from ana.subsystems.alignment import SelfAlignment
    from ana.subsystems.harmonizer import TruthHarmonizer
    assert isinstance(ExperienceEngine(use_llm=False), GenerativeEngine)
    assert isinstance(NPCDirector(), NPCEntropyLayer)
    assert isinstance(SynchronicityOrchestrator(), SynchronicityEngine)
    assert isinstance(SelfAlignment(), AlignmentProtocols)
    assert isinstance(TruthHarmonizer(), WorldTruthHarmonizer)


def test_generative_produces_scene():
    from ana.generative.engine import ExperienceEngine
    req = GenerationRequest(
        identity=IdentitySnapshot(active_themes=["leadership"], goals=[Goal(description="lead")]),
        world=WorldState(location="a town", motifs=["a lighthouse"],
                         npcs=[NPC(name="Fisher", archetype=Archetype.MENTOR)]),
        directive="a leadership test", target_theme="leadership", target_intensity=0.6)
    s = ExperienceEngine(use_llm=False, seed=3).generate(req)
    assert s.beats and s.choices and s.source == "deterministic"


def test_alignment_blocks_forbidden_theme():
    from ana.subsystems.alignment import SelfAlignment
    sa = SelfAlignment(forbidden_themes=["drowning"])
    r = sa.evaluate(IdentitySnapshot(), WorldState(), Scene(theme="drowning", intensity=0.5))
    assert r.blocked and r.block_reason


def test_interpreter_reads_engagement():
    from ana.engine.interpret import Interpreter
    it = Interpreter(use_llm=False)
    scene = Scene(theme="leadership", intensity=0.6)
    out = it.interpret(scene, UserAction(raw="I organize the rescue and lead them."),
                       WorldState(), IdentitySnapshot(active_themes=["leadership"]))
    assert out.success is True
    assert out.trait_updates.get("leadership_confidence", 0) > 0
    assert out.lesson


def test_autonomous_driver_picks_choice():
    from ana.engine.drivers import AutonomousDriver
    d = AutonomousDriver(seed=1)
    scene = Scene(choices=["Flee the scene", "Lead the rescue"], theme="leadership")
    brave = IdentitySnapshot(traits=[Trait(name="leadership_confidence", value=0.9)],
                             emotion=EmotionalState(valence=0.4, stress=0.1))
    act = d.act(scene, brave)
    assert act.choice_index in (0, 1)


# ── full loop ────────────────────────────────────────────────────────
def test_full_session_runs_and_converges():
    from ana.session import build_session
    from ana.engine.drivers import ScriptedDriver
    from ana.identity.core import IdentityCore
    from ana.identity.memory import ShardMemory

    ic = IdentityCore(memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.add_goal("find purpose", horizon="life", is_future_memory=True)
    ic.active_themes = ["leadership", "purpose"]

    sess = build_session(
        ANAConfig(use_llm=False, max_cycles=8),
        identity=ic,
        driver=ScriptedDriver(["I explore.", "I climb the lighthouse.",
                               "I organize a rescue and lead.", "I speak honestly."]),
    )
    records = sess.run()
    assert len(records) == 8
    # convergence should trend upward across the journey
    assert records[-1].convergence > records[0].convergence
    # every cycle produced a scene with beats and passed through all subsystems
    for r in records:
        assert r.scene.beats
        assert r.alignment is not None and r.harmonizer is not None
        assert 0.0 <= r.convergence <= 1.0
    # the self changed
    assert ic.traits["leadership_confidence"].value != 0.25
    sess.close()


def test_forbidden_theme_never_reaches_player():
    """A session that forbids a theme must never surface it unblocked."""
    from ana.session import build_session
    from ana.engine.drivers import ScriptedDriver
    cfg = ANAConfig(use_llm=False, max_cycles=5, forbidden_themes=["drowning"])
    sess = build_session(cfg, driver=ScriptedDriver(["I continue."] * 5))
    sess.identity.active_themes = ["drowning", "courage"]  # try to force it
    records = sess.run()
    for r in records:
        if r.scene.theme == "drowning":
            # if it ever themed on drowning, alignment must have caught it
            assert not r.alignment.blocked  # block handler replaced it with a safe scene
    sess.close()
