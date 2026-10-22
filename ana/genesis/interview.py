"""
The Genesis interview: a biographer agent that gets the creator's life on record.

The interview runs module by module (origins, defining moments, people, …).
In each module the interviewer reads the transcript so far and picks the next
question: one at a time, chasing specific stories rather than adjectives.  Every
answer is saved straight away, so the interview can span many sessions.

Commands while answering:
    /skip   skip this question
    /next   finish this module and move on
    /q      stop; rerun to resume where you left off
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List

from pydantic import BaseModel, Field

from ..llm import StructuredLLM
from .store import INTERVIEW, Store


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    goal: str
    opening: str
    min_turns: int = 4
    max_turns: int = 10


MODULES: List[Module] = [
    Module(
        "origins",
        "Where you come from",
        "Childhood place, home, family atmosphere, the earliest memories that still matter, what the world felt like then.",
        "Let's start at the beginning. Where did you grow up, and what did home feel like?",
    ),
    Module(
        "defining_moments",
        "Defining moments",
        "The 5-8 events that most shaped who they are: turning points, wins, failures, losses, moments of clarity. "
        "For each, get what happened concretely, how it felt, and what it changed.",
        "If you had to name the moments that made you who you are, which come to mind first?",
        min_turns=6,
        max_turns=14,
    ),
    Module(
        "people",
        "The people",
        "The most important people in their life, past and present: who they are, the story of the relationship, "
        "how they feel about them now, unresolved things.",
        "Who are the people who've mattered most in your life?",
        min_turns=5,
        max_turns=12,
    ),
    Module(
        "work",
        "Work and craft",
        "What they do, how they got there, what they're good at, how they work, what work means to them, what they'd do if money didn't matter.",
        "Tell me about your work. What do you actually spend your days doing?",
    ),
    Module(
        "love",
        "Love",
        "How they love and want to be loved, important relationships, patterns, what they've learned, what they still get wrong.",
        "How do you love people? What does it look like when you do?",
    ),
    Module(
        "loves_and_hates",
        "Loves and hates",
        "Specific things they love and hate (places, foods, music, ideas, behaviors, kinds of people) and the reasons behind each.",
        "What are some things you genuinely love? Small or big, doesn't matter.",
    ),
    Module(
        "values",
        "Values under pressure",
        "What they do when values collide: honesty vs kindness, loyalty vs fairness, ambition vs peace. Ask for real situations they faced, not hypotheticals. Also how they fight and how they make up.",
        "Tell me about a time doing the right thing cost you something.",
    ),
    Module(
        "shadows",
        "Fears and wounds",
        "Fears, insecurities, old wounds, what they avoid, what they're ashamed of or still carry. Be gentle; skips are completely fine.",
        "This part goes deeper, and you can skip anything. What are you afraid of?",
        min_turns=3,
    ),
    Module(
        "future",
        "The future",
        "Ambitions, dreams, the life they hope for, who they want to become, what they fear becoming, unfinished business.",
        "Picture yourself ten years from now, if things go well. What does that look like?",
    ),
    Module(
        "texture",
        "Everyday texture",
        "Daily routines, rituals, habits, humor, how they talk (phrases they overuse, how they text), what a good ordinary day looks like.",
        "Walk me through a good, ordinary day in your life.",
        min_turns=3,
        max_turns=8,
    ),
]

INTERVIEWER_SYSTEM = """\
You are a skilled biographer interviewing someone about their own life. The material will seed a \
character who believes it has lived this person's life, so concrete detail matters more than polish.

How you interview:
- Ask exactly one question at a time. Keep it short and warm, without flattery.
- Chase specifics: a story, a scene, a name, a sentence someone said. "What happened next?" beats "How did that make you feel?"
- When an answer is abstract ("I value honesty"), ask for the time it was tested.
- Follow threads they open, but keep the module's goal in view.
- Respect skips and short answers. Never push on something they declined.
- Don't summarize their answers back to them or comment on their character.

You will be given the module's goal and the transcript so far. Return the next question, or mark the \
module complete once its goal is covered well enough (never before the minimum number of answers). \
In coverage_notes, list briefly what the module has covered and what's missing."""


class InterviewerTurn(BaseModel):
    question: str = Field(description="The next question to ask; empty if module_complete")
    module_complete: bool
    coverage_notes: str


def _empty_module() -> Dict:
    return {"turns": [], "complete": False, "notes": ""}


def _prompt(module: Module, state: Dict) -> str:
    answered = [t for t in state["turns"] if t["a"] != "(skipped)"]
    lines = [
        f"Module: {module.title}",
        f"Goal: {module.goal}",
        f"Answers so far: {len(answered)} (minimum {module.min_turns}, maximum {module.max_turns})",
        "",
        "Transcript:",
    ]
    for t in state["turns"]:
        lines += [f"Q: {t['q']}", f"A: {t['a']}", ""]
    return "\n".join(lines)


def next_turn(llm: StructuredLLM, module: Module, state: Dict) -> InterviewerTurn:
    if not state["turns"]:
        return InterviewerTurn(question=module.opening, module_complete=False, coverage_notes="")
    if len(state["turns"]) >= module.max_turns:
        return InterviewerTurn(question="", module_complete=True, coverage_notes=state.get("notes", ""))
    turn = llm.structured(
        system=INTERVIEWER_SYSTEM,
        messages=[{"role": "user", "content": _prompt(module, state)}],
        schema=InterviewerTurn,
        effort="medium",
        max_tokens=4000,
    )
    answered = sum(1 for t in state["turns"] if t["a"] != "(skipped)")
    if turn.module_complete and answered < module.min_turns:
        turn.module_complete = False
    if not turn.module_complete and not turn.question.strip():
        turn.module_complete = True
    return turn


def load(store: Store) -> Dict:
    data = store.read(INTERVIEW) or {"modules": {}, "old_friend": None}
    for m in MODULES:
        data["modules"].setdefault(m.id, _empty_module())
    return data


def _ask_old_friend(ask: Callable[[str], str], say: Callable[[str], None]) -> Dict | None:
    say(
        "\nOne last thing, about you rather than your life.\n"
        "In the twin's world you'll be an old friend from before it moved, the one person from its past it still talks to.\n"
    )
    name = ask("What name should that friend go by? (/q to decide later)\n> ").strip()
    if name in ("", "/q"):
        return None
    how = ask("In a sentence or two: how did the two of you meet, as far as it remembers?\n> ").strip()
    return {"name": name, "how_we_met": how}


def run(
    store: Store,
    llm: StructuredLLM,
    ask: Callable[[str], str] = input,
    say: Callable[[str], None] = print,
) -> Dict:
    data = load(store)
    done = sum(1 for m in MODULES if data["modules"][m.id]["complete"])
    say(f"Interview: {done}/{len(MODULES)} modules complete. Commands: /skip, /next, /q\n")

    for module in MODULES:
        state = data["modules"][module.id]
        if state["complete"]:
            continue
        say(f"\n── {module.title} ──\n")
        while True:
            turn = next_turn(llm, module, state)
            state["notes"] = turn.coverage_notes
            if turn.module_complete:
                state["complete"] = True
                store.write(INTERVIEW, data)
                break
            answer = ask(f"{turn.question}\n> ").strip()
            if answer == "/q":
                store.write(INTERVIEW, data)
                say("Saved. Run again to continue.")
                return data
            if answer == "/next":
                state["complete"] = True
                store.write(INTERVIEW, data)
                break
            state["turns"].append({"q": turn.question, "a": "(skipped)" if answer in ("", "/skip") else answer})
            store.write(INTERVIEW, data)

    if data.get("old_friend") is None:
        data["old_friend"] = _ask_old_friend(ask, say)
        store.write(INTERVIEW, data)
    say("\nInterview complete.")
    return data


def is_complete(data: Dict) -> bool:
    return all(data["modules"][m.id]["complete"] for m in MODULES) and data.get("old_friend") is not None


def render_transcript(data: Dict) -> str:
    parts: List[str] = []
    for m in MODULES:
        turns = data["modules"][m.id]["turns"]
        if not turns:
            continue
        parts.append(f"## {m.title}")
        for t in turns:
            parts += [f"Q: {t['q']}", f"A: {t['a']}", ""]
    return "\n".join(parts)
