"""Tests for the journey arc, the debrief, and the persistent NPC cast.

Hermetic: no network, no model. LLM paths use fake gateway objects.
"""
from __future__ import annotations

import pytest

from ana.config import ANAConfig
from ana.contracts import (
    AlignmentReport, Archetype, CycleRecord, EmotionalState, GenerationRequest,
    Goal, HarmonizerVerdict, IdentitySnapshot, NPC, Outcome, Scene, SceneBeat,
    Trait, UserAction, WorldState,
)
from ana.engine.arc import (
    DEBRIEF_SCHEMA, STAGES, Debrief, JourneyArc, build_debrief, infer_stage,
    normalize_stage,
)
from ana.npc.layer import NPCDirector, classify_action


# ── helpers ──────────────────────────────────────────────────────────

_LABELS = {
    "call": "The Call To Adventure", "threshold": "Crossing The Threshold",
    "mentor": "Meeting The Mentor", "ordeal": "The Ordeal",
    "reward": "Seizing The Reward", "return": "The Return",
}


def make_record(tick: int, stage: str | None = None, *, title: str | None = None,
                convergence: float = 0.1, success: bool | None = None,
                lesson: str | None = None, action: str = "I look around.",
                shadow: bool = False, symbols: list[str] | None = None,
                identity: IdentitySnapshot | None = None) -> CycleRecord:
    npcs = [NPC(name="Old Ines", archetype=Archetype.MENTOR)]
    beats = [SceneBeat(kind="description", text="The harbour at dusk.")]
    if shadow:
        npcs.append(NPC(name="Mara", archetype=Archetype.SHADOW))
        beats.append(SceneBeat(kind="dialogue", text="Mara speaks.", speaker="Mara",
                               archetype=Archetype.SHADOW))
    world = WorldState(location="a town waking by the water", npcs=npcs)
    ident = identity or IdentitySnapshot(tick=tick, convergence=convergence)
    scene = Scene(title=title if title is not None else _LABELS.get(stage or "", "A quiet hour"),
                  summary="Something happens.", beats=beats, symbols=symbols or [])
    return CycleRecord(
        tick=tick, identity_before=ident,
        request=GenerationRequest(identity=ident, world=world),
        scene=scene, alignment=AlignmentReport(), harmonizer=HarmonizerVerdict(),
        outcome=Outcome(scene_id=scene.id, success=success, lesson=lesson,
                        user_action=UserAction(raw=action),
                        npc_events=["Old Ines nods slowly"]),
        convergence=convergence,
    )


@pytest.fixture
def session_records(tmp_path):
    """A real, short, deterministic ANA session (10 cycles)."""
    from ana.session import build_session
    from ana.engine.drivers import ScriptedDriver
    from ana.identity.core import IdentityCore
    from ana.identity.memory import ShardMemory

    ic = IdentityCore(memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.add_goal("find purpose", horizon="life", is_future_memory=True)
    ic.active_themes = ["leadership", "purpose"]
    cfg = ANAConfig(use_llm=False, max_cycles=10, data_dir=tmp_path,
                    shard_db=str(tmp_path / "shards.db"))
    sess = build_session(cfg, identity=ic, driver=ScriptedDriver(
        ["I explore.", "I climb the lighthouse.", "I organize a rescue and lead.",
         "I speak honestly."]))
    records = sess.run()
    yield sess, records
    sess.close()


# ── stage tracking ───────────────────────────────────────────────────

def test_normalize_and_infer_stage():
    assert normalize_stage("Ordeal") == "ordeal"
    assert normalize_stage("the call to adventure") == "call"
    assert normalize_stage("seizing the reward") == "reward"
    assert normalize_stage("nonsense") is None
    assert infer_stage(make_record(0, "threshold")) == "threshold"
    assert infer_stage(make_record(0, title="At The Return")) == "return"
    # signature symbol on an otherwise unlabeled scene
    assert infer_stage(make_record(0, title="Fog", symbols=["the dark water"])) == "ordeal"
    assert infer_stage(make_record(0, title="Fog")) is None


def test_arc_records_stages_and_chapters(session_records):
    _, records = session_records
    arc = JourneyArc()
    for r in records:
        arc.record(r)
    assert len(arc.entries) == len(records)
    assert arc.entries[0].stage == "call"
    assert all(e.stage in STAGES for e in arc.entries)
    # stages inferred from real deterministic scene titles/summaries
    assert arc.reached("threshold")
    assert arc.current_chapter().startswith(f"Chapter {len(arc.chapters())}: ")
    assert arc.npcs_met()  # people actually spoke / reacted
    # recording the same record twice is a no-op
    arc.record(records[-1])
    assert len(arc.entries) == len(records)


def test_explicit_stage_overrides_inference():
    arc = JourneyArc()
    arc.record(make_record(0, "call"), stage="Ordeal")
    assert arc.stage == "ordeal"
    # an unlabeled scene carries the previous stage forward
    arc.record(make_record(1, title="Fog"))
    assert arc.stage == "ordeal"
    assert JourneyArc().current_chapter() == "Prologue"


def test_milestones():
    arc = JourneyArc()
    plan = ["call", "call", "call", "threshold", "mentor", "mentor",
            "ordeal", "ordeal", "ordeal", "reward", "return"]
    for i, st in enumerate(plan):
        arc.record(make_record(
            i, st, success=True if i != 2 else False,
            lesson="Leading is deciding before I feel certain." if i == 1 else None,
            shadow=(i == 8), action="I face her." if i == 8 else "I go on.",
            convergence=0.06 * i))
    ms = arc.milestones()
    assert "crossed the threshold at cycle 4" in ms
    assert "first lesson at cycle 2" in ms
    assert "faced the Shadow at cycle 9" in ms
    assert "heard the call at cycle 1" in ms
    assert "turned for home at cycle 11" in ms
    assert "first time you turned away, at cycle 3" in ms
    # chronological
    cycles = [int(m.split("cycle ")[1].split()[0].strip(",.")) for m in ms]
    assert cycles == sorted(cycles)
    assert arc.current_chapter() == "Chapter 6: The Return"


def test_shadow_not_faced_when_fled():
    arc = JourneyArc()
    arc.record(make_record(0, "threshold", shadow=True, action="I flee from her.",
                           success=False))
    assert not any("Shadow" in m for m in arc.milestones())


# ── closing ──────────────────────────────────────────────────────────

def test_should_close():
    arc = JourneyArc(max_return_cycles=None)
    for i, st in enumerate(["call", "threshold", "mentor", "ordeal", "reward"]):
        arc.record(make_record(i, st))
    assert not arc.should_close(0.95, 1.0)          # not home yet
    arc.record(make_record(5, "return"))
    assert not arc.should_close(0.3, 0.1)           # home, but not converged
    assert arc.should_close(0.8, 0.1)               # home and converged
    assert arc.should_close(0.3, 0.9)               # home and goals fulfilled
    assert not JourneyArc().should_close(1.0, 1.0)  # nothing yet


def test_should_close_guards():
    # chapter guard: oscillating stages never reaching the return
    arc = JourneyArc(max_chapters=4)
    for i, st in enumerate(["call", "threshold", "call", "threshold"]):
        arc.record(make_record(i, st))
    assert arc.should_close(0.1, 0.0)
    # homecoming guard
    arc = JourneyArc(max_return_cycles=2)
    arc.record(make_record(0, "return"))
    assert not arc.should_close(0.1, 0.0)
    arc.record(make_record(1, "return"))
    assert arc.should_close(0.1, 0.0)
    # cycle guard
    arc = JourneyArc(max_cycles=3, max_chapters=99)
    for i in range(3):
        arc.record(make_record(i, "call"))
    assert arc.should_close(0.0, 0.0)


# ── stage hint ───────────────────────────────────────────────────────

def _drive(convs: list[float], dwell: int = 3) -> list[str]:
    arc = JourneyArc(stage_dwell=dwell, max_return_cycles=None, max_cycles=None,
                     max_chapters=999)
    hint = None
    stages: list[str] = []
    for i, c in enumerate(convs):
        st = hint or "call"
        arc.record(make_record(i, st, convergence=c), stage=st)
        stages.append(st)
        hint = arc.stage_hint(c)
        assert hint in STAGES
    return stages


@pytest.mark.parametrize("convs", [
    [0.01 * i for i in range(40)],                         # slow climb
    [0.05 * i for i in range(20)],                         # brisk climb
    [0.3, 0.5, 0.1, 0.6, 0.05, 0.9, 0.2, 0.95, 0.1, 0.4] * 3,  # jagged
    [0.9 - 0.04 * i for i in range(20)],                   # falling
    [0.0] * 25,                                            # stuck
])
def test_stage_hint_never_skips_or_slides_back(convs):
    stages = _drive(convs)
    idx = [STAGES.index(s) for s in stages]
    furthest = 0
    for prev, cur in zip(idx, idx[1:]):
        assert cur <= max(furthest, prev) + 1, "skipped a stage forward"
        furthest = max(furthest, prev)
        assert cur >= furthest - 1, "slid back more than one stage"
    # ordeal, reward, return arrive in order
    firsts = [idx.index(STAGES.index(s)) for s in ("ordeal", "reward", "return") if
              STAGES.index(s) in idx]
    assert firsts == sorted(firsts)
    assert "return" in stages, "journey never came home"
    assert idx.index(5) <= 6 * 3


def test_stage_hint_none_before_start_and_progresses_with_convergence():
    arc = JourneyArc(stage_dwell=10)
    assert arc.stage_hint(0.9) is None
    arc.record(make_record(0, "call"), stage="call")
    assert arc.stage_hint(0.0) == "call"
    assert arc.stage_hint(0.9) == "threshold"   # ready, but one stage at a time


def test_stage_hint_revisits_once_when_self_slides_back():
    arc = JourneyArc(stage_dwell=10)
    for i, (st, c) in enumerate([("call", 0.1), ("threshold", 0.2), ("mentor", 0.4),
                                 ("ordeal", 0.55), ("ordeal", 0.3)]):
        arc.record(make_record(i, st, convergence=c), stage=st)
    assert arc.stage_hint(0.3) == "mentor"      # slid back: revisit one stage
    arc.record(make_record(5, "mentor", convergence=0.2), stage="mentor")
    assert arc.stage_hint(0.2) == "ordeal"      # then forward again, never further back


# ── debrief ──────────────────────────────────────────────────────────

def test_deterministic_debrief_from_real_session(session_records):
    _, records = session_records
    d = build_debrief(records, use_llm=False)
    assert isinstance(d, Debrief) and d.source == "deterministic"
    assert d.title and d.arc_summary and d.closing_image
    assert d.arc_summary.startswith("You ")
    # lessons are exactly the earned (non-retreat) lessons, in order, deduped
    earned = []
    for r in records:
        les = r.outcome.lesson
        if les and r.outcome.success is not False and "turned away" not in les \
                and les not in earned:
            earned.append(les)
    assert d.lessons == earned and d.lessons
    # how_you_changed reflects trait deltas first -> last identity_before
    first = records[0].identity_before.trait("leadership_confidence")
    last = records[-1].identity_before.trait("leadership_confidence")
    assert last != first
    line = next(x for x in d.how_you_changed if "leadership confidence" in x)
    assert f"{first:.2f}" in line and f"{last:.2f}" in line
    # recurring symbols really recur
    for sym in d.recurring_symbols:
        n = sum(1 for r in records if sym in r.scene.symbols or sym in r.outcome.symbols)
        assert n >= 2
    assert d.recurring_symbols
    assert len(d.chapters) >= 1 and d.chapters[0].startswith("Chapter 1, The Call")
    assert d.open_questions
    md = d.to_markdown()
    assert md.startswith(f"# {d.title}")
    for head in ("## The chapters", "## What you learned", "## How you changed",
                 "## Symbols that kept returning"):
        assert head in md
    assert "> " in md


def test_debrief_empty_and_with_cast(session_records):
    sess, records = session_records
    empty = build_debrief([])
    assert empty.title and "first step" in empty.arc_summary
    d = build_debrief(records, cast=sess.engine.npc.cast_summary())
    assert d.companions and any(" the mentor" in c for c in d.companions)


class _FakeGateway:
    def __init__(self, reply):
        self.reply = reply
        self.calls: list[dict] = []
        self.available = True

    def structured(self, system, user, schema, *, effort=None, max_tokens=None):
        self.calls.append({"system": system, "user": user, "schema": schema,
                           "effort": effort})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_llm_debrief_uses_structured_reply(session_records):
    _, records = session_records
    reply = {
        "title": "The Lighthouse You Became",
        "arc_summary": "You arrived unsure, and the town asked you to lead.",
        "chapters": ["The call", "The crossing"],
        "turning_points": ["You climbed the lighthouse."],
        "lessons": ["Being useful points toward purpose."],
        "how_you_changed": ["Your leadership confidence grew."],
        "recurring_symbols": ["an absent mayor"],
        "open_questions": ["What will you carry home?"],
        "closing_image": "The lamp turns once over the dark water.",
        "companions": [],                     # empty -> falls back to deterministic
    }
    gw = _FakeGateway(reply)
    d = build_debrief(records, gateway=gw, use_llm=True)
    assert d.source == "llm"
    assert d.title == "The Lighthouse You Became"
    assert d.closing_image.startswith("The lamp")
    assert d.companions == build_debrief(records).companions
    assert len(gw.calls) == 1
    call = gw.calls[0]
    assert call["schema"] is DEBRIEF_SCHEMA and call["effort"] == "medium"
    assert "Journey digest" in call["user"]
    # use_llm=False never calls the gateway
    build_debrief(records, gateway=gw, use_llm=False)
    assert len(gw.calls) == 1


@pytest.mark.parametrize("reply", [None, "not a dict", RuntimeError("boom")])
def test_llm_debrief_falls_back(session_records, reply):
    _, records = session_records
    det = build_debrief(records)
    d = build_debrief(records, gateway=_FakeGateway(reply), use_llm=True)
    assert d.source == "deterministic"
    assert d.model_dump() == det.model_dump()


def test_llm_debrief_skips_unavailable_gateway(session_records):
    _, records = session_records
    gw = _FakeGateway({"title": "x"})
    gw.available = False
    assert build_debrief(records, gateway=gw, use_llm=True).source == "deterministic"
    assert gw.calls == []


def _walk_schema(node):
    if isinstance(node, dict):
        for bad in ("minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"):
            assert bad not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        for v in node.values():
            _walk_schema(v)
    elif isinstance(node, list):
        for v in node:
            _walk_schema(v)


def test_debrief_schema_is_strict():
    _walk_schema(DEBRIEF_SCHEMA)
    assert set(DEBRIEF_SCHEMA["properties"]) >= {
        "title", "arc_summary", "chapters", "turning_points", "lessons",
        "how_you_changed", "recurring_symbols", "open_questions", "closing_image"}


# ── NPC cast registry, drift, callbacks ──────────────────────────────

def _identity():
    return IdentitySnapshot(
        traits=[Trait(name="leadership_confidence", value=0.25)],
        goals=[Goal(description="find purpose", horizon="life")],
        active_themes=["leadership"],
        emotion=EmotionalState(),
    )


def test_classify_action():
    assert classify_action("I help the fisher haul the rope") == "helped"
    assert classify_action("I follow her advice") == "followed"
    assert classify_action("I confront the figure") == "confronted"
    assert classify_action("I ignore them and walk on") == "ignored"
    assert classify_action("I run away") == "fled"
    assert classify_action("I refuse to help") == "ignored"
    assert classify_action("I explore.") is None


def test_cast_persists_and_does_not_bloat():
    d = NPCDirector(seed=7, entropy_prob=0.0)
    world, ident = WorldState(), _identity()
    d.populate(world, ident)
    mentor = next(n for n in world.npcs if n.archetype == Archetype.MENTOR)
    shadow = next(n for n in world.npcs if n.archetype == Archetype.SHADOW)
    for _ in range(15):
        d.populate(world, ident)
    names = [n.name for n in world.npcs]
    assert len(names) == len(set(names))
    assert len(world.npcs) <= d.max_present
    assert sum(1 for n in world.npcs if n.archetype == Archetype.MENTOR) == 1
    assert mentor.name in names and shadow.name in names
    summary = d.cast_summary()
    m = next(c for c in summary if c["name"] == mentor.name)
    assert m["times_met"] == 16 and m["role"] == "mentor"
    assert {"name", "archetype", "role", "represents", "times_met", "disposition",
            "disposition_start", "trend", "callbacks", "fled", "faced"} <= set(m)
    roles = {c["role"] for c in summary}
    assert {"mentor", "shadow"} <= roles


def test_external_npcs_are_kept_and_tracked():
    d = NPCDirector(seed=1, entropy_prob=0.0, max_present=1)
    fisher = NPC(name="the Fisher", archetype=Archetype.ALLY)
    world = WorldState(npcs=[fisher])
    for _ in range(4):
        d.populate(world, _identity())
    assert fisher in world.npcs
    assert d.member("the Fisher").role == "ally"


def test_disposition_drift_from_actions():
    d = NPCDirector(seed=3, entropy_prob=0.0)
    world, ident = WorldState(), _identity()
    d.populate(world, ident)
    scene = Scene(title="x")
    mentor = next(n for n in world.npcs if n.archetype == Archetype.MENTOR)
    shadow = next(n for n in world.npcs if n.archetype == Archetype.SHADOW)
    m0, s0 = mentor.disposition, shadow.disposition

    d.react(scene, UserAction(raw=f"I help {mentor.name} carry the lamp"), world)
    assert mentor.disposition > m0
    m1 = mentor.disposition
    d.react(scene, UserAction(raw="I flee into the dark"), world)
    assert mentor.disposition < m1
    assert shadow.disposition < s0          # fleeing feeds the Shadow
    s1 = shadow.disposition
    d.react(scene, UserAction(raw=f"I turn and confront {shadow.name}"), world)
    assert shadow.disposition > s1
    assert d.member(shadow.name).faced
    t = d.member(mentor.name).treatment
    assert t["helped"] == 1 and t["fled"] == 1
    assert d.member(mentor.name).interactions >= 1


def test_react_records_npc_fleeing():
    d = NPCDirector(seed=11, entropy_prob=0.0)
    world = WorldState(npcs=[NPC(name="Runner", archetype=Archetype.HERALD,
                                 disposition=-1.0, autonomy=1.0)])
    d.populate(world, IdentitySnapshot())
    for _ in range(10):
        lines = d.react(Scene(title="x"), UserAction(raw="I call for aid"), world)
        if any("Runner turns and flees" in l for l in lines):
            break
    assert d.member("Runner").fled >= 1


def _callback_beats(scene):
    return [b for b in scene.beats if b.symbol in (
        "the mentor's return", "the shadow", "the returning ally", "trust", "the witness")]


def test_stage_callbacks():
    d = NPCDirector(seed=5, entropy_prob=0.0)
    world, ident = WorldState(), _identity()
    for st in ["call", "call", "threshold", "mentor"]:
        d.populate(world, ident, stage=st)
        assert _callback_beats(d.inject_entropy(Scene(title="x"), world)) == []
    mentor = d._find("mentor").npc
    shadow = d._find("shadow").npc
    ally = next(m for m in d.cast.values() if m.role not in ("mentor", "shadow")
                and m.first_stage == "call")
    # an early ally the protagonist helped
    d.react(Scene(title="x"), UserAction(raw=f"I help {ally.name}"), world)
    # someone fled when called (the lighthouse villager)
    runner = NPC(name="the Panicked Villager", archetype=Archetype.HERALD, autonomy=0.9)
    world.npcs.append(runner)
    d.populate(world, ident, stage="mentor")
    d.member(runner.name).fled = 1
    world.npcs = [n for n in world.npcs if n.name != runner.name]  # off-stage now

    # ordeal: the Mentor returns and the Shadow must be faced
    d.populate(world, ident, stage="ordeal")
    assert [n.name for n in world.npcs[:2]] == [mentor.name, shadow.name]
    scene = d.inject_entropy(Scene(title="ordeal"), world)
    beats = _callback_beats(scene)
    assert {b.speaker for b in beats} == {mentor.name, shadow.name}
    assert any("facing me" in b.text for b in beats)
    # announced once per scene, and once per journey per kind
    assert d.inject_callback(scene, world) is scene and len(_callback_beats(scene)) == 2
    d.populate(world, ident, stage="ordeal")
    assert _callback_beats(d.inject_entropy(Scene(title="ordeal 2"), world)) == []
    assert mentor in world.npcs  # still present for the rest of the ordeal

    # reward: the early ally helps, and the one who fled asks for trust
    d.populate(world, ident, stage="reward")
    scene = d.inject_callback(Scene(title="reward"), world, "reward")
    by_sym = {b.symbol: b for b in _callback_beats(scene)}
    assert by_sym["the returning ally"].speaker == ally.name
    assert "You helped me once" in by_sym["the returning ally"].text
    assert by_sym["trust"].speaker == runner.name
    assert runner in world.npcs

    # return: someone from the call witnesses the homecoming
    d.populate(world, ident, stage="return")
    scene = d.inject_entropy(Scene(title="return"), world)
    witness = [b for b in _callback_beats(scene) if b.symbol == "the witness"]
    assert len(witness) == 1
    assert d.member(witness[0].speaker).first_stage == "call"

    summary = {c["name"]: c for c in d.cast_summary()}
    assert summary[mentor.name]["callbacks"] == ["ordeal"]
    assert "reward" in summary[ally.name]["callbacks"]


def test_no_stage_means_no_callbacks():
    d = NPCDirector(seed=5, entropy_prob=0.0)
    world, ident = WorldState(), _identity()
    for _ in range(6):
        d.populate(world, ident)
        assert _callback_beats(d.inject_entropy(Scene(title="x"), world)) == []
    assert all(c["callbacks"] == [] for c in d.cast_summary())


# ── end-to-end: a staged journey reaches home, closes, and debriefs ─

def test_staged_journey_reaches_return_and_closes(tmp_path):
    """The real loop wiring: the arc's stage hint drives generation and the cast,
    the journey advances through every stage in order, the cast calls back at
    the ordeal/return, and the run stops on its own once it has come home."""
    from ana.session import build_session
    from ana.engine.drivers import ScriptedDriver
    from ana.identity.core import IdentityCore
    from ana.identity.memory import ShardMemory

    ic = IdentityCore(memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.add_goal("find purpose", horizon="life", is_future_memory=True)
    ic.active_themes = ["leadership", "purpose"]
    cfg = ANAConfig(use_llm=False, data_dir=tmp_path, shard_db=str(tmp_path / "s.db"))
    sess = build_session(cfg, identity=ic, driver=ScriptedDriver(
        ["I explore the harbour.", "I climb the lighthouse.", "I organize a rescue and lead.",
         "I speak honestly.", "Step across", "I accept the gift and listen.",
         "I face it directly.", "I share it at once.", "I trust them again.",
         "Carry it home"] * 3))
    eng, npc = sess.engine, sess.engine.npc
    records = sess.run(30, until_close=True)
    arc = eng.arc
    assert eng.closed and len(records) < 30
    assert arc.stage == "return"
    # every record carries the stage it realised, and it matches the arc
    assert [r.scene.stage for r in records] == [e.stage for e in arc.entries]
    order = [arc.first_cycle_at(s) for s in STAGES]
    assert all(c is not None for c in order) and order == sorted(order)
    all_beats = [b for rec in eng.history for b in rec.scene.beats]
    syms = {b.symbol for b in all_beats}
    assert {"the mentor's return", "the shadow", "the witness"} <= syms

    d = build_debrief(eng.history, arc=arc, cast=npc.cast_summary())
    assert any(", The Return" in c for c in d.chapters)  # chapter number varies with step-backs
    assert any("returned at ordeal" in c for c in d.companions)
    assert "the road bends homeward" in d.closing_image
    sess.close()
