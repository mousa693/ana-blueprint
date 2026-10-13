"""Journal — the Identity Core's record of its own journey, and its meta-analysis.

The blueprint's Memory Shard System includes a *Meta-Analysis*: "an algorithm
that looks at all shards to date and identifies trends: e.g. 'User has
consistently improved on courage but still struggles with trust.'" It notes this
can be "a simpler rules engine looking at tags" — which is what this is.

Every cycle the Identity Core writes one `JournalEntry` (tick, themes touched,
whether the moment was met, the lesson, symbols present, trait deltas, emotion
after the cycle). `Journal.meta_analysis()` reads the whole journal back and
returns short, honest, deterministic trend statements the Generative Engine can
use to shape the narrative arc; `recurring_symbols()` and `callback_symbols()`
let the system deliberately bring a meaningful motif back (the fox callback).

Pure stdlib, deterministic, no LLM.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class JournalEntry:
    tick: int
    themes: list[str] = field(default_factory=list)
    success: Optional[bool] = None
    lesson: Optional[str] = None
    symbols: list[str] = field(default_factory=list)
    trait_deltas: dict[str, float] = field(default_factory=dict)
    valence: float = 0.0
    arousal: float = 0.0
    stress: float = 0.0
    emotion: str = ""
    goals_advanced: list[str] = field(default_factory=list)
    intensity: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_symbol(s: str) -> str:
    return " ".join(str(s).strip().lower().split())


def _with_article(sym: str) -> str:
    if sym.startswith(("the ", "a ", "an ")):
        return sym
    return f"the {sym}"


class Journal:
    """Per-cycle diary of the self, with rules-based meta-analysis."""

    def __init__(self) -> None:
        self.entries: list[JournalEntry] = []

    # -- writing ---------------------------------------------------------
    def record(self, *, tick: int, themes: list[str] | None = None,
               success: Optional[bool] = None, lesson: Optional[str] = None,
               symbols: list[str] | None = None,
               trait_deltas: dict[str, float] | None = None,
               valence: float = 0.0, arousal: float = 0.0, stress: float = 0.0,
               emotion: str = "", goals_advanced: list[str] | None = None,
               intensity: Optional[float] = None) -> JournalEntry:
        entry = JournalEntry(
            tick=tick,
            themes=list(dict.fromkeys(t for t in (themes or []) if t)),
            success=success,
            lesson=lesson,
            symbols=list(dict.fromkeys(_norm_symbol(s) for s in (symbols or []) if str(s).strip())),
            trait_deltas=dict(trait_deltas or {}),
            valence=valence, arousal=arousal, stress=stress, emotion=emotion,
            goals_advanced=list(goals_advanced or []),
            intensity=intensity,
        )
        self.entries.append(entry)
        return entry

    def __len__(self) -> int:
        return len(self.entries)

    # -- reading ---------------------------------------------------------
    def theme_record(self) -> dict[str, dict]:
        """Per theme: met / avoided / neutral counts, total, first and last tick,
        and the outcome sequence in order (True/False/None)."""
        rec: dict[str, dict] = {}
        for e in self.entries:
            for t in e.themes:
                r = rec.setdefault(t, {"met": 0, "avoided": 0, "neutral": 0, "total": 0,
                                       "first_tick": e.tick, "last_tick": e.tick,
                                       "sequence": []})
                key = "met" if e.success is True else "avoided" if e.success is False else "neutral"
                r[key] += 1
                r["total"] += 1
                r["last_tick"] = e.tick
                r["sequence"].append(e.success)
        return rec

    def recurring_symbols(self, min_count: int = 2) -> list[tuple[str, int]]:
        """Symbols seen in at least `min_count` cycles, most frequent first
        (ties broken by first appearance)."""
        counts: Counter[str] = Counter()
        first: dict[str, int] = {}
        for i, e in enumerate(self.entries):
            for s in e.symbols:
                counts[s] += 1
                first.setdefault(s, i)
        items = [(s, c) for s, c in counts.items() if c >= min_count]
        items.sort(key=lambda sc: (-sc[1], first[sc[0]]))
        return items

    def callback_symbols(self, *, min_gap: int = 3, limit: int = 3) -> list[str]:
        """Meaningful symbols that have been absent for a while — candidates for a
        deliberate callback (the blueprint's fox returning in the city).

        A symbol is *meaningful* if it was present in a cycle where a challenge
        was met or a lesson was learned, or if it recurred. It is a callback
        candidate when it has not appeared in the last `min_gap` cycles.
        """
        if not self.entries:
            return []
        n = len(self.entries)
        last_seen: dict[str, int] = {}
        weight: Counter[str] = Counter()
        for i, e in enumerate(self.entries):
            meaningful = e.success is True or bool(e.lesson)
            for s in e.symbols:
                last_seen[s] = i
                weight[s] += 2 if meaningful else 1
        cands = [s for s, i in last_seen.items() if (n - 1 - i) >= min_gap and weight[s] >= 2]
        cands.sort(key=lambda s: (-weight[s], last_seen[s]))
        return cands[:limit]

    def suggested_focus(self) -> Optional[str]:
        """The theme the self most struggles with — a candidate for a 'grand test'."""
        worst, worst_key = None, None
        for t, r in self.theme_record().items():
            if r["avoided"] == 0:
                continue
            key = (r["avoided"] - r["met"], r["avoided"], r["last_tick"])
            if worst_key is None or key > worst_key:
                worst, worst_key = t, key
        return worst

    # -- meta-analysis ---------------------------------------------------
    def meta_analysis(self, *, limit: Optional[int] = None) -> list[str]:
        """Short, honest trend statements over the whole journal, most salient
        first. Deterministic rules over tags — no model involved."""
        scored: list[tuple[float, str]] = []
        rec = self.theme_record()

        growing: list[str] = []
        struggling: list[str] = []
        for theme, r in rec.items():
            n, met, avoided = r["total"], r["met"], r["avoided"]
            if n < 2:
                continue
            seq = r["sequence"]
            if met >= 2 and met / n >= 0.6:
                growing.append(theme)
                scored.append((2.0 + met / n + 0.1 * n,
                               f"growing in {theme} ({met} of {n} encounters met)"))
            elif avoided >= 2 and avoided / n >= 0.6:
                struggling.append(theme)
                scored.append((2.0 + avoided / n + 0.1 * n,
                               f"repeatedly turning from {theme} ({met} of {n} met)"))
            elif met and avoided:
                # A turnaround: the latest two were met after earlier avoidance.
                if len(seq) >= 3 and seq[-1] is True and seq[-2] is True and False in seq[:-2]:
                    growing.append(theme)
                    scored.append((2.2, f"turning toward {theme} after earlier avoidance "
                                        f"({met} of {n} met)"))
                elif seq[-1] is False and True in seq[:-1]:
                    struggling.append(theme)
                    scored.append((1.8, f"slipping on {theme} after earlier progress "
                                        f"({met} of {n} met)"))
                else:
                    scored.append((1.2, f"still wrestling with {theme} "
                                        f"({met} met, {avoided} turned from, of {n})"))
            elif r["neutral"] >= 2 and r["neutral"] / n >= 0.6:
                scored.append((0.8, f"circling {theme} without resolving it "
                                    f"({r['neutral']} of {n} undecided)"))

        # The blueprint's canonical shape: improving on X but still struggling with Y.
        if growing and struggling:
            scored.append((3.5, f"consistently improving on {growing[0]} but still "
                                f"struggling with {struggling[0]}"))

        # Recurring symbols.
        for sym, c in self.recurring_symbols(min_count=3)[:2]:
            scored.append((1.5 + 0.1 * c, f"{_with_article(sym)} keeps returning ({c} appearances)"))

        # Emotional trajectories over the most recent run of cycles.
        for attr, up, down in (("stress", "climbing", "easing"),
                               ("valence", "brightening", "darkening")):
            k, direction = self._run_length(attr)
            if k >= 3:
                word = up if direction > 0 else down
                label = "stress" if attr == "stress" else "mood"
                weight = 2.6 if (attr == "stress" and direction > 0) else 1.4
                scored.append((weight + 0.1 * k, f"{label} has been {word} for {k} cycles"))

        # Traits that have moved most across the journey.
        totals: dict[str, float] = {}
        counts: Counter[str] = Counter()
        for e in self.entries:
            for name, d in e.trait_deltas.items():
                totals[name] = totals.get(name, 0.0) + d
                counts[name] += 1
        for name, tot in sorted(totals.items(), key=lambda kv: -abs(kv[1]))[:2]:
            if abs(tot) >= 0.25 and counts[name] >= 2:
                verb = "has strengthened" if tot > 0 else "has weakened"
                scored.append((1.0 + abs(tot), f"{name.replace('_', ' ')} {verb} "
                                               f"({tot:+.2f} over {counts[name]} cycles)"))

        # Lessons accumulated.
        lessons = [e.lesson for e in self.entries if e.lesson]
        if len(lessons) >= 3:
            scored.append((0.6, f"{len(lessons)} lessons carried so far"))

        # Dormant themes: once present, now silent for a while.
        if self.entries:
            now = self.entries[-1].tick
            for theme, r in rec.items():
                gap = now - r["last_tick"]
                if gap >= 5 and r["total"] >= 1:
                    scored.append((0.9, f"{theme} has gone quiet ({gap} cycles since last faced)"))

        scored.sort(key=lambda ws: -ws[0])  # stable: ties keep insertion order
        out = list(dict.fromkeys(s for _, s in scored))
        return out[:limit] if limit else out

    def _run_length(self, attr: str) -> tuple[int, int]:
        """Length of the current strictly monotone run of `attr` (in steps), and
        its direction (+1 rising, -1 falling)."""
        vals = [getattr(e, attr) for e in self.entries]
        if len(vals) < 2:
            return 0, 0
        eps = 1e-9
        last = vals[-1] - vals[-2]
        if abs(last) <= eps:
            return 0, 0
        direction = 1 if last > 0 else -1
        k = 0
        for i in range(len(vals) - 1, 0, -1):
            d = vals[i] - vals[i - 1]
            if (d > eps and direction > 0) or (d < -eps and direction < 0):
                k += 1
            else:
                break
        return k, direction

    # -- serialisation ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [e.to_dict() for e in self.entries],
            "theme_record": {t: {k: v for k, v in r.items() if k != "sequence"}
                             for t, r in self.theme_record().items()},
            "recurring_symbols": [list(sc) for sc in self.recurring_symbols()],
            "meta_analysis": self.meta_analysis(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Journal":
        j = cls()
        for e in data.get("entries", []):
            j.entries.append(JournalEntry(**e))
        return j
