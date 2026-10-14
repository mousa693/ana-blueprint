"""Prose quality: every sentence a player reads is grammatical, specific and
humane — never machine-ish.

Runs a few dozen deterministic journeys to closure through the web runtime
(``ana.runtime.JourneyRuntime``) — the lighthouse identity and an
onboarding-like identity, several seeds, with the self carried into a second
journey — and checks ALL player-visible text: pending-scene chapter/location,
scene titles, summaries, beats and choices, NPC reactions, lessons, the
debrief, and the journal's insights.
"""
from __future__ import annotations

import random
import re
from collections import defaultdict

import pytest

from ana.contracts import Archetype, UserAction
from ana.engine.arc import STAGES
from ana.engine.interpret import classify_stance
from ana.runtime import IdentitySeed, JourneyRuntime, SeedGoal, SeedMemory

# ─────────────────────────────────────────────────────────────
# Identities
# ─────────────────────────────────────────────────────────────

LIGHTHOUSE = IdentitySeed(
    name="the wanderer",
    traits={"leadership_confidence": 0.25, "honesty": 0.8, "courage": 0.4},
    beliefs=["I am not the kind of person others follow."],
    goals=[SeedGoal(description="find my purpose", horizon="life", is_future_memory=True),
           SeedGoal(description="become someone who can lead", horizon="mid",
                    is_future_memory=True)],
    memories=[SeedMemory(summary="A childhood by a lighthouse on a grey coast.",
                         themes=["purpose", "courage"], symbols=["lighthouse"])],
    active_themes=["leadership", "purpose", "courage"],
    valence=-0.1,
)

ONBOARDED = IdentitySeed(
    name="Mara",
    traits={"courage_confidence": 0.4, "grief_confidence": 0.25},
    beliefs=["people leave when it gets hard"],
    goals=[SeedGoal(description="Become someone who can speak up", horizon="mid",
                    is_future_memory=True),
           SeedGoal(description="Do one brave thing I have been putting off", horizon="near")],
    memories=[SeedMemory(summary="My grandmother's kitchen on rainy afternoons",
                         symbols=["grandmother's kitchen"])],
    active_themes=["courage", "grief"],
    valence=-0.3, stress=0.45,
)

SEEDS = (1, 2, 3, 4, 5, 6, 7)
_AVOID = ["I turn back and hide.", "I look away and wait it out."]


class _Player:
    """Picks offered choices with a seeded rng; now and then turns away in
    free text, so the avoidance paths (and their questions) are exercised."""

    def __init__(self, seed: int, avoid_rate: float):
        self.rng = random.Random(seed)
        self.avoid_rate = avoid_rate

    def act(self, scene) -> UserAction:
        if self.rng.random() < self.avoid_rate:
            return UserAction(raw=self.rng.choice(_AVOID))
        return UserAction(raw="", choice_index=self.rng.randrange(len(scene.choices)))


# ─────────────────────────────────────────────────────────────
# Running journeys and collecting what a player reads
# ─────────────────────────────────────────────────────────────

class Journey:
    def __init__(self):
        self.pending = []          # PendingScene per turn
        self.records = []          # CycleRecord per turn
        self.debrief = None
        self.insights: list[str] = []
        self.cast_names: list[str] = []
        self.goals: list[str] = []
        self.state: dict = {}


def _run(seed: IdentitySeed, seed_value: int, *, avoid_rate: float, state=None,
         gentle_at=None) -> Journey:
    rt = JourneyRuntime.start(seed, identity_id=f"prose-{seed.name}", shard_db=None,
                              use_llm=False, seed_value=seed_value, max_cycles=30,
                              identity_state=state)
    j = Journey()
    player = _Player(seed_value * 31 + len(seed.name), avoid_rate)
    try:
        while not rt.closed:
            p = rt.pending()
            j.pending.append(p)
            gentle = gentle_at is not None and len(rt.history) == gentle_at
            action = (UserAction(raw="I feel so hopeless and worthless lately")
                      if gentle else player.act(p.scene))
            j.records.append(rt.act(action, gentle=gentle))
        j.debrief = rt.debrief()
        j.insights = rt.insights(limit=0)
        j.cast_names = list(rt.engine.npc.cast)
        j.protagonist = seed.name
        j.goals = [g.description for g in rt.session.identity.goals]
        j.state = rt.identity_state()
    finally:
        rt.close()
    return j


@pytest.fixture(scope="module")
def journeys() -> list[Journey]:
    out: list[Journey] = []
    for s in SEEDS:
        out.append(_run(LIGHTHOUSE, s, avoid_rate=0.1 if s % 2 else 0.0))
        first = _run(ONBOARDED, s, avoid_rate=0.35 if s % 3 == 0 else 0.05)
        out.append(first)
        # The self carries over into a second journey (higher convergence).
        out.append(_run(ONBOARDED, s + 100, avoid_rate=0.05, state=first.state))
    return out


def _scene_texts(p) -> list[tuple[str, str]]:
    s = p.scene
    out = [("chapter", p.chapter), ("title", s.title), ("summary", s.summary)]
    out += [(f"beat:{b.kind}", b.text) for b in s.beats]
    out += [("choice", c) for c in s.choices]
    return out


def _debrief_texts(d) -> list[tuple[str, str]]:
    out = [("title", d.title), ("arc_summary", d.arc_summary),
           ("closing_image", d.closing_image)]
    for f in ("chapters", "turning_points", "lessons", "how_you_changed",
              "open_questions", "companions"):
        out += [(f, x) for x in getattr(d, f)]
    return out


def _all_texts(j: Journey) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in j.pending:
        out += _scene_texts(p)
    for r in j.records:
        out += [("npc_event", e) for e in r.outcome.npc_events]
        if r.outcome.lesson:
            out.append(("lesson", r.outcome.lesson))
    out += _debrief_texts(j.debrief)
    out += [("insight", x) for x in j.insights]
    return out


def _unquoted(text: str) -> str:
    """Text with quoted speech removed (quotes are a person's own words)."""
    return re.sub(r"\"[^\"]*\"", "\"…\"", text)


def _fail(found: list[str], what: str) -> None:
    assert not found, f"{what}:\n  " + "\n  ".join(found[:12])


# ─────────────────────────────────────────────────────────────
# Grammar and specificity
# ─────────────────────────────────────────────────────────────

def test_goal_text_is_humanised(journeys):
    bad = []
    for j in journeys:
        for field, t in _all_texts(j):
            for g in j.goals:
                if re.search(r"\byour " + re.escape(g) + r"\b", t, re.IGNORECASE):
                    bad.append(f"[{field}] {t}")
            if field != "lesson" and re.search(r"[a-z,;:—-] Become\b", t):
                bad.append(f"[{field}] {t}")
    _fail(bad, "raw goal text in prose")


def test_echoes_never_show_outcome_records(journeys):
    pat = re.compile(r":\s*(moved through|met|turned from) the moment")
    bad = [f"[{f}] {t}" for j in journeys for f, t in _all_texts(j) if pat.search(t)]
    _fail(bad, "outcome summaries leaked into prose")


_ADJ = {"gold", "honeyed", "amber", "soft", "warm", "grey", "slate", "pale", "thin",
        "steel-blue", "even", "muted", "level", "still", "silver", "quiet", "charged",
        "clear", "bruised", "dim", "flickering", "flat", "rinsed", "clean", "new"}


def test_no_doubled_words_in_a_sentence(journeys):
    bad = []
    for j in journeys:
        for f, t in _all_texts(j):
            if not f.startswith("beat:description"):
                continue
            for sent in re.split(r"(?<=[.;!?])\s+", _unquoted(t)):
                words = re.findall(r"[a-z][a-z-]*", sent.lower())
                dup = [w for w in _ADJ if words.count(w) > 1]
                if dup or re.search(r"\b(\w+), the \w+ \1\b", sent):
                    bad.append(f"[{f}] {sent}")
    _fail(bad, "an adjective repeated within one sentence")


_STAGE_MISUSE = re.compile(
    r"\b(at|stand at|stand in|this is|meets)\s+(crossing the threshold|seizing the reward|"
    r"meeting the mentor|the call to adventure)\b", re.IGNORECASE)


def test_stage_labels_are_never_places(journeys):
    bad = [f"[{f}] {t}" for j in journeys for f, t in _all_texts(j)
           if _STAGE_MISUSE.search(t) or re.search(r"\bstand at the (ordeal|return)\b", t)]
    _fail(bad, "stage label used as a place")


def test_sentences_start_with_a_capital(journeys):
    bad = []
    for j in journeys:
        for f, t in _all_texts(j):
            body = t.lstrip("\"'“‘(")
            if body[:1].isalpha() and not body[:1].isupper():
                bad.append(f"[{f}] {t}")
            for m in re.finditer(r"[.!?][\"”]?\s+([a-z])", _unquoted(t)):
                bad.append(f"[{f}] …{_unquoted(t)[max(0, m.start() - 30):m.end() + 20]}")
    _fail(bad, "a sentence starts in lower case")


_EMBEDDED = re.compile(r", (What|Become|Fear|Grief|Courage|My|It|This|There|When|Being|"
                       r"Not|Trust|Purpose|Leading|People|Listening|Freedom|I am)\b")


def test_no_capitalised_sentence_spliced_mid_sentence(journeys):
    bad = []
    for j in journeys:
        for f, t in _all_texts(j):
            text = re.sub(r"^Chapter \w+, [A-Z][\w ]+", "Chapter", _unquoted(t))
            if _EMBEDDED.search(text):
                bad.append(f"[{f}] {t}")
    _fail(bad, "an embedded sentence keeps its capital mid-sentence")


def test_motif_verbs_agree(journeys):
    bad = [f"[{f}] {t}" for j in journeys for f, t in _all_texts(j)
           if re.search(r"\b(leaves|gates|houses|boats|hearths|horizons|gazes|carvings) "
                        r"(draws|waits|marks|glints|looks)\b", t, re.IGNORECASE)]
    _fail(bad, "plural motif with a singular verb")


def test_weather_agrees_with_the_place(journeys):
    bad = []
    for j in journeys:
        for p in j.pending:
            if "storm" in p.location:
                desc = p.scene.beats[0].text if p.scene.beats else ""
                if re.search(r"\b(sky is clear|clear sky|gold light|lit with gold)\b", desc):
                    bad.append(desc)
    _fail(bad, "storm summit described as clear")


# ─────────────────────────────────────────────────────────────
# The debrief and insights speak human, not engine
# ─────────────────────────────────────────────────────────────

_INTERNAL = re.compile(r"\d|\b(cycles?|confidence|valence|convergence|entropy)\b",
                       re.IGNORECASE)


def test_debrief_and_insights_have_no_numbers_or_internal_terms(journeys):
    bad = []
    for j in journeys:
        for f, t in _debrief_texts(j.debrief) + [("insight", x) for x in j.insights]:
            if _INTERNAL.search(t):
                bad.append(f"[{f}] {t}")
        for sym in j.debrief.recurring_symbols:
            if sym.startswith("~") or sym in ("entropy", "echo") or _INTERNAL.search(sym):
                bad.append(f"[symbol] {sym}")
    _fail(bad, "numbers or engine vocabulary in the debrief/insights")


def test_debrief_questions_fit_what_happened(journeys):
    bad = []
    for j in journeys:
        per: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for r in j.records:
            for t in r.outcome.themes_touched:
                if r.outcome.success is True:
                    per[t][0] += 1
                elif r.outcome.success is False:
                    per[t][1] += 1
        for q in j.debrief.open_questions:
            m = re.search(r"turn away from (\w+)", q)
            if m and per[m.group(1)][1] <= per[m.group(1)][0]:
                bad.append(f"{q}  (met/avoided: {per[m.group(1)]})")
    _fail(bad, "asks about turning away from something mostly met")


def test_chapters_are_the_six_stages_at_most(journeys):
    for j in journeys:
        assert len(j.debrief.chapters) <= 6
        names = [re.match(r"Chapter [A-Z][a-z]+, (.+?) —", c).group(1)
                 for c in j.debrief.chapters]
        assert len(names) == len(set(names)), names
        nums = {int(re.match(r"Chapter (\d+):", p.chapter).group(1)) for p in j.pending}
        assert max(nums) <= 6, [p.chapter for p in j.pending]


def test_every_journey_opens_at_the_call(journeys):
    for j in journeys:
        assert j.pending[0].stage == "call"
        assert j.pending[0].chapter == "Chapter 1: The Call"


def test_never_back_to_the_call_after_the_threshold(journeys):
    for j in journeys:
        stages = [p.stage for p in j.pending]
        if "threshold" in stages:
            assert "call" not in stages[stages.index("threshold"):], stages


# ─────────────────────────────────────────────────────────────
# The cast: present, consistent, not repetitive
# ─────────────────────────────────────────────────────────────

def _actor(event: str, names: list[str]) -> str | None:
    low = event.lower()
    for n in sorted(names, key=len, reverse=True):
        if low.startswith(n.lower() + " ") or low.startswith(n.lower() + ","):
            return n
    return None


def test_reactions_come_only_from_npcs_in_the_scene(journeys):
    bad = []
    for j in journeys:
        for r in j.records:
            speakers = {(b.speaker or "").lower() for b in r.scene.beats if b.speaker}
            for e in r.outcome.npc_events:
                who = _actor(e, j.cast_names)
                if who is None or who.lower() not in speakers:
                    bad.append(f"{e!r} — scene voices: {sorted(speakers)}")
    _fail(bad, "a reaction from someone not in the scene")


def test_reactions_do_not_contradict_the_callback(journeys):
    bad = []
    for j in journeys:
        for r in j.records:
            askers = {b.speaker for b in r.scene.beats
                      if b.symbol in ("trust", "the returning ally", "the mentor's return")}
            for e in r.outcome.npc_events:
                who = _actor(e, j.cast_names)
                if who in askers and re.search(r"flee|slips away|holds back", e):
                    bad.append(e)
    _fail(bad, "someone who just promised to stand with you runs")


def test_no_npc_line_repeats_within_six_turns(journeys):
    bad = []
    for j in journeys:
        last: dict[tuple[str, str], int] = {}
        for i, p in enumerate(j.pending):
            for b in p.scene.beats:
                if b.kind != "dialogue" or not b.speaker or b.speaker == "a grounding voice":
                    continue
                m = re.search(r"\"[^\"]+\"", b.text)
                if not m:
                    continue
                key = (b.speaker, m.group(0))
                if key in last and i - last[key] < 6:
                    bad.append(f"{b.speaker} at turns {last[key]} and {i}: {m.group(0)}")
                last[key] = i
    _fail(bad, "an NPC repeated a line within six turns")


def test_shadow_line_never_repeats_within_six_turns(journeys):
    saw_shadow = False
    for j in journeys:
        last: dict[str, int] = {}
        for i, p in enumerate(j.pending):
            for b in p.scene.beats:
                if b.archetype == Archetype.SHADOW and b.kind == "dialogue":
                    saw_shadow = True
                    line = re.search(r"\"[^\"]+\"", b.text)
                    if line:
                        k = line.group(0)
                        assert k not in last or i - last[k] >= 6, (k, last[k], i)
                        last[k] = i
    assert saw_shadow


def test_reflective_choice_rotates(journeys):
    for j in journeys:
        refl = []
        for p in j.pending:
            r = [c for c in p.scene.choices if classify_stance(c) == "reflect"
                 or c.startswith(("Choose the", "Choose one", "Decide on"))]
            refl.append(r[-1] if r else None)
        for a, b, c in zip(refl, refl[1:], refl[2:]):
            assert not (a is not None and a == b == c), a


def test_no_npc_shares_the_protagonists_name(journeys):
    # "Mara" is in the Shadow's name bank; a protagonist called Mara never meets her.
    assert any(j.protagonist == "Mara" for j in journeys)
    for j in journeys:
        assert j.protagonist.lower() not in {n.lower() for n in j.cast_names}


def test_realised_goal_reports_complete(journeys):
    seen = False
    for j in journeys:
        for g in j.state["goals"]:
            if g["realised"]:
                seen = True
                assert g["progress"] == 1.0 and 0.0 < g["learned_progress"] <= 1.0
    assert seen


def test_insights_are_warm_sentences(journeys):
    for j in journeys:
        for x in j.insights:
            assert x[:1].isupper() and x.rstrip()[-1:] in ".!?", x
            assert "Growing in" not in x and "encounters" not in x


# ─────────────────────────────────────────────────────────────
# After distress, the story is gentle
# ─────────────────────────────────────────────────────────────

def test_gentle_turn_after_concern():
    rt = JourneyRuntime.start(ONBOARDED, identity_id="prose-gentle", shard_db=None,
                              use_llm=False, seed_value=3, max_cycles=30)
    try:
        for _ in range(2):
            rt.pending()
            rt.act(UserAction(raw="", choice_index=0))
        p = rt.pending()
        cast = list(rt.engine.npc.cast.values())
        rec = rt.act(UserAction(raw="I feel so hopeless and worthless lately"), gentle=True)

        # Not read as a setback: no avoidance, no penalty, no "turned away" lesson.
        assert rec.outcome.success is not False
        assert all(v > 0 for v in rec.outcome.trait_updates.values())
        assert not (rec.outcome.lesson and "turned away" in rec.outcome.lesson)
        # Only companions react, and only warmly.
        for e in rec.outcome.npc_events:
            who = next(m for m in cast if e.lower().startswith(m.name.lower()))
            assert who.npc.archetype not in (Archetype.SHADOW, Archetype.TRICKSTER)
            assert not re.search(r"flee|cold|fail|mock|price|demand|blocks", e), e

        nxt = rt.pending()
        assert nxt.scene.intensity <= 0.25 + 1e-9
        req = rt.engine.pending.request
        assert "keep gentle" in req.constraints and "offer hope" in req.constraints
        assert req.target_intensity <= 0.25
        voices = [b for b in nxt.scene.beats if b.kind == "dialogue"
                  and b.archetype in (Archetype.SHADOW, Archetype.TRICKSTER)]
        assert not voices, [b.text for b in voices]
        assert all(classify_stance(c) != "avoid" for c in nxt.scene.choices)
        assert p.stage == nxt.stage or STAGES.index(nxt.stage) >= STAGES.index(p.stage)
    finally:
        rt.close()


def test_gentle_flag_defaults_off_and_matches_plain_act(tmp_path):
    a = JourneyRuntime.start(ONBOARDED, identity_id="prose-a", shard_db=None,
                             use_llm=False, seed_value=9, max_cycles=30)
    b = JourneyRuntime.start(ONBOARDED, identity_id="prose-b", shard_db=None,
                             use_llm=False, seed_value=9, max_cycles=30)
    try:
        for _ in range(3):
            pa, pb = a.pending(), b.pending()
            assert pa.scene.title == pb.scene.title
            ra = a.act(UserAction(raw="", choice_index=0))
            rb = b.act(UserAction(raw="", choice_index=0), gentle=False)
            assert ra.outcome.npc_events == rb.outcome.npc_events
    finally:
        a.close()
        b.close()
