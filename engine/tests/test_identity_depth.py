"""Hermetic tests for Identity Core depth: meaning-aware goals, the journal's
meta-analysis, recurring symbols, and memories of the future being fulfilled."""
from __future__ import annotations

import pytest

from ana.contracts import EmotionalState, Goal, IdentitySnapshot, Outcome
from ana.identity.core import IdentityCore, derive_goal_themes, first_person_past
from ana.identity.journal import Journal
from ana.identity.memory import ShardMemory


@pytest.fixture
def ic():
    core = IdentityCore(memory=ShardMemory(db_path=None))
    yield core
    core.close()


def _out(theme, success, *, lesson=None, symbols=(), stress=0.0, trait=None):
    return Outcome(
        scene_id="s", summary=f"a {theme} scene", themes_touched=[theme],
        success=success, lesson=lesson, symbols=list(symbols),
        trait_updates=trait or {},
        emotion_delta=EmotionalState(valence=0.0, arousal=0.0, stress=stress),
    )


def _cycle(ic, outcome, intensity=0.5):
    ic.apply_outcome(outcome, scene_intensity=intensity)
    ic.advance(0.1)


# ── A. meaning-aware goal progress ──────────────────────────────────
def test_goal_themes_derived_from_lexicon_and_content_words():
    assert derive_goal_themes("become someone who can lead")[0] == "leadership"
    assert "purpose" in derive_goal_themes("find my purpose")
    assert "courage" in derive_goal_themes("stop being afraid of the dark")
    assert "trust" in derive_goal_themes("learn to rely on others")
    assert "home" in derive_goal_themes("a place where I belong")


def test_lead_goal_advances_on_leadership_successes(ic):
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    goal = ic.goals[0]
    assert "leadership" in goal.themes
    for _ in range(3):
        _cycle(ic, _out("leadership", True))
    assert goal.progress > 0.3
    assert goal.advanced_at == [0, 1, 2]


def test_success_beats_neutral_and_intensity_and_lesson_matter():
    def progress(outcome, intensity):
        c = IdentityCore(memory=ShardMemory(db_path=None))
        c.add_goal("become someone who can lead")
        c.apply_outcome(outcome, scene_intensity=intensity)
        p = c.goals[0].progress
        c.close()
        return p

    met = progress(_out("leadership", True), 0.5)
    neutral = progress(_out("leadership", None), 0.5)
    assert met > neutral > 0.0
    assert progress(_out("leadership", True), 0.9) > progress(_out("leadership", True), 0.1)
    assert progress(_out("leadership", True, lesson="I can lead."), 0.5) > met


def test_unrelated_theme_does_not_advance(ic):
    ic.add_goal("become someone who can lead")
    _cycle(ic, _out("grief", True))
    assert ic.goals[0].progress == 0.0


def test_avoidance_does_not_advance_and_regresses_near_goals(ic):
    ic.add_goal("become someone who can lead", horizon="mid")
    ic.add_goal("take charge of the expedition", horizon="near")
    near = ic.goals[1]
    near.progress = 0.3
    for _ in range(3):
        _cycle(ic, _out("leadership", False))
    assert ic.goals[0].progress == 0.0
    assert ic.goals[0].advanced_at == []
    assert near.progress < 0.3


def test_plain_goal_object_gets_themes_lazily(ic):
    ic.goals.append(Goal(description="become a leader"))
    _cycle(ic, _out("leadership", True))
    assert ic.goals[0].progress > 0.0
    assert "leadership" in ic.goals[0].themes


def test_intensity_inferred_from_arousal_when_not_given(ic):
    ic.add_goal("become someone who can lead")
    o = _out("leadership", True)
    o.emotion_delta = EmotionalState(valence=0.2, arousal=0.4, stress=-0.1)  # ~intensity 1.0
    ic.apply_outcome(o)
    hi = ic.goals[0].progress
    ic.goals[0].progress = 0.0
    o.emotion_delta = EmotionalState(valence=0.2, arousal=0.1, stress=-0.1)  # ~intensity 0
    ic.apply_outcome(o)
    assert hi > ic.goals[0].progress > 0.0


# ── B. journal & meta-analysis ──────────────────────────────────────
def test_journal_meta_analysis_trends(ic):
    seq = [("leadership", True), ("trust", False), ("leadership", True),
           ("trust", False), ("leadership", None), ("leadership", True),
           ("trust", False)]
    for theme, s in seq:
        _cycle(ic, _out(theme, s, symbols=["lighthouse"]))
    rec = ic.journal.theme_record()
    assert rec["leadership"]["met"] == 3 and rec["leadership"]["neutral"] == 1
    assert rec["trust"]["avoided"] == 3 and rec["trust"]["last_tick"] == 6
    stmts = ic.journal.meta_analysis()
    assert "growing in leadership (3 of 4 encounters met)" in stmts
    assert "repeatedly turning from trust (0 of 3 met)" in stmts
    assert "the lighthouse keeps returning (7 appearances)" in stmts
    assert any("improving on leadership but still struggling with trust" in s for s in stmts)
    assert ic.journal.suggested_focus() == "trust"


def test_stress_climbing_statement():
    j = Journal()
    for i, s in enumerate([0.2, 0.3, 0.4, 0.5]):
        j.record(tick=i, themes=["courage"], stress=s)
    assert "stress has been climbing for 3 cycles" in j.meta_analysis()


def test_meta_analysis_is_deterministic_and_serialisable(ic):
    for theme, s in [("courage", True), ("courage", True), ("trust", False)]:
        _cycle(ic, _out(theme, s))
    assert ic.journal.meta_analysis() == ic.journal.meta_analysis()
    d = ic.journal.to_dict()
    assert len(d["entries"]) == 3 and "courage" in d["theme_record"]
    assert Journal.from_dict(d).meta_analysis() == ic.journal.meta_analysis()


# ── recurring symbols & callbacks ───────────────────────────────────
def test_recurring_symbols_and_callbacks(ic):
    _cycle(ic, _out("trust", True, symbols=["Fox", "lantern"], lesson="Trust survives."))
    _cycle(ic, _out("trust", True, symbols=["fox"]))
    for _ in range(4):
        _cycle(ic, _out("leadership", None, symbols=["lighthouse"]))
    rec = ic.journal.recurring_symbols(min_count=2)
    assert rec[0] == ("lighthouse", 4)
    assert ("fox", 2) in rec
    assert "lantern" not in dict(rec)
    # The fox meant something and has been absent — a callback candidate.
    assert "fox" in ic.journal.callback_symbols()
    assert "lighthouse" not in ic.journal.callback_symbols()


# ── D. memories of the future ───────────────────────────────────────
def test_future_memory_is_strengthened_then_fulfilled(ic):
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.add_goal("find my purpose", horizon="life", is_future_memory=True)
    goal = ic.goals[0]
    # A strengthened vision surfaces first among future memories.
    _cycle(ic, _out("leadership", True))
    assert ic.memory.recall_future(top_k=1) == ["become someone who can lead"]
    assert ic.fulfilled_visions() == []
    for _ in range(6):
        _cycle(ic, _out("leadership", True, lesson="I can take charge."))
        if goal.realised:
            break
    assert goal.realised and goal.progress >= 0.6
    assert goal.realised_at is not None
    assert ic.fulfilled_visions() == ["I became someone who can lead."]
    assert "I became someone who can lead." in ic.memory.fulfilled()
    # the fulfilment is a Prime lesson that recall surfaces
    assert "I became someone who can lead." in ic.memory.recall_lessons("leadership lead", top_k=3)
    # the other vision is untouched
    assert not ic.goals[1].realised


def test_first_person_past():
    assert first_person_past("become someone who can lead") == "I became someone who can lead."
    assert first_person_past("find my purpose") == "I found my purpose."
    assert first_person_past("I will heal") == "I healed."


# ── C. snapshot self-remembering ────────────────────────────────────
def test_snapshot_fields_populated(ic):
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.active_themes = ["leadership", "trust"]
    for theme, s in [("leadership", True), ("trust", False), ("leadership", True),
                     ("trust", False), ("leadership", True), ("leadership", True),
                     ("leadership", True)]:
        _cycle(ic, _out(theme, s, symbols=["lighthouse"],
                        lesson="I can take charge when no one else will." if s else None))
    snap = ic.snapshot()
    assert isinstance(snap, IdentitySnapshot)
    assert snap.insights and any("leadership" in s for s in snap.insights)
    assert len(snap.insights) <= 4
    assert "lighthouse" in snap.recurring_symbols
    assert snap.fulfilled_visions == ["I became someone who can lead."]
    assert "I can take charge when no one else will." in snap.recent_shard_summaries
    assert snap.goals[0].realised and snap.goals[0].advanced_at
    # the snapshot is a copy: mutating it does not touch the core
    snap.goals[0].progress = 0.0
    assert ic.goals[0].progress > 0.0
    # round-trips through JSON
    assert IdentitySnapshot.model_validate_json(snap.model_dump_json()).insights == snap.insights


def test_snapshot_defaults_on_fresh_core(ic):
    snap = ic.snapshot()
    assert snap.insights == [] and snap.recurring_symbols == []
    assert snap.fulfilled_visions == [] and snap.callback_symbols == []
