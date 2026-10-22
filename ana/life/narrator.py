"""
The narrator gives the twin words: its journal at night and its letters to the old friend.

Narration never decides anything.  Choices are already made (``mind/choice.py``);
the narrator only voices what happened.  ``TemplateNarrator`` works offline and
costs nothing.  ``ClaudeNarrator`` writes in the twin's own voice, within a
daily call budget, and falls back to the template whenever the budget runs out
or the text would break the cover story.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from pydantic import BaseModel

from ..genesis.leakage import scan
from ..llm import StructuredLLM
from ..mind.mind import Mind

DRIVE_LONGING = {
    "connection": ["I could do with a real conversation.", "Lonely tonight, if I'm honest.", "I miss being known."],
    "novelty": ["I keep looking at the road north.", "Same streets again. I want something new.", "Restless."],
    "competence": ["My hands want something to get good at.", "I'm not using what I'm good at.", "I want to be useful."],
    "autonomy": ["I need a day that's only mine.", "Too much of today belonged to other people.", "I want my own hours back."],
    "meaning": ["I want something to matter.", "What am I actually doing here?", "Busy, but for what."],
    "security": ["I keep counting the money in my head.", "Money's thin.", "I'd sleep better with something put aside."],
    "energy": ["I'm tired in a way sleep doesn't fix.", "Bone tired.", "I need to rest."],
}


class Narrator(Protocol):
    calls: int

    def journal(self, mind: Mind, day: int, facts: Dict) -> str: ...

    def letter(self, mind: Mind, day: int, facts: Dict) -> str: ...


def day_facts(mind: Mind, day: int, weather: str, money: float) -> Dict:
    rec = mind.day_record(day)
    moments, seen = [], set()
    for m in sorted(rec.moments, key=lambda m: -m["intensity"]):
        if m["text"] not in seen:
            seen.add(m["text"])
            moments.append(m)
    people = sorted(
        (r for w, r in mind.relationships.items() if w != "friend" and r.familiarity > 0.05),
        key=lambda r: -r.affection,
    )
    return {
        "day": day,
        "weather": weather,
        "money": round(money),
        "best": [m["text"] for m in moments if m["valence"] > 0.1][:3],
        "worst": [m["text"] for m in moments if m["valence"] < -0.1][:2],
        "people": [(r.name, r.stance) for r in people[:4]],
        "goals_started": rec.goals_started,
        "goals_closed": rec.goals_closed,
        "mood": mind.mood_word(),
        "longing": DRIVE_LONGING[mind.drives.most_pressing()][day % 3],
        "phase": mind.temperament.phase(mind.decisions[-1].tick if mind.decisions else 0),
        "friend": mind.relationships["friend"].name,
        "friend_stance": mind.relationships["friend"].stance,
        "suggested": [k for k, (p, _) in mind.suggestions.items() if p > 0],
        "pushed": [k for k, (p, _) in mind.suggestions.items() if p < 0],
    }


class TemplateNarrator:
    calls = 0

    def journal(self, mind: Mind, day: int, f: Dict) -> str:
        lines = [f"Day {day}. {f['weather'].capitalize()}. Feeling {f['mood']}."]
        lines += f["best"][:2]
        lines += f["worst"][:1]
        if f.get("echo"):
            lines.append(f"It reminded me of something: {_lower_first(f['echo'])}")
        for name, stance in f["people"][:2]:
            lines.append(f"{name}: {_stance_phrase(stance)}")
        for g in f["goals_started"]:
            lines.append(f"I've decided something: {g.rstrip('.')}.")
        for g in f["goals_closed"]:
            lines.append(g[0].upper() + g[1:] + ".")
        lines.append(f["longing"])
        return " ".join(lines)

    def letter(self, mind: Mind, day: int, f: Dict) -> str:
        parts = [f"{f['friend']},"]
        if f["best"]:
            parts.append(f["best"][0])
        elif f["worst"]:
            parts.append(f["worst"][0])
        else:
            parts.append("Quiet here.")
        parts.append(f"I'm {f['mood']}.")
        if f["pushed"]:
            parts.append(f"And please stop telling me to {mind.action_label(f['pushed'][0])}. I heard you the first time.")
        elif f["suggested"]:
            parts.append(f"You said I should {mind.action_label(f['suggested'][0])}. Maybe.")
        parts.append(f["longing"])
        return " ".join(parts)


def _lower_first(text: str) -> str:
    if text.startswith(("Day ", "Week ", "I ", "I'")):
        return text
    return text[0].lower() + text[1:]


def _stance_phrase(stance: str) -> str:
    return {
        "loves": "I'd be lost here without them.",
        "likes": "I like them more each time.",
        "knows": "still working them out.",
        "barely knows": "a face, not yet a person.",
        "wary of": "something's off between us.",
        "resents": "I'd rather not see them.",
    }[stance]


class Voiced(BaseModel):
    text: str


VOICE_SYSTEM = """\
You are writing as {name}. You ARE {name}: write in the first person, in their voice, as a real \
person writing privately. Their voice: {tone}. Phrases they use: {phrases}. They never say: {never}.

Write only from the facts given. Don't invent events, people or feelings that aren't there. \
Keep it short, plain and specific, the way a real person writes when nobody's watching."""


class ClaudeNarrator:
    def __init__(self, llm: StructuredLLM, calls_per_day: int = 3) -> None:
        self.llm = llm
        self.calls_per_day = calls_per_day
        self.calls = 0
        self._by_day: Dict[int, int] = {}
        self.fallback = TemplateNarrator()
        self.leaks: List[str] = []

    def _voice(self, mind: Mind, day: int, task: str, facts: Dict, fallback: str) -> str:
        if self._by_day.get(day, 0) >= self.calls_per_day:
            return fallback
        self._by_day[day] = self._by_day.get(day, 0) + 1
        self.calls += 1
        v = mind.seed.voice
        system = VOICE_SYSTEM.format(name=mind.name, tone=v.tone, phrases=", ".join(v.typical_phrases) or "-",
                                     never=", ".join(v.never_says) or "-")
        out: Optional[Voiced] = self.llm.structured(
            system=system, messages=[{"role": "user", "content": f"{task}\n\nFacts: {facts}"}],
            schema=Voiced, effort="low", max_tokens=2000,
        )
        text = out.text.strip() if out else ""
        if not text or scan([text]):
            self.leaks.append(text)
            return fallback
        return text

    def journal(self, mind: Mind, day: int, facts: Dict) -> str:
        return self._voice(mind, day, "Write tonight's journal entry (3-6 sentences).", facts,
                           self.fallback.journal(mind, day, facts))

    def letter(self, mind: Mind, day: int, facts: Dict) -> str:
        return self._voice(mind, day, f"Write a short message to your old friend {facts['friend']} (2-4 sentences).",
                           facts, self.fallback.letter(mind, day, facts))
