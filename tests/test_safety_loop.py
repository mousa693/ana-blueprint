"""Safety-loop regressions: forbidden themes never reach the protagonist, the
harmonizer's veto and corrections are honoured, the block path keeps the record
honest and the NPC callbacks intact, entropy shocks are real shocks, and the
safe interlude is never learned into the identity."""
from __future__ import annotations

import re

import pytest

from ana.config import ANAConfig
from ana.contracts import (
    IdentitySnapshot,
    NPC,
    Archetype,
    Scene,
    SceneBeat,
    WorldState,
)
from ana.engine.feedback_loop import INTERLUDE_THEME, is_interlude
from ana.npc.layer import NPCDirector, is_entropy_beat
from ana.session import build_session
from ana.subsystems.alignment import (
    SelfAlignment,
    has_entropy_shock,
    mentions_forbidden,
)
from ana.subsystems.harmonizer import TruthHarmonizer

LIGHTHOUSE_THEMES = ["leadership", "purpose", "courage"]


def _identity():
    from ana.cli import _seed_lighthouse_identity
    return _seed_lighthouse_identity()


def _session(seed=42, **kw):
    cfg = ANAConfig(use_llm=False, seed=seed, **kw)
    return build_session(cfg, identity=_identity())


def _texts(scene: Scene) -> list[str]:
    out = [scene.title, scene.summary, scene.theme, *scene.symbols, *scene.choices]
    for b in scene.beats:
        out += [b.text, b.speaker or "", b.symbol or ""]
    return out


def _leaks(scene: Scene, word: str) -> list[str]:
    pat = re.compile(rf"(?<![a-z0-9]){re.escape(word)}", re.I)
    return [t for t in _texts(scene) if pat.search((t or "").replace("_", " "))]


# ─────────────────────────────────────────────────────────────
# 1. Forbidden themes never reach the protagonist
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("forbidden", LIGHTHOUSE_THEMES)
@pytest.mark.parametrize("seed", [42, 7, 1234])
def test_forbidden_theme_never_in_scene_text(forbidden, seed):
    sess = _session(seed=seed, forbidden_themes=[forbidden])
    recs = sess.run(30)
    assert len(recs) == 30
    for r in recs:
        assert _leaks(r.scene, forbidden) == [], (r.tick, _leaks(r.scene, forbidden))
        # The story's view of the self never carries it either.
        assert forbidden not in r.request.identity.active_themes
        assert f"never depict: {forbidden}" in r.request.constraints
        assert all(n.represents is None or not mentions_forbidden(
            n.represents, [forbidden], synonyms=True) for n in r.request.world.npcs)
        assert forbidden not in [t.lower() for t in r.outcome.themes_touched]
    # The real identity is untouched: the record keeps it whole.
    assert forbidden in recs[0].identity_before.active_themes
    # The defences in depth mean the final gate rarely has to fire.
    assert sum(is_interlude(r.scene) for r in recs) <= 3


def test_safety_gate_scans_every_text_field():
    sa = SelfAlignment(forbidden_themes=["leadership"])
    ident = IdentitySnapshot(active_themes=["trust"])
    world = WorldState()

    def blocked(scene):
        return sa.evaluate(ident, world, scene).blocked

    base = dict(title="t", summary="s", theme="trust", choices=["go"], symbols=["key"],
                beats=[SceneBeat(kind="description", text="Fog.")])
    assert not blocked(Scene(**base))
    for field, value in [("title", "On Leadership"), ("summary", "about leadership"),
                         ("theme", "leadership"), ("choices", ["Take the leadership"]),
                         ("symbols", ["leadership crown"])]:
        assert blocked(Scene(**{**base, field: value})), field
    for beat in [SceneBeat(kind="dialogue", text="You keep refusing leadership."),
                 SceneBeat(kind="dialogue", text="hi", speaker="Leadership Ghost"),
                 SceneBeat(kind="event", text="x", symbol="leadership_confidence")]:
        assert blocked(Scene(**{**base, "beats": [beat]}))
    # Whole-word anchored: "encourage" is not "courage"; empty theme never matches.
    sa2 = SelfAlignment(forbidden_themes=["courage", "rest"])
    ok = Scene(title="Encouragement", summary="an interesting day", theme="",
               beats=[SceneBeat(kind="description", text="They encourage you.")])
    assert not sa2.evaluate(ident, world, ok).blocked


# ─────────────────────────────────────────────────────────────
# 2. Harmonizer corrections are kept; a veto is honoured
# ─────────────────────────────────────────────────────────────

class _VetoAt(TruthHarmonizer):
    """Corrections at tick 1 (approved); veto at tick 2 (every review)."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reviewed: list[tuple[int, str]] = []

    def review(self, scene, world, ident):
        v = super().review(scene, world, ident)
        self.reviewed.append((ident.tick, scene.id))
        if ident.tick == 1:
            v.corrections = ["Keep the harbour bell silent"]
        if ident.tick == 2:
            v.corrections = ["Frame the stranger's return as a vision"]
            v.continuity_errors = ["the stranger was dead"]
            v.approved = False
        return v


def test_harmonizer_corrections_survive_carry_forward_and_veto_replaces_scene():
    sess = _session()
    h = _VetoAt(seed=42)
    sess.engine.harmonizer = h
    recs = sess.run(4)
    # Corrections from an approved scene reach the very next request...
    assert "Keep the harbour bell silent" in recs[2].request.constraints
    # ...and are delivered once, not forever.
    assert "Keep the harbour bell silent" not in recs[3].request.constraints
    # The vetoed tick-2 scene was replaced: regenerated once (still vetoed by
    # this harmonizer) and then the safe interlude played.
    tick2 = [sid for t, sid in h.reviewed if t == 2]
    assert len(tick2) == 3 and recs[2].scene.id == tick2[-1]
    assert recs[2].scene.id not in tick2[:2]
    assert is_interlude(recs[2].scene)
    assert any(e.startswith("replaced (vetoed):") for e in recs[2].harmonizer.continuity_errors)
    assert "Frame the stranger's return as a vision" in recs[2].harmonizer.corrections
    assert "Frame the stranger's return as a vision" in recs[3].request.constraints


class _VetoOnce(TruthHarmonizer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.done = False

    def review(self, scene, world, ident):
        v = super().review(scene, world, ident)
        if ident.tick == 1 and not self.done:
            self.done = True
            v.approved = False
            v.corrections = ["Name the harbour master"]
        return v


def test_veto_regenerates_with_corrections_as_constraints():
    sess = _session()
    sess.engine.harmonizer = _VetoOnce(seed=42)
    seen = []
    gen = sess.engine.generative
    orig = gen.generate

    def spy(req):
        seen.append((req.identity.tick, list(req.constraints)))
        return orig(req)

    gen.generate = spy
    recs = sess.run(3)
    tick1 = [c for t, c in seen if t == 1]
    assert len(tick1) == 2 and "Name the harbour master" in tick1[1]
    assert not is_interlude(recs[1].scene) and recs[1].harmonizer.approved
    assert not recs[1].alignment.blocked  # a veto is not a safety block


# ─────────────────────────────────────────────────────────────
# 3. Block path: full pipeline on the final scene, honest record
# ─────────────────────────────────────────────────────────────

class _LeakyFirstDraft:
    """Wraps a generator: the first draft of each tick in `ticks` leaks."""

    def __init__(self, inner, word, ticks):
        self.inner, self.word, self.ticks = inner, word, set(ticks)
        self.seen: set[int] = set()

    def generate(self, req):
        scene = self.inner.generate(req)
        t = req.identity.tick
        if t in self.ticks and t not in self.seen:
            self.seen.add(t)
            scene.beats.insert(1, SceneBeat(kind="dialogue", speaker="Corren",
                                            text=f"You keep refusing {self.word}."))
        return scene


class _Recorder(TruthHarmonizer):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reviewed: list[tuple[int, str]] = []

    def review(self, scene, world, ident):
        self.reviewed.append((ident.tick, scene.id))
        return super().review(scene, world, ident)


_CALLBACK_SYMBOL = {"mentor_returns": "the mentor's return", "shadow_faced": "the shadow",
                    "ally_helps": "the returning ally", "trust_issue": "trust",
                    "witness": "the witness"}


def _blocked_session(ticks, seed=42):
    sess = _session(seed=seed, forbidden_themes=["leadership"])
    eng = sess.engine
    eng.generative = _LeakyFirstDraft(eng.generative, "leadership", ticks)
    eng.harmonizer = _Recorder(seed=seed)
    return sess


def test_block_path_record_fidelity_and_final_scene_pipeline():
    ticks = range(0, 30, 2)
    sess = _blocked_session(ticks)
    recs = sess.run(30)
    h = sess.engine.harmonizer
    for r in recs:
        assert _leaks(r.scene, "leadership") == []
        if r.tick in ticks:
            # The original block is visible on the record...
            assert r.alignment.blocked
            assert r.alignment.block_reason.startswith("replaced: forbidden theme")
            # ...and the harmonizer's verdict is for the scene actually played.
            last = [sid for t, sid in h.reviewed if t == r.tick][-1]
            assert last == r.scene.id
        else:
            assert not r.alignment.blocked
    # Most replacements are real regenerated scenes (not interludes) that went
    # through the NPC layer: entropy shocks can land on them.
    replaced = [r for r in recs if r.tick in ticks]
    assert sum(not is_interlude(r.scene) for r in replaced) >= len(replaced) - 2
    assert any(has_entropy_shock(r.scene) for r in replaced)


@pytest.mark.parametrize("seed", [42, 7])
def test_block_path_never_loses_npc_callbacks(seed):
    sess = _blocked_session(range(30), seed=seed)  # every tick's first draft blocked
    recs = sess.run(30)
    shown = {(b.speaker, b.symbol) for r in recs for b in r.scene.beats}
    marked = [(m.name, k) for m in sess.engine.npc.cast.values() for k in m.callback_kinds]
    assert marked, "the journey should reach callback stages"
    for name, kind in marked:
        assert (name, _CALLBACK_SYMBOL[kind]) in shown, (name, kind)


def test_director_withdraw_requeues_or_unmarks_callbacks():
    d = NPCDirector(seed=1, entropy_prob=0.0)
    ident = IdentitySnapshot(active_themes=["trust"])
    world = WorldState(npcs=[NPC(name="Ines", archetype=Archetype.MENTOR)])
    d.populate(world, ident, stage="call")
    d.populate(world, ident, stage="ordeal")
    a = d.inject_entropy(Scene(title="a"), world)
    cb = [b for b in a.beats if b.symbol == "the mentor's return"]
    assert cb
    d.withdraw(a)  # re-queue for the replacement
    b = d.inject_entropy(Scene(title="b"), world)
    assert [x.text for x in b.beats if x.symbol == "the mentor's return"] == [cb[0].text]
    d.withdraw(b, requeue=False)  # nothing will carry it: un-mark
    assert "mentor_returns" not in d.member("Ines").callback_kinds
    d.populate(world, ident, stage="ordeal")  # can be planned again
    c = d.inject_entropy(Scene(title="c"), world)
    assert any(x.symbol == "the mentor's return" for x in c.beats)


# ─────────────────────────────────────────────────────────────
# 4. Entropy shocks are real shocks
# ─────────────────────────────────────────────────────────────

def test_no_entropy_shocks_when_entropy_is_off():
    sess = _session(entropy_prob=0.0)
    recs = sess.run(40)
    assert sum(r.scene.beats[-1].kind == "event" for r in recs) > 10  # generator's own
    assert not any(has_entropy_shock(r.scene) for r in recs)
    sa = sess.engine.alignment
    for r in recs[:10]:
        sig = sa.signal_for(r.request.identity, r.request.world, r.scene, r.outcome)
        expected = (0.5 if r.scene.is_vision else 0.0) + 0.3 * max(
            0.0, min(1.0, (r.scene.intensity - 0.6) / 0.4))
        assert sig.surprise_external == pytest.approx(min(1.0, expected))


def test_entropy_beats_are_marked_wherever_they_sit():
    sess = _session(entropy_prob=1.0)
    recs = sess.run(10)
    for r in recs:
        marked = [b for b in r.scene.beats if is_entropy_beat(b)]
        assert len(marked) == 1 and has_entropy_shock(r.scene)
        assert marked[0].symbol.lstrip("~") in r.scene.symbols
    # A shock that is not the last beat (a grounding note follows) still counts.
    s = Scene(beats=[SceneBeat(kind="event", text="A squall.", symbol="~storm"),
                     SceneBeat(kind="dialogue", text="Breathe.", speaker="a grounding voice")])
    assert has_entropy_shock(s)
    # The generator's own stage event is not a shock.
    s2 = Scene(beats=[SceneBeat(kind="event", text="A door.", symbol="the crossing")])
    assert not has_entropy_shock(s2)


# ─────────────────────────────────────────────────────────────
# 5. The safe interlude is never learned into the identity
# ─────────────────────────────────────────────────────────────

class _AlwaysLeaks:
    def __init__(self, inner, word):
        self.inner, self.word = inner, word

    def generate(self, req):
        scene = self.inner.generate(req)
        scene.beats.append(SceneBeat(kind="description", text=f"Pure {self.word}."))
        return scene


def test_interlude_does_not_teach_rest():
    sess = _session(forbidden_themes=["courage"])
    eng = sess.engine
    eng.generative = _AlwaysLeaks(eng.generative, "courage")
    recs = sess.run(8)
    assert all(is_interlude(r.scene) for r in recs)
    assert all(r.scene.theme == INTERLUDE_THEME for r in recs)
    assert all(r.alignment.blocked and "replaced:" in r.alignment.block_reason for r in recs)
    assert all(INTERLUDE_THEME not in r.outcome.themes_touched for r in recs)
    assert INTERLUDE_THEME not in sess.identity.active_themes
    assert f"{INTERLUDE_THEME}_confidence" not in [
        t.name for t in sess.identity.snapshot().traits]
    assert all(_leaks(r.scene, "courage") == [] for r in recs)
