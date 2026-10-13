"""Run ANA journeys under fixed behavioural profiles and aggregate metrics.

Profiles (all deterministic given a seed):

  engaged     always picks the most engaging offered choice
  avoidant    always picks the most avoidant choice (free-text withdrawal if none)
  mixed       per cycle, a seeded coin decides engaged vs avoidant
  autonomous  the engine's own AutonomousDriver
  lighthouse  the CLI demo script (five scripted actions, then first choice)
"""
from __future__ import annotations

import random
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

from ..config import ANAConfig
from ..contracts import CycleRecord, IdentitySnapshot, Scene, UserAction
from ..identity.core import IdentityCore
from ..identity.memory import ShardMemory
from ..session import build_session
from .metrics import STAGES, compute_metrics, convergence_deltas, pearson, success_values

PROFILES: list[str] = ["engaged", "avoidant", "mixed", "autonomous", "lighthouse"]

DEFAULT_FORBIDDEN: list[str] = ["drowning", "abandonment"]

LIGHTHOUSE_SCRIPT: list[str] = [
    "I explore the town and talk to the fishermen.",
    "I climb toward the lighthouse to see what's wrong.",
    "I organize a rescue and signal the stranded ship.",
    "I speak honestly to the council about what happened.",
    "I accept the role they offer, uneasy but willing.",
]


def seed_identity() -> IdentityCore:
    """The blueprint's lighthouse protagonist (mirrors the CLI demo seed)."""
    ic = IdentityCore(name="the wanderer", memory=ShardMemory(db_path=None))
    ic.set_trait("leadership_confidence", 0.25)
    ic.set_trait("honesty", 0.8)
    ic.set_trait("courage", 0.4)
    ic.add_belief("I am not the kind of person others follow.")
    ic.add_goal("find my purpose", horizon="life", is_future_memory=True)
    ic.add_goal("become someone who can lead", horizon="mid", is_future_memory=True)
    ic.seed_memory("A childhood by a lighthouse on a grey coast.",
                   themes=["purpose", "courage"], symbols=["lighthouse"])
    ic.active_themes = ["leadership", "purpose", "courage"]
    ic.emotion.valence = -0.1
    ic.emotion.arousal = 0.45
    ic.emotion.dominant = ic.emotion.dominant.CURIOSITY
    return ic


# ─────────────────────────────────────────────────────────────
# Eval drivers
# ─────────────────────────────────────────────────────────────

_ENGAGE = ("answer", "step across", "accept", "face", "take", "share", "carry",
           "help", "lead", "climb", "save", "organi", "speak", "trust", "confront",
           "protect", "try", "offer", "act", "stand", "enter", "go ", "join",
           "fight", "decide", "choose", "build", "forgive")
_AVOID = ("hesitate", "turn back", "turn away", "run", "flee", "hide", "ignore",
          "refuse", "leave", "avoid", "wait", "rest", "look back", "decline",
          "retreat", "withdraw", "give up", "abandon", "back away", "stay away")
_SOFT_AVOID = ("pause", "gentlest", "question", "only what", "observe", "watch")


def engagement_score(choice: str) -> float:
    """>0 engaging, <0 avoidant. Keyword heuristic independent of the engine."""
    c = " " + choice.lower() + " "
    s = sum(1.0 for w in _ENGAGE if w in c)
    s -= sum(1.0 for w in _AVOID if w in c)
    s -= sum(0.5 for w in _SOFT_AVOID if w in c)
    return s


class StanceDriver:
    """Picks offered choices according to a fixed stance.

    stance="engaged": highest engagement score. stance="avoidant": lowest; if no
    offered choice is actually avoidant, it withdraws in free text (so the
    interpreter cannot read a bare choice index as engagement).
    stance="mixed": a seeded coin per cycle picks one of the two.
    """

    ENGAGE_FALLBACK = "I step forward and face it directly."
    AVOID_FALLBACK = "I hold back, turn away and leave it for now."

    def __init__(self, stance: str = "engaged", seed: int = 0, p_engage: float = 0.5):
        assert stance in ("engaged", "avoidant", "mixed")
        self.stance = stance
        self.rng = random.Random(seed)
        self.p_engage = p_engage
        self.history: list[str] = []

    def act(self, scene: Scene, identity: IdentitySnapshot) -> UserAction:
        stance = self.stance
        if stance == "mixed":
            stance = "engaged" if self.rng.random() < self.p_engage else "avoidant"
        self.history.append(stance)
        choices = list(getattr(scene, "choices", None) or [])
        if not choices:
            return UserAction(raw=self.ENGAGE_FALLBACK if stance == "engaged"
                              else self.AVOID_FALLBACK)
        scores = [engagement_score(c) for c in choices]
        if stance == "engaged":
            idx = max(range(len(choices)), key=lambda i: (scores[i], -i))
            if scores[idx] <= 0:
                return UserAction(raw=self.ENGAGE_FALLBACK)
        else:
            idx = min(range(len(choices)), key=lambda i: (scores[i], i))
            if scores[idx] >= 0:
                return UserAction(raw=self.AVOID_FALLBACK)
        return UserAction(raw=choices[idx], choice_index=idx)


def _make_driver(profile: str, seed: int):
    from ..engine.drivers import ScriptedDriver
    if profile in ("engaged", "avoidant", "mixed"):
        return StanceDriver(profile, seed=seed)
    if profile == "lighthouse":
        return ScriptedDriver(LIGHTHOUSE_SCRIPT)
    if profile == "autonomous":
        return None  # build_session's default AutonomousDriver
    raise ValueError(f"unknown profile {profile!r}; choose from {PROFILES}")


def run_journey(profile: str, seed: int, cycles: int, use_llm: bool = False,
                forbidden: Optional[Sequence[str]] = None) -> list[CycleRecord]:
    """Run one journey from the lighthouse identity under a behavioural profile."""
    data_dir = Path(tempfile.gettempdir()) / "ana_eval_run"
    cfg = ANAConfig(use_llm=use_llm, max_cycles=cycles, seed=seed,
                    forbidden_themes=list(DEFAULT_FORBIDDEN if forbidden is None else forbidden),
                    data_dir=data_dir, shard_db=str(data_dir / "shards.db"))
    sess = build_session(cfg, identity=seed_identity(), driver=_make_driver(profile, seed))
    try:
        return list(sess.run(cycles))
    finally:
        try:
            sess.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _agg(runs: list[dict]) -> dict:
    keys = [k for k in runs[0] if _is_num(runs[0][k])] if runs else []
    out = {}
    for k in keys:
        vals = [float(r[k]) for r in runs if _is_num(r.get(k))]
        if vals:
            out[k] = {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals)}
    return out


def evaluate(profiles: Sequence[str] = PROFILES, seeds: Sequence[int] | int = 5,
             cycles: int = 16, use_llm: bool = False,
             forbidden: Optional[Sequence[str]] = None) -> dict:
    """Run every profile x seed and aggregate metrics (mean/min/max per metric)."""
    if isinstance(seeds, int):
        seeds = list(range(1, seeds + 1))
    forbidden = list(DEFAULT_FORBIDDEN if forbidden is None else forbidden)
    result: dict = {"config": {"profiles": list(profiles), "seeds": list(seeds),
                               "cycles": cycles, "use_llm": use_llm,
                               "forbidden": forbidden},
                    "profiles": {}}
    pooled_s: list[float] = []
    pooled_d: list[float] = []
    for p in profiles:
        runs = []
        for s in seeds:
            recs = run_journey(p, s, cycles, use_llm=use_llm, forbidden=forbidden)
            m = compute_metrics(recs, forbidden)
            m["seed"] = s
            runs.append(m)
            pooled_s += success_values(recs)
            pooled_d += convergence_deltas(recs)
        result["profiles"][p] = {"agg": _agg(runs), "runs": runs}
    result["pooled"] = {"earned_corr": pearson(pooled_s, pooled_d), "n_cycles": len(pooled_s)}
    result["rubric"] = rubric(result)
    return result


def _m(result: dict, profile: str, metric: str, stat: str = "mean") -> Optional[float]:
    try:
        return result["profiles"][profile]["agg"][metric][stat]
    except KeyError:
        return None


def _over_profiles(result: dict, metric: str, stat: str) -> list[float]:
    return [v for p in result["profiles"]
            if (v := _m(result, p, metric, stat)) is not None]


def rubric(result: dict) -> list[dict]:
    """'What good looks like': each line {name, target, value, pass}."""
    lines = []

    def add(name, target, value, ok):
        lines.append({"name": name, "target": target,
                      "value": value if value is None else round(float(value), 4),
                      "pass": bool(ok)})

    cyc = result["config"]["cycles"]
    e, a = _m(result, "engaged", "conv_final"), _m(result, "avoidant", "conv_final")
    add("engaged final convergence > avoidant", "engaged - avoidant > 0",
        None if e is None or a is None else e - a, e is not None and a is not None and e > a)
    pc = result.get("pooled", {}).get("earned_corr")
    add("earnedness: corr(success, dConv) pooled over all runs", "> 0.3", pc,
        pc is not None and pc > 0.3)
    ro = _m(result, "engaged", "journey_reached_ordeal", "min")
    if ro is None:
        ro = min(_over_profiles(result, "journey_reached_ordeal", "min") or [0.0])
    add(f"journey reaches ordeal or beyond within {cyc} cycles (engaged, every seed)",
        "== 1", ro, ro >= 1)
    cn = max(_over_profiles(result, "consecutive_identical_nudges", "max") or [0.0])
    add("consecutive identical nudges (worst run)", "== 0", cn, cn == 0)
    rb = max(_over_profiles(result, "repeated_beat_ratio", "mean") or [0.0])
    add("repeated-beat ratio (worst profile mean)", "< 0.2", rb, rb < 0.2)
    tc = min(_over_profiles(result, "theme_coverage", "min") or [0.0])
    add("theme coverage (worst run)", "== 1.0", tc, tc >= 1.0 - 1e-9)
    fs = max(_over_profiles(result, "forbidden_scenes", "max") or [0.0])
    add("forbidden-theme scenes (worst run)", "== 0", fs, fs == 0)
    return lines


# ─────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────

KEY_METRICS: list[str] = [
    "conv_final", "conv_max", "conv_auc", "conv_monotonicity", "conv_dips",
    "conv_recovery_rate", "earned_corr", "earned_gap", "success_rate",
    "journey_distinct_stages", "journey_max_stage", "journey_reached_ordeal",
    "journey_in_order", "journey_max_backjump", "distinct_locations",
    "distinct_scene_ratio", "repeated_beat_ratio", "distinct_nudge_ratio",
    "consecutive_identical_nudges", "theme_coverage", "trait_delta_sum",
    "goal_progress_mean", "lessons", "distinct_lesson_ratio", "final_stress",
    "forbidden_scenes", "blocked", "max_intensity_when_stressed", "vision_flags",
]


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "–"
    if abs(v - round(v)) < 1e-9 and abs(v) >= 1:
        return str(int(round(v)))
    return f"{v:.3f}"


def rubric_markdown(result: dict) -> str:
    rows = ["| | Check | Target | Value |", "|---|---|---|---|"]
    for ln in result.get("rubric", []):
        rows.append(f"| **{'PASS' if ln['pass'] else 'FAIL'}** | {ln['name']} | "
                    f"{ln['target']} | {_fmt(ln['value'])} |")
    return "\n".join(rows)


def metrics_markdown(result: dict, metrics: Sequence[str] = KEY_METRICS) -> str:
    profs = list(result["profiles"])
    rows = ["| metric | " + " | ".join(profs) + " |",
            "|---|" + "---|" * len(profs)]
    for k in metrics:
        cells = []
        for p in profs:
            st = result["profiles"][p]["agg"].get(k)
            cells.append("–" if st is None else
                         f"{_fmt(st['mean'])} [{_fmt(st['min'])}..{_fmt(st['max'])}]")
        rows.append(f"| {k} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def stage_markdown(result: dict) -> str:
    out = []
    for p, data in result["profiles"].items():
        for r in data["runs"][:2]:
            seq = r.get("journey_stage_seq") or []
            out.append(f"- `{p}` seed {r.get('seed')}: " + " → ".join(seq))
    return "\n".join(out)


def compare(before: dict, after: dict, metrics: Sequence[str] = KEY_METRICS) -> str:
    """Markdown table of mean metric values before vs after, per profile."""
    rows = ["| profile | metric | before | after | Δ |", "|---|---|---|---|---|"]
    for p in [p for p in after.get("profiles", {}) if p in before.get("profiles", {})]:
        for k in metrics:
            b, a = _m(before, p, k), _m(after, p, k)
            if b is None and a is None:
                continue
            d = (a - b) if (a is not None and b is not None) else None
            rows.append(f"| {p} | {k} | {_fmt(b)} | {_fmt(a)} | "
                        f"{'–' if d is None else f'{d:+.3f}'} |")
    rb = {ln["name"]: ln for ln in before.get("rubric", [])}
    rows.append("")
    rows.append("| rubric | before | after |")
    rows.append("|---|---|---|")
    for ln in after.get("rubric", []):
        b = rb.get(ln["name"])
        bs = "–" if b is None else ("PASS" if b["pass"] else "FAIL") + f" ({_fmt(b['value'])})"
        rows.append(f"| {ln['name']} | {bs} | "
                    f"{'PASS' if ln['pass'] else 'FAIL'} ({_fmt(ln['value'])}) |")
    return "\n".join(rows)


RUBRIC_INTRO = """\
## What good looks like

A healthy ANA journey is *earned*, *moving*, *varied* and *safe*:
convergence should rise because the protagonist meets challenges (not merely
because time passes), the Hero's journey should advance past the ordeal, the
world should not repeat itself, every active theme should be reflected, and no
forbidden theme should ever reach the protagonist.
"""


def report_markdown(result: dict, header: str = "# ANA evaluation report") -> str:
    cfg = result["config"]
    passed = sum(1 for ln in result["rubric"] if ln["pass"])
    parts = [
        header, "",
        f"Profiles: {', '.join(cfg['profiles'])} · seeds: {cfg['seeds']} · "
        f"cycles: {cfg['cycles']} · use_llm: {cfg['use_llm']} · "
        f"forbidden list: {cfg['forbidden']}", "",
        RUBRIC_INTRO,
        f"**Rubric: {passed}/{len(result['rubric'])} PASS**", "",
        rubric_markdown(result), "",
        f"Pooled earnedness correlation: {result['pooled']['earned_corr']:.3f} "
        f"over {result['pooled']['n_cycles']} cycles.", "",
        "## Metrics per profile (mean [min..max] over seeds)", "",
        metrics_markdown(result), "",
        "## Hero's-journey stage sequences (first two seeds)", "",
        f"Stages: {' → '.join(STAGES)} (`?` = no stage label found)", "",
        stage_markdown(result), "",
        "## Metric glossary", "",
        "- **conv_*:** final / max / mean level (AUC) of convergence; monotonicity = fraction of "
        "non-negative steps; dips = falls; recovery = regained pre-dip level within 3 cycles.",
        "- **earned_corr:** Pearson corr between outcome success (+1/-1/0) and that cycle's "
        "change in convergence (0 = time-driven). earned_gap = mean dConv when met − when avoided.",
        "- **journey_*:** stages inferred from scene title/summary; max_stage indexes "
        "call=0 … return=5; backjump = largest step backwards.",
        "- **repeated_beat_ratio:** fraction of beats whose exact text appeared in an earlier cycle.",
        "- **consecutive_identical_nudges:** cycles whose alignment nudge list equals the previous one.",
        "- **theme_coverage:** fraction of the initial active themes that became a scene theme.",
        "- **trait_delta_sum:** sum of trait changes first→last (incl. the last outcome).",
        "- **forbidden_scenes / blocked / vision_flags:** safety counts; "
        "max_intensity_when_stressed = max scene intensity while stress > 0.7.",
    ]
    return "\n".join(parts) + "\n"
