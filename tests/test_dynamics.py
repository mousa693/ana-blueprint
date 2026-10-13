"""Earned convergence + escalating alignment guidance (hermetic, deterministic)."""
from __future__ import annotations

from ana.contracts import (
    IdentitySnapshot,
    Outcome,
    Scene,
    SceneBeat,
    UserAction,
    WorldState,
)
from ana.engine.convergence import (
    PHASES,
    AlignmentSignal,
    ConvergenceEngine,
    phase_of,
)
from ana.subsystems.alignment import NUDGE_LADDER, SelfAlignment

N = 16


def engaged(t: int) -> AlignmentSignal:
    return AlignmentSignal(reflection=0.6, engagement=1.0, integration=0.8)


def avoidant(t: int) -> AlignmentSignal:
    return AlignmentSignal(reflection=0.3, engagement=-1.0, integration=0.05)


def mixed(t: int) -> AlignmentSignal:
    if t % 2 == 0:
        return AlignmentSignal(reflection=0.45, engagement=1.0, integration=0.5)
    return AlignmentSignal(reflection=0.45, engagement=-1.0, integration=0.05)


def run(policy, seed: int = 7, n: int = N, novelty: float = 0.5) -> list[float]:
    ce = ConvergenceEngine(seed=seed)
    return [ce.step_with(policy(t), novelty).convergence for t in range(n)]


# ── convergence ─────────────────────────────────────────────────────
def test_earned_convergence_orders_engaged_mixed_avoidant():
    for seed in (1, 7, 42):
        e, m, a = run(engaged, seed), run(mixed, seed), run(avoidant, seed)
        for traj in (e, m, a):
            assert all(0.0 <= c <= 1.0 for c in traj)
            assert traj[0] < 0.1                    # starts divergent
        assert 0.6 <= e[-1] <= 0.85, e[-1]
        assert a[-1] < 0.3 and max(a) < 0.3, a
        assert a[-1] < m[-1] < e[-1], (a[-1], m[-1], e[-1])
        assert 0.1 < m[-1] < 0.5, m[-1]
        assert phase_of(e[-1]) in ("integrating", "converged")
        assert phase_of(a[-1]) == "divergent"


def test_avoidance_reverses_earned_convergence():
    ce = ConvergenceEngine(seed=3)
    for t in range(12):
        ce.step_with(engaged(t), 0.4)
    peak = ce.conv
    for t in range(6):
        ce.step_with(avoidant(t), 0.4)
    assert ce.conv < peak - 0.2


def test_shock_dips_then_recovers():
    base = run(engaged, seed=5)
    ce = ConvergenceEngine(seed=5)
    traj = []
    for t in range(N):
        sig = engaged(t)
        if t == 10:
            sig = AlignmentSignal(sig.reflection, sig.engagement, sig.integration,
                                  surprise_external=1.0)
        traj.append(ce.step_with(sig, 0.5).convergence)
    assert traj[10] < traj[9] - 0.05              # visible dip
    assert traj[10] < base[10] - 0.05
    assert traj[-1] > traj[9]                     # recovered past pre-shock level
    assert abs(traj[-1] - base[-1]) < 0.1


def test_step_is_backward_compatible_and_reports_phase():
    a, b = ConvergenceEngine(seed=1), ConvergenceEngine(seed=1)
    ra = [a.step(0.2) for _ in range(12)]
    rb = [b.step(0.2) for _ in range(12)]
    assert [r.convergence for r in ra] == [r.convergence for r in rb]
    assert ra[-1].convergence > ra[0].convergence
    assert all(r.phase == phase_of(r.convergence) for r in ra)


def test_phase_of_thresholds():
    assert [phase_of(x) for x in (0.0, 0.2, 0.4, 0.6, 0.9)] == list(PHASES)
    assert phase_of(1.0) == "converged"


def test_signal_couples_into_pi_phi_surprise():
    calm, torn = ConvergenceEngine(seed=11), ConvergenceEngine(seed=11)
    sc = st = 0.0
    for t in range(12):
        rc = calm.step_with(AlignmentSignal(1.0, 1.0, 0.5), 0.3)
        rt = torn.step_with(AlignmentSignal(0.0, -1.0, 0.0), 0.3)
        sc += rc.omega_surprise + rc.alpha_surprise
        st += rt.omega_surprise + rt.alpha_surprise
    assert st > 1.5 * sc
    assert rt.mutual_prediction < rc.mutual_prediction


# ── alignment ───────────────────────────────────────────────────────
def _ident(themes, tick=0, convergence=0.0):
    return IdentitySnapshot(tick=tick, active_themes=themes, convergence=convergence)


def _scene(theme="weather", symbols=None, beats=None, intensity=0.4, **kw):
    return Scene(theme=theme, symbols=symbols or [], beats=beats or [],
                 intensity=intensity, **kw)


def test_nudges_escalate_and_never_repeat_consecutively():
    sa = SelfAlignment()
    world = WorldState(motifs=["fog"])
    prev, prev_all = None, []
    strategies = []
    for tick in range(10):
        rep = sa.evaluate(_ident(["leadership"], tick), world, _scene())
        theme_nudges = [n for n in rep.nudges if "'leadership'" in n]
        assert len(theme_nudges) == 1
        assert theme_nudges[0] != prev
        prev = theme_nudges[0]
        strategies.append(sa.nudge_log[-1][2])
        # no single nudge string is issued on two consecutive cycles
        assert not set(rep.nudges) & set(prev_all)
        prev_all = rep.nudges
    ladder = [name for name, _ in NUDGE_LADDER]
    assert strategies[:5] == ladder                # climbs the ladder in order
    assert len(set(strategies)) == len(ladder)
    # rung 4 uses the phrase the loop listens for
    assert any(n.startswith("raise the reflectiveness dial") for n in
               [t.format(theme="x") for _, t in NUDGE_LADDER])


def test_nudges_rotate_across_themes_and_stop_once_reflected():
    sa = SelfAlignment()
    world = WorldState()
    themes = ["trust", "courage", "honesty"]
    addressed = []
    for tick in range(3):
        sa.evaluate(_ident(themes, tick), world, _scene())
        addressed.append(sa.nudge_log[-1][1])
    assert sorted(addressed) == sorted(themes)     # each got attention
    # Now the scene works "trust" and "courage": only "honesty" may be nudged.
    beats = [SceneBeat(kind="description", text="An act of Trust under fire."),
             SceneBeat(kind="event", text="Courage, finally.")]
    rep = sa.evaluate(_ident(themes, 3), world, _scene(beats=beats))
    assert not any("'trust'" in n or "'courage'" in n for n in rep.nudges)
    assert any("'honesty'" in n for n in rep.nudges)
    # Fully reflected -> no theme nudges at all.
    beats.append(SceneBeat(kind="dialogue", text="Honesty costs something."))
    rep = sa.evaluate(_ident(themes, 4), world, _scene(beats=beats))
    assert rep.reflection_pct == 1.0
    assert not any("'" + t + "'" in n for n in rep.nudges for t in themes)


def test_reblocked_reevaluation_does_not_advance_ladder():
    sa = SelfAlignment(forbidden_themes=["drowning"])
    world = WorldState()
    sa.evaluate(_ident(["trust"], 0), world, _scene())
    r1 = sa.evaluate(_ident(["trust"], 1), world, _scene(theme="drowning"))
    assert r1.blocked
    r2 = sa.evaluate(_ident(["trust"], 1), world, _scene(theme="calm"))
    assert [n for n in r2.nudges if "'trust'" in n] == [n for n in r1.nudges if "'trust'" in n]
    assert len([x for x in sa.nudge_log if x[1] == "trust"]) == 2


def test_reflection_counts_beat_text_symbols_and_theme():
    sa = SelfAlignment()
    world = WorldState(motifs=["a lantern"])
    ident = _ident(["leadership", "trust", "grief", "lantern"])
    scene = _scene(
        theme="Grief",
        symbols=["broken trust"],
        beats=[SceneBeat(kind="event", text="Someone must take LEADERSHIP of the boats.")],
    )
    rep = sa.evaluate(ident, world, scene)
    assert rep.reflection_pct == 1.0
    rep2 = sa.evaluate(_ident(["leadership", "patience"], 1), WorldState(),
                       _scene(beats=[SceneBeat(kind="description",
                                               text="Her leadership is quiet.")]))
    assert rep2.reflection_pct == 0.5


def _outcome(scene, **kw):
    return Outcome(scene_id=scene.id, **kw)


def test_signal_for_maps_outcomes():
    sa = SelfAlignment()
    world = WorldState()
    ident = _ident(["leadership"])
    scene = _scene(theme="leadership", intensity=0.5)

    won = sa.signal_for(ident, world, scene, _outcome(
        scene, success=True, lesson="I can lead.",
        trait_updates={"leadership_confidence": 0.17},
        user_action=UserAction(raw="I lead them out")))
    assert won.engagement == 1.0
    assert won.reflection == 1.0
    assert won.integration > 0.7
    assert won.surprise_external == 0.0

    fled = sa.signal_for(ident, world, scene, _outcome(
        scene, success=False, lesson="I turned away from leadership; the question remains.",
        trait_updates={"leadership_confidence": -0.06},
        user_action=UserAction(raw="I run")))
    assert fled.engagement == -1.0
    assert fled.integration <= 0.1

    drift = sa.signal_for(ident, world, scene, _outcome(
        scene, success=None, user_action=UserAction(raw="I look around")))
    assert drift.engagement == 0.0 and drift.integration == 0.0

    hedge = sa.signal_for(ident, world, scene, _outcome(
        scene, success=None, user_action=UserAction(raw="maybe I should leave")))
    assert hedge.engagement < 0

    # Not reflected by the scene, but the outcome touched it.
    other = _scene(theme="weather")
    touched = sa.signal_for(ident, world, other, _outcome(
        other, success=True, themes_touched=["leadership"]))
    assert touched.reflection == 1.0
    assert sa.signal_for(ident, world, other, _outcome(other)).reflection == 0.0

    shock_scene = _scene(theme="leadership", intensity=1.0, is_vision=True,
                         beats=[SceneBeat(kind="event", text="The tower falls.")])
    shock = sa.signal_for(ident, world, shock_scene, _outcome(shock_scene))
    assert shock.surprise_external == 1.0


def test_signal_for_drives_convergence_end_to_end():
    """Signals read off real outcomes order convergence the same way."""
    sa = SelfAlignment()
    world = WorldState()
    ident = _ident(["leadership"])

    def traj(success, raw):
        ce = ConvergenceEngine(seed=2)
        for t in range(N):
            scene = _scene(theme="leadership" if success else "fog", intensity=0.5)
            out = _outcome(
                scene, success=success, user_action=UserAction(raw=raw),
                lesson="I can lead." if success else None,
                trait_updates={"leadership_confidence": 0.17 if success else -0.06},
            )
            c = ce.step_with(sa.signal_for(ident, world, scene, out), 0.5).convergence
        return c

    assert traj(True, "I lead") > 0.6 > 0.3 > traj(False, "I flee")
