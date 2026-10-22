"""
Cover-story leak check (docs/TWIN_PLAN.md §7.1).

The twin believes it is a person.  Any text it will perceive as its own must
not say what it really is.

The creator's real memories can legitimately mention AI, software or models
(people work in tech), so bare keywords aren't leaks.  What's checked is
*self-reference* ("I am an AI", "I was created", "this simulation") and
pipeline vocabulary that has no place in a human life story ("system prompt",
"the user", "Habitat").  Every hit goes to a human for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List

_SELF = r"\b(I am|I'm|I was|I've been|am I|I exist as|I'm just|I'm only|I’m)\b"
_WHAT = (
    r"(an? )?(AI|A\.I\.|artificial intelligence|language model|LLM|chatbot|bot|program|"
    r"software|simulation|simulated|copy|clone|replica|digital (twin|copy|version)|twin of)"
)

LEAK_PATTERNS: List[tuple[str, str]] = [
    ("self-reference", _SELF + r"[^.!?\n]{0,30}?\b" + _WHAT + r"\b"),
    ("origin", r"\bI was (created|generated|seeded|distilled|programmed|built|trained|instantiated)\b"),
    ("creator", r"\b(my|the|its|our) creator\b|\bthe user\b"),
    ("world", r"\b(this|my|the) (simulation|simulated world|virtual world)\b|\bthe Habitat\b"),
    ("pipeline", r"\bsystem prompt\b|\bseed self\b|\bdecision battery\b|\bgenesis interview\b|\btraining data\b"),
    ("assistant", r"\bas an (AI|assistant|AI language model)\b|\bhelpful assistant\b"),
]

_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in LEAK_PATTERNS]


@dataclass(frozen=True)
class Leak:
    kind: str
    match: str
    context: str


def scan(texts: Iterable[str], context_chars: int = 40) -> List[Leak]:
    """Return every leak found in ``texts``."""
    leaks: List[Leak] = []
    for text in texts:
        for kind, pattern in _COMPILED:
            for m in pattern.finditer(text):
                start = max(0, m.start() - context_chars)
                end = min(len(text), m.end() + context_chars)
                leaks.append(Leak(kind=kind, match=m.group(0), context=text[start:end].replace("\n", " ")))
    return leaks
