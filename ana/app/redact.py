"""Read-time redaction of a person's never-show words (forbidden themes).

Journey titles and debriefs are written when a journey ends; the person can
add a word to their never-show list afterwards. Everything served back to
them passes through here with the CURRENT list, so a change takes effect
immediately (the stored rows are left untouched; the export is the raw data).

Matching is the engine's own (``mentions_forbidden(..., synonyms=True)``):
the word itself (word-anchored prefix, "courage" → "courageous") and, via the
identity lexicon, its cue words ("someone who can lead" → leadership).
Replacement: the matched word → "what you carry" ("A journey through grief
and leadership" → "A journey through what you carry and leadership"); a
sentence or title clause that still mentions the theme after that (a looser
synonym) is dropped.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable, Optional

from ana.subsystems.alignment import forbidden_terms, mentions_forbidden

NEUTRAL = "what you carry"
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_TITLE_SPLIT = re.compile(r"\s+[—–-]\s+|:\s+|,\s+|;\s+|\s+\|\s+")


def _lexicon() -> dict:
    try:
        from ana.identity.core import THEME_LEXICON
        return THEME_LEXICON
    except Exception:  # pragma: no cover - lexicon is part of the engine
        return {}


@lru_cache(maxsize=512)
def _pattern(terms: tuple[str, ...]) -> Optional[re.Pattern]:
    alts: list[str] = []
    lex = _lexicon()
    for t in terms:
        alts.append(re.escape(t).replace(r"\ ", r"[\s_-]+") + r"[a-z0-9]*")
        for cue in lex.get(t, ()):
            cue = str(cue).lower().strip()
            if not cue:
                continue
            if " " in cue:
                alts.append(re.escape(cue).replace(r"\ ", r"[\s_-]+"))
            else:
                alts.append(re.escape(cue) + r"[a-z0-9]*")
    if not alts:
        return None
    alts.sort(key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(alts) + r")", re.IGNORECASE)


_REPEAT = re.compile(re.escape(NEUTRAL) + r"(?:\s*(?:,|and|&|or)\s*" + re.escape(NEUTRAL) + r")+",
                     re.IGNORECASE)


def _substitute(text: str, fb: list[str]) -> str:
    pat = _pattern(tuple(fb))
    if pat is None:
        return text
    return _REPEAT.sub(NEUTRAL, pat.sub(NEUTRAL, text))


def _hits(text: str, forbidden: list[str]) -> bool:
    return bool(text and forbidden and mentions_forbidden(text, forbidden, synonyms=True))


def redact_sentence_text(text: str, forbidden: Iterable[str]) -> str:
    """Prose: replace forbidden words, drop sentences that still mention one."""
    fb = forbidden_terms(forbidden)
    if not text or not fb or not _hits(text, fb):
        return text
    out_lines = []
    for line in str(text).split("\n"):
        if not _hits(line, fb):
            out_lines.append(line)
            continue
        m = re.match(r"^(\s*(?:[#>*-]+|\d+[.)])?\s*)(.*)$", line)
        prefix, body = (m.group(1), m.group(2)) if m else ("", line)
        kept = []
        for sent in _SENTENCE_SPLIT.split(body):
            if _hits(sent, fb):
                sent = _substitute(sent, fb)
            if not _hits(sent, fb):
                kept.append(sent)
        body = " ".join(s for s in kept if s.strip())
        if body.strip():
            out_lines.append(prefix + body)
        elif not prefix.strip():
            out_lines.append("")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out_lines))


def redact_title(title: str, forbidden: Iterable[str], fallback: str = "A journey") -> str:
    fb = forbidden_terms(forbidden)
    if not title or not fb or not _hits(title, fb):
        return title
    parts = [p for p in _TITLE_SPLIT.split(title) if p.strip()]
    kept = []
    for p in parts:
        if _hits(p, fb):
            p = _substitute(p, fb)
        if not _hits(p, fb):
            kept.append(p)
    if kept:
        return " — ".join(kept)
    return fallback


def redact_value(value: Any, forbidden: Iterable[str]) -> Any:
    """Recursively redact strings inside JSON-like data; list items that end
    up empty are dropped."""
    fb = forbidden_terms(forbidden)
    if not fb:
        return value
    if isinstance(value, str):
        return redact_sentence_text(value, fb)
    if isinstance(value, list):
        out = []
        for v in value:
            if isinstance(v, str) and len(v.split()) <= 3 and _hits(v, fb):
                continue                         # a bare label: drop it
            r = redact_value(v, fb)
            if isinstance(v, str) and not str(r).strip():
                continue
            out.append(r)
        return out
    if isinstance(value, dict):
        return {k: redact_value(v, fb) for k, v in value.items()}
    return value
