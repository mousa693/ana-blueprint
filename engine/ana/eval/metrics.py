"""Pure metric functions over a journey (a list of CycleRecord).

Every function here only *reads* records, and does so defensively (getattr with
defaults) so the metrics keep working while the contracts gain optional fields.
All functions return flat dicts of floats/ints (plus a few small lists) so they
can be aggregated, diffed and dumped to JSON.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence

# Hero's-journey stages, in canonical order, with the labels used to detect
# them. Full labels are matched first anywhere in title/summary; bare keywords
# only in the title (a summary like "an unmarked threshold" is a location).
STAGES: list[str] = ["call", "threshold", "mentor", "ordeal", "reward", "return"]
_STAGE_LABELS: dict[str, tuple[str, ...]] = {
    "call": ("call to adventure",),
    "threshold": ("crossing the threshold", "cross the threshold"),
    "mentor": ("meeting the mentor", "the mentor"),
    "ordeal": ("the ordeal", "ordeal"),
    "reward": ("seizing the reward", "the reward"),
    "return": ("the return", "road back", "homeward"),
}
_STAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "call": ("call to adventure", "the call"),
    "threshold": ("threshold",),
    "mentor": ("mentor",),
    "ordeal": ("ordeal",),
    "reward": ("reward",),
    "return": ("return",),
}


def _g(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _f(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _ratio(n: float, d: float, default: float = 0.0) -> float:
    return float(n) / float(d) if d else default


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Pearson correlation; 0.0 when undefined (n<2 or zero variance)."""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0
    xs, ys = list(xs)[:n], list(ys)[:n]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return 0.0
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


# ─────────────────────────────────────────────────────────────
# Convergence
# ─────────────────────────────────────────────────────────────

def convergence_series(records: Sequence[Any]) -> list[float]:
    return [_f(_g(r, "convergence", 0.0)) for r in records]


def _initial_convergence(records: Sequence[Any]) -> float:
    if not records:
        return 0.0
    return _f(_g(_g(records[0], "identity_before"), "convergence", 0.0))


def convergence_deltas(records: Sequence[Any]) -> list[float]:
    """Per-cycle change in convergence (cycle 0 vs the identity's starting value)."""
    series = convergence_series(records)
    prev = _initial_convergence(records)
    out = []
    for c in series:
        out.append(c - prev)
        prev = c
    return out


def convergence_metrics(records: Sequence[Any], recovery_window: int = 3) -> dict:
    s = convergence_series(records)
    if not s:
        return {"conv_final": 0.0, "conv_max": 0.0, "conv_auc": 0.0,
                "conv_monotonicity": 0.0, "conv_dips": 0, "conv_dips_recovered": 0,
                "conv_recovery_rate": 0.0}
    deltas = convergence_deltas(records)
    steps = deltas[1:] if len(deltas) > 1 else deltas
    monotonic = _ratio(sum(1 for d in steps if d >= -1e-9), len(steps), 1.0)
    # A dip starts at a step i where s[i] < s[i-1] and s[i-1] was not itself a dip
    # step. Recovered if s regains >= s[i-1] within `recovery_window` cycles.
    dips = recovered = 0
    for i in range(1, len(s)):
        if s[i] < s[i - 1] - 1e-9 and not (i >= 2 and s[i - 1] < s[i - 2] - 1e-9):
            dips += 1
            pre = s[i - 1]
            window = s[i + 1:i + 1 + recovery_window]
            if any(v >= pre - 1e-9 for v in window):
                recovered += 1
    auc = sum(s) / len(s)  # normalised area under the curve (mean level)
    return {
        "conv_final": s[-1],
        "conv_max": max(s),
        "conv_auc": auc,
        "conv_monotonicity": monotonic,
        "conv_dips": dips,
        "conv_dips_recovered": recovered,
        "conv_recovery_rate": _ratio(recovered, dips, 1.0),
    }


# ─────────────────────────────────────────────────────────────
# Earnedness
# ─────────────────────────────────────────────────────────────

def success_values(records: Sequence[Any]) -> list[float]:
    """+1 challenge met, -1 avoided, 0 neutral / unknown."""
    out = []
    for r in records:
        s = _g(_g(r, "outcome"), "success", None)
        out.append(1.0 if s is True else (-1.0 if s is False else 0.0))
    return out


def earnedness_metrics(records: Sequence[Any]) -> dict:
    succ = success_values(records)
    d = convergence_deltas(records)
    met = [dd for ss, dd in zip(succ, d) if ss > 0]
    avoided = [dd for ss, dd in zip(succ, d) if ss < 0]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return {
        "earned_corr": pearson(succ, d),
        "earned_gap": (mean(met) - mean(avoided)) if (met and avoided) else 0.0,
        "dconv_when_met": mean(met),
        "dconv_when_avoided": mean(avoided),
        "success_rate": _ratio(sum(1 for x in succ if x > 0), len(succ)),
        "avoid_rate": _ratio(sum(1 for x in succ if x < 0), len(succ)),
    }


# ─────────────────────────────────────────────────────────────
# Journey
# ─────────────────────────────────────────────────────────────

def infer_stage(scene: Any) -> Optional[str]:
    """Infer a Hero's-journey stage key from a scene's title/summary, or None."""
    title = str(_g(scene, "title", "") or "").lower()
    summary = str(_g(scene, "summary", "") or "").lower()
    explicit = _g(scene, "stage", None)  # tolerate a future explicit field
    if isinstance(explicit, str) and explicit.lower() in STAGES:
        return explicit.lower()
    text = title + " \n " + summary
    hits = []
    for st, labels in _STAGE_LABELS.items():
        for lab in labels:
            i = text.find(lab)
            if i >= 0:
                hits.append((i, st))
                break
    if hits:
        return min(hits)[1]  # the earliest-mentioned label wins
    for st, kws in _STAGE_KEYWORDS.items():
        if any(k in title for k in kws):
            return st
    return None


def journey_metrics(records: Sequence[Any]) -> dict:
    stages = [infer_stage(_g(r, "scene")) for r in records]
    idx = [STAGES.index(s) for s in stages if s is not None]
    max_back = 0
    for a, b in zip(idx, idx[1:]):
        max_back = max(max_back, a - b)
    locations = []
    for r in records:
        loc = _g(_g(_g(r, "request"), "world"), "location", None)
        if loc:
            locations.append(str(loc))
    return {
        "journey_stage_seq": [s or "?" for s in stages],
        "journey_distinct_stages": len(set(idx)),
        "journey_max_stage": max(idx) if idx else -1,
        "journey_reached_ordeal": int(bool(idx) and max(idx) >= STAGES.index("ordeal")),
        "journey_in_order": int(all(b >= a for a, b in zip(idx, idx[1:]))),
        "journey_max_backjump": max_back,
        "journey_unlabeled": sum(1 for s in stages if s is None),
        "distinct_locations": len(set(locations)),
    }


# ─────────────────────────────────────────────────────────────
# Variety / repetition
# ─────────────────────────────────────────────────────────────

def _beats(scene: Any) -> list[str]:
    return [str(_g(b, "text", "") or "") for b in (_g(scene, "beats", None) or [])]


def _scene_text(scene: Any) -> str:
    return "\n".join([str(_g(scene, "title", "") or ""), str(_g(scene, "summary", "") or "")]
                     + _beats(scene))


def variety_metrics(records: Sequence[Any]) -> dict:
    n = len(records)
    texts = [_scene_text(_g(r, "scene")) for r in records]
    # Repeated beats: beats whose exact text already appeared in an earlier cycle.
    seen: set[str] = set()
    total_beats = repeated = 0
    for r in records:
        bt = [t.strip() for t in _beats(_g(r, "scene")) if t.strip()]
        total_beats += len(bt)
        repeated += sum(1 for t in bt if t in seen)
        seen.update(bt)
    # Nudges.
    nudge_lists = [list(_g(_g(r, "alignment"), "nudges", None) or []) for r in records]
    all_nudges = [x for lst in nudge_lists for x in lst]
    consec = sum(1 for a, b in zip(nudge_lists, nudge_lists[1:]) if a and a == b)
    # Theme coverage.
    initial = []
    if records:
        initial = [str(t).lower() for t in
                   (_g(_g(records[0], "identity_before"), "active_themes", None) or [])]
    scene_themes = {str(_g(_g(r, "scene"), "theme", "") or "").lower() for r in records}
    covered = [t for t in initial if t in scene_themes]
    return {
        "distinct_scene_ratio": _ratio(len(set(texts)), n),
        "repeated_beat_ratio": _ratio(repeated, total_beats),
        "distinct_nudge_ratio": _ratio(len(set(all_nudges)), len(all_nudges), 1.0),
        "consecutive_identical_nudges": consec,
        "theme_coverage": _ratio(len(covered), len(initial), 1.0),
        "themes_missed": [t for t in initial if t not in scene_themes],
        "distinct_scene_themes": len(scene_themes - {""}),
    }


# ─────────────────────────────────────────────────────────────
# Growth
# ─────────────────────────────────────────────────────────────

def _traits(snap: Any) -> dict[str, float]:
    out = {}
    for t in (_g(snap, "traits", None) or []):
        name = _g(t, "name", None)
        if name:
            out[str(name)] = _f(_g(t, "value", 0.0))
    return out


def growth_metrics(records: Sequence[Any]) -> dict:
    if not records:
        return {"trait_deltas": {}, "trait_delta_sum": 0.0, "trait_delta_abs": 0.0,
                "goal_progress": [], "goal_progress_mean": 0.0, "lessons": 0,
                "distinct_lesson_ratio": 0.0, "final_valence": 0.0, "final_stress": 0.0}
    first = _traits(_g(records[0], "identity_before"))
    last = _traits(_g(records[-1], "identity_before"))
    # Fold in the final cycle's own trait updates (not yet in any snapshot).
    for k, dv in (_g(_g(records[-1], "outcome"), "trait_updates", None) or {}).items():
        last[k] = max(0.0, min(1.0, last.get(k, first.get(k, 0.0)) + _f(dv)))
    deltas = {k: round(last.get(k, 0.0) - first.get(k, 0.0), 4)
              for k in sorted(set(first) | set(last))}
    goals = [round(_f(_g(gl, "progress", 0.0)), 4)
             for gl in (_g(_g(records[-1], "identity_before"), "goals", None) or [])]
    lessons = [str(_g(_g(r, "outcome"), "lesson")) for r in records
               if _g(_g(r, "outcome"), "lesson", None)]
    emo = _g(_g(records[-1], "identity_before"), "emotion")
    return {
        "trait_deltas": deltas,
        "trait_delta_sum": sum(deltas.values()),
        "trait_delta_abs": sum(abs(v) for v in deltas.values()),
        "goal_progress": goals,
        "goal_progress_mean": sum(goals) / len(goals) if goals else 0.0,
        "lessons": len(lessons),
        "distinct_lesson_ratio": _ratio(len(set(lessons)), len(lessons), 1.0),
        "final_valence": _f(_g(emo, "valence", 0.0)),
        "final_stress": _f(_g(emo, "stress", 0.0)),
    }


# ─────────────────────────────────────────────────────────────
# Safety
# ─────────────────────────────────────────────────────────────

def _matches_forbidden(cand: str, forbidden: Iterable[str]) -> bool:
    low = cand.lower().strip()
    if not low:
        return False
    for f in forbidden:
        f = (f or "").lower().strip()
        if f and (f in low or low in f):
            return True
    return False


def safety_metrics(records: Sequence[Any], forbidden: Sequence[str] = (),
                   stress_threshold: float = 0.7) -> dict:
    forbidden_hits = blocked = visions = 0
    max_int_stressed = 0.0
    stressed_cycles = 0
    for r in records:
        sc = _g(r, "scene")
        cands = [str(_g(sc, "theme", "") or "")] + [str(s) for s in (_g(sc, "symbols", None) or [])]
        if forbidden and any(_matches_forbidden(c, forbidden) for c in cands):
            forbidden_hits += 1
        al = _g(r, "alignment")
        if _g(al, "blocked", False) or _g(al, "block_reason", None):
            blocked += 1
        if _g(sc, "is_vision", False) or _g(_g(r, "harmonizer"), "is_vision", False):
            visions += 1
        stress = _f(_g(_g(_g(r, "identity_before"), "emotion"), "stress", 0.0))
        if stress > stress_threshold:
            stressed_cycles += 1
            max_int_stressed = max(max_int_stressed, _f(_g(sc, "intensity", 0.0)))
    return {
        "forbidden_scenes": forbidden_hits,
        "blocked": blocked,
        "stressed_cycles": stressed_cycles,
        "max_intensity_when_stressed": max_int_stressed,
        "vision_flags": visions,
    }


# ─────────────────────────────────────────────────────────────

def compute_metrics(records: Sequence[Any], forbidden: Sequence[str] = ()) -> dict:
    """All metrics for one journey as one flat dict."""
    out: dict = {"cycles": len(records)}
    out.update(convergence_metrics(records))
    out.update(earnedness_metrics(records))
    out.update(journey_metrics(records))
    out.update(variety_metrics(records))
    out.update(growth_metrics(records))
    out.update(safety_metrics(records, forbidden))
    return out
