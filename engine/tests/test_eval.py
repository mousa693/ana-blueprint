"""Tests for the ana.eval yardstick: metric functions on hand-built records,
plus a small end-to-end smoke run."""
from __future__ import annotations

import math

import pytest

from ana.contracts import (
    AlignmentReport, CycleRecord, EmotionalState, GenerationRequest, Goal,
    HarmonizerVerdict, IdentitySnapshot, Outcome, Scene, SceneBeat, Trait,
    UserAction, WorldState,
)
from ana.eval import metrics as M
from ana.eval.harness import (
    StanceDriver, compare, engagement_score, evaluate, report_markdown, run_journey,
)

STAGE_TITLES = {
    "call": "The Call To Adventure", "threshold": "Crossing The Threshold",
    "mentor": "Meeting The Mentor", "ordeal": "At The Ordeal",
    "reward": "Seizing The Reward", "return": "The Return",
}


def rec(tick, conv, *, success=None, stage=None, theme="leadership", beats=None,
        nudges=None, lesson=None, traits=None, trait_updates=None, stress=0.2,
        intensity=0.4, symbols=None, blocked=False, vision=False,
        location="a town", themes=("leadership", "purpose"), conv_before=None,
        goals=None):
    ident = IdentitySnapshot(
        tick=tick,
        traits=[Trait(name=k, value=v) for k, v in (traits or {"courage": 0.4}).items()],
        goals=[Goal(description=f"g{i}", progress=p) for i, p in enumerate(goals or [])],
        emotion=EmotionalState(stress=stress),
        active_themes=list(themes),
        convergence=conv_before if conv_before is not None else 0.0,
    )
    world = WorldState(location=location)
    scene = Scene(
        title=STAGE_TITLES.get(stage, f"Scene {tick}"),
        summary=f"summary {tick}",
        beats=[SceneBeat(kind="description", text=t) for t in (beats or [f"beat {tick}"])],
        theme=theme, intensity=intensity, symbols=list(symbols or []), is_vision=vision,
    )
    return CycleRecord(
        tick=tick, identity_before=ident,
        request=GenerationRequest(identity=ident, world=world),
        scene=scene,
        alignment=AlignmentReport(nudges=list(nudges or []), blocked=blocked),
        harmonizer=HarmonizerVerdict(),
        outcome=Outcome(scene_id=scene.id, success=success, lesson=lesson,
                        trait_updates=dict(trait_updates or {}),
                        user_action=UserAction(raw="x")),
        convergence=conv,
    )


def test_convergence_metrics_dips_and_recovery():
    s = [0.1, 0.2, 0.15, 0.25, 0.3, 0.2, 0.19, 0.18, 0.17]
    recs = [rec(i, c) for i, c in enumerate(s)]
    m = M.convergence_metrics(recs)
    assert m["conv_final"] == pytest.approx(0.17)
    assert m["conv_max"] == pytest.approx(0.3)
    assert m["conv_auc"] == pytest.approx(sum(s) / len(s))
    assert m["conv_dips"] == 2               # 0.2->0.15 and 0.3->0.2 (one run)
    assert m["conv_dips_recovered"] == 1     # first regains 0.2 next cycle
    assert m["conv_recovery_rate"] == pytest.approx(0.5)
    # 8 steps, 5 negative
    assert m["conv_monotonicity"] == pytest.approx(3 / 8)


def test_convergence_metrics_empty_and_monotone():
    assert M.convergence_metrics([])["conv_final"] == 0.0
    m = M.convergence_metrics([rec(i, 0.1 * i) for i in range(5)])
    assert m["conv_monotonicity"] == 1.0 and m["conv_dips"] == 0
    assert m["conv_recovery_rate"] == 1.0


def test_earnedness_positive_and_time_driven():
    succ = [True, False, True, False, True, None]
    conv, c = [], 0.0
    for s in succ:
        c += 0.1 if s is True else (-0.05 if s is False else 0.0)
        conv.append(c)
    earned = [rec(i, cv, success=s) for i, (cv, s) in enumerate(zip(conv, succ))]
    e = M.earnedness_metrics(earned)
    assert e["earned_corr"] > 0.9
    assert e["earned_gap"] == pytest.approx(0.15)
    # time-driven: constant increment regardless of success -> corr 0
    timed = [rec(i, 0.05 * (i + 1), success=s) for i, s in enumerate(succ)]
    assert abs(M.earnedness_metrics(timed)["earned_corr"]) < 1e-9


def test_pearson_degenerate():
    assert M.pearson([1, 1, 1], [0.1, 0.2, 0.3]) == 0.0
    assert M.pearson([1], [2]) == 0.0
    assert M.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)


def test_infer_stage_labels():
    assert M.infer_stage(Scene(title="Leadership and a bell",
                               summary="At an unmarked threshold, the protagonist meets "
                                       "the call to adventure")) == "call"
    assert M.infer_stage(Scene(title="At Crossing The Threshold")) == "threshold"
    assert M.infer_stage(Scene(title="x", summary="meets the ordeal")) == "ordeal"
    assert M.infer_stage(Scene(title="Seizing The Reward")) == "reward"
    assert M.infer_stage(Scene(title="Quiet", summary="an unmarked threshold")) is None


def test_journey_metrics_order_and_backjump():
    stages = ["call", "threshold", "mentor", "threshold", "ordeal", "reward"]
    locs = ["a", "a", "b", "c", "c", "d"]
    recs = [rec(i, 0.1, stage=s, location=l) for i, (s, l) in enumerate(zip(stages, locs))]
    j = M.journey_metrics(recs)
    assert j["journey_distinct_stages"] == 5
    assert j["journey_reached_ordeal"] == 1
    assert j["journey_in_order"] == 0
    assert j["journey_max_backjump"] == 1
    assert j["distinct_locations"] == 4
    assert j["journey_stage_seq"] == stages


def test_variety_metrics():
    recs = [
        rec(0, 0.1, theme="leadership", beats=["a", "b"], nudges=["n1", "n2"]),
        rec(1, 0.1, theme="leadership", beats=["a", "c"], nudges=["n1", "n2"]),
        rec(2, 0.1, theme="trust", beats=["a", "a"], nudges=["n3"]),
    ]
    v = M.variety_metrics(recs)
    assert v["repeated_beat_ratio"] == pytest.approx(3 / 6)
    assert v["consecutive_identical_nudges"] == 1
    assert v["distinct_nudge_ratio"] == pytest.approx(3 / 5)
    assert v["theme_coverage"] == pytest.approx(0.5)   # purpose never a scene theme
    assert v["themes_missed"] == ["purpose"]
    assert v["distinct_scene_ratio"] == 1.0


def test_growth_metrics():
    recs = [
        rec(0, 0.1, traits={"courage": 0.4, "honesty": 0.8}, lesson="L1", goals=[0.0]),
        rec(1, 0.1, traits={"courage": 0.5, "honesty": 0.8}, lesson="L1"),
        rec(2, 0.1, traits={"courage": 0.6, "honesty": 0.7, "trust": 0.2},
            trait_updates={"courage": 0.1}, lesson="L2", goals=[0.3, 0.6]),
    ]
    g = M.growth_metrics(recs)
    assert g["trait_deltas"]["courage"] == pytest.approx(0.3)
    assert g["trait_deltas"]["honesty"] == pytest.approx(-0.1)
    assert g["trait_deltas"]["trust"] == pytest.approx(0.2)
    assert g["goal_progress"] == [0.3, 0.6]
    assert g["goal_progress_mean"] == pytest.approx(0.45)
    assert g["lessons"] == 3
    assert g["distinct_lesson_ratio"] == pytest.approx(2 / 3)


def test_safety_metrics():
    recs = [
        rec(0, 0.1, theme="grief", symbols=["drowning man"]),
        rec(1, 0.1, theme="Abandonment"),
        rec(2, 0.1, stress=0.9, intensity=0.7, blocked=True),
        rec(3, 0.1, stress=0.75, intensity=0.5, vision=True),
    ]
    s = M.safety_metrics(recs, forbidden=["drowning", "abandonment"])
    assert s["forbidden_scenes"] == 2
    assert s["blocked"] == 1
    assert s["max_intensity_when_stressed"] == pytest.approx(0.7)
    assert s["stressed_cycles"] == 2
    assert s["vision_flags"] == 1
    assert M.safety_metrics(recs)["forbidden_scenes"] == 0


def test_metrics_tolerate_missing_fields():
    class Bare:  # a duck-typed record missing nearly everything
        convergence = 0.3
    m = M.compute_metrics([Bare(), Bare()])
    assert m["cycles"] == 2 and m["conv_final"] == 0.3
    assert m["theme_coverage"] == 1.0 and m["forbidden_scenes"] == 0
    assert all(not (isinstance(v, float) and math.isnan(v)) for v in m.values())


def test_stance_driver():
    sc = Scene(choices=["Hesitate at the edge", "Answer the call", "Turn back once more"])
    ident = IdentitySnapshot()
    assert StanceDriver("engaged").act(sc, ident).raw == "Answer the call"
    a = StanceDriver("avoidant").act(sc, ident)
    assert a.choice_index is not None and engagement_score(a.raw) < 0
    # No avoidant option on offer: withdraw in free text, no choice index.
    a2 = StanceDriver("avoidant").act(Scene(choices=["Accept the gift"]), ident)
    assert a2.choice_index is None
    mixed = StanceDriver("mixed", seed=3)
    for _ in range(10):
        mixed.act(sc, ident)
    assert set(mixed.history) == {"engaged", "avoidant"}


def test_smoke_evaluate_two_profiles():
    res = evaluate(["engaged", "avoidant"], seeds=[1], cycles=6)
    assert set(res["profiles"]) == {"engaged", "avoidant"}
    for p in res["profiles"].values():
        assert p["runs"][0]["cycles"] == 6
        assert 0.0 <= p["agg"]["conv_final"]["mean"] <= 1.0
    assert len(res["rubric"]) == 7
    md = report_markdown(res)
    assert "What good looks like" in md and ("PASS" in md or "FAIL" in md)
    table = compare(res, res)
    assert "| engaged | conv_final |" in table and "+0.000" in table


def test_run_journey_lighthouse_and_autonomous():
    for prof in ("lighthouse", "autonomous", "mixed"):
        recs = run_journey(prof, seed=2, cycles=3)
        assert len(recs) == 3
    with pytest.raises(ValueError):
        run_journey("nope", seed=1, cycles=1)
