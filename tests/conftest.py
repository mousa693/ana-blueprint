from __future__ import annotations

from typing import Any, Callable, Dict, List

import pytest

from ana.genesis.seed import SeedSelf


class FakeLLM:
    """Records every call; answers with whatever ``responder`` builds for the schema."""

    def __init__(self, responder: Callable[[type, List[Dict], str], Any]) -> None:
        self.responder = responder
        self.calls: List[Dict] = []

    def structured(self, *, system, messages, schema, effort="medium", max_tokens=16000):
        self.calls.append({"system": system, "messages": messages, "schema": schema, "effort": effort})
        return self.responder(schema, messages, system)


def make_seed(**overrides: Any) -> SeedSelf:
    trait = {"score": 0.5, "evidence": "e"}
    data: Dict[str, Any] = {
        "name": "Sam",
        "age": 34,
        "self_story": "I grew up by the sea and I worked in AI for years before I left.",
        "character": {
            "big_five": {k: dict(trait) for k in ("openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism")},
            "values": [{"name": "freedom", "weight": 0.9, "evidence": "e"}],
            "drives": {k: 0.5 for k in ("energy", "connection", "competence", "autonomy", "novelty", "meaning", "security")},
            "humor": "dry",
            "conflict_style": "direct",
            "decision_style": "gut first",
            "quirks": ["reorders bookshelves"],
        },
        "defining_memories": [
            {
                "title": "The lighthouse",
                "when": "age 9",
                "what_happened": "I climbed the lighthouse alone.",
                "feeling": "terrified, then free",
                "meaning": "fear shrinks when you climb",
                "people": ["Dad"],
                "symbols": ["lighthouse"],
                "intensity": 0.8,
            }
        ],
        "people": [{"name": "Dad", "role": "father", "story": "s", "feelings": "f", "affection": 0.7, "trust": 0.4}],
        "loves": [{"thing": "rain", "why": "quiet", "intensity": 0.6}],
        "hates": [{"thing": "small talk", "why": "empty", "intensity": 0.5}],
        "future": [{"kind": "aspiration", "text": "build a boat", "intensity": 0.7}],
        "voice": {"tone": "short", "typical_phrases": ["fair enough"], "never_says": ["awesome"], "sample_lines": ["Fair enough."]},
        "old_friend": {"name": "placeholder", "how_we_met": "placeholder", "what_they_mean_to_me": "home"},
        "arrival": {"left_behind": "the city", "why_i_left": "burnout", "what_im_looking_for": "quiet", "first_impressions": "windy"},
        "open_questions": ["Is Sam really a morning person? The system prompt is unclear."],
    }
    data.update(overrides)
    return SeedSelf(**data)


@pytest.fixture
def seed() -> SeedSelf:
    return make_seed()


class Script:
    """Feeds scripted answers to an ``ask`` callable and captures prompts."""

    def __init__(self, answers: List[str]) -> None:
        self.answers = list(answers)
        self.prompts: List[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"script ran out of answers at prompt: {prompt!r}")
        return self.answers.pop(0)
