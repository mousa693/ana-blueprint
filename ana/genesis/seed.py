"""
The Seed Self: everything the twin is at the moment of birth.

Distillation produces one of these from the interview and the seed half of the
decision battery.  It is written from the twin's own point of view: it believes
it is a person who has lived the creator's life and just moved to a new town
(docs/TWIN_PLAN.md §7.1).  Nothing in here may say otherwise.

The models avoid dicts and numeric constraints so they translate cleanly to
the structured-output JSON schema; ranges are enforced in ``clamp()``.
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class Trait(BaseModel):
    """A Big Five dimension, 0 = very low, 1 = very high."""

    score: float
    evidence: str = Field(description="What in the interview or battery shows this")


class BigFive(BaseModel):
    openness: Trait
    conscientiousness: Trait
    extraversion: Trait
    agreeableness: Trait
    neuroticism: Trait


class Value(BaseModel):
    name: str = Field(description="e.g. freedom, loyalty, honesty, achievement")
    weight: float = Field(description="0-1, relative importance")
    evidence: str


class DriveProfile(BaseModel):
    """How strongly each homeostatic need pulls (0-1).  See plan §3.4."""

    energy: float
    connection: float
    competence: float
    autonomy: float
    novelty: float
    meaning: float
    security: float


class Character(BaseModel):
    big_five: BigFive
    values: List[Value]
    drives: DriveProfile
    humor: str
    conflict_style: str
    decision_style: str
    quirks: List[str] = Field(description="Habits, obsessions, pet peeves, rituals")


class Memory(BaseModel):
    """A defining memory.  Becomes an IDENTITY shard in MCMG at birth."""

    title: str
    when: str = Field(description="Age or period of life")
    what_happened: str = Field(description="First person, concrete")
    feeling: str
    meaning: str = Field(description="What it taught or how it shaped me")
    people: List[str]
    symbols: List[str] = Field(description="Objects, places, images tied to it")
    intensity: float = Field(description="0-1, emotional weight")


class Person(BaseModel):
    """Someone from the past life.  A memory, never an NPC (plan §8)."""

    name: str
    role: str = Field(description="mother, first love, old boss, ...")
    story: str = Field(description="The relationship in my own words")
    feelings: str
    affection: float = Field(description="-1 to 1")
    trust: float = Field(description="-1 to 1")


class Attachment(BaseModel):
    """Something loved or hated, with the reason."""

    thing: str
    why: str
    intensity: float = Field(description="0-1")


class FutureShard(BaseModel):
    """A 'memory of the future': an aspiration, fear or vision."""

    kind: str = Field(description="aspiration | fear | vision")
    text: str
    intensity: float = Field(description="0-1")


class Voice(BaseModel):
    tone: str = Field(description="How I talk: register, length, formality")
    typical_phrases: List[str]
    never_says: List[str]
    sample_lines: List[str] = Field(description="Lines that sound exactly like me")


class OldFriend(BaseModel):
    """The creator's in-world identity (plan §7.1)."""

    name: str
    how_we_met: str
    what_they_mean_to_me: str


class Arrival(BaseModel):
    """Why I'm in this new town.  Must not contradict any memory."""

    left_behind: str = Field(description="Where I came from, kept vague enough to be unreachable")
    why_i_left: str
    what_im_looking_for: str
    first_impressions: str


class SeedSelf(BaseModel):
    name: str
    age: int
    self_story: str = Field(description="First person: who I am, what has happened to me, what I'm becoming")
    character: Character
    defining_memories: List[Memory]
    people: List[Person]
    loves: List[Attachment]
    hates: List[Attachment]
    future: List[FutureShard]
    voice: Voice
    old_friend: OldFriend
    arrival: Arrival
    open_questions: List[str] = Field(
        description="Things the source material left unclear; for the creator to review. Never shown to the twin."
    )

    def clamp(self) -> "SeedSelf":
        """Force every score into its documented range."""

        def unit(x: float) -> float:
            return min(1.0, max(0.0, x))

        def signed(x: float) -> float:
            return min(1.0, max(-1.0, x))

        bf = self.character.big_five
        for trait in (bf.openness, bf.conscientiousness, bf.extraversion, bf.agreeableness, bf.neuroticism):
            trait.score = unit(trait.score)
        for value in self.character.values:
            value.weight = unit(value.weight)
        drives = self.character.drives
        for field in DriveProfile.model_fields:
            setattr(drives, field, unit(getattr(drives, field)))
        for item in [*self.defining_memories, *self.loves, *self.hates, *self.future]:
            item.intensity = unit(item.intensity)
        for person in self.people:
            person.affection = signed(person.affection)
            person.trust = signed(person.trust)
        return self

    def twin_visible_text(self) -> List[str]:
        """All text the twin will ever perceive as its own (everything except open_questions)."""
        data = self.model_dump(exclude={"open_questions"})
        out: List[str] = []

        def walk(node: object) -> None:
            if isinstance(node, str):
                out.append(node)
            elif isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(data)
        return out
