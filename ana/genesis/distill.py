"""
Distillation: interview + seed battery answers (+ optional writing) → SeedSelf.

Held-out battery answers are never passed in.  The output is scanned for
cover-story leaks; if any turn up, distillation runs once more with the
findings as feedback, and anything still flagged is reported for human review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..llm import StructuredLLM
from . import interview as interview_mod
from .battery import Answer, Battery
from .leakage import Leak, scan
from .seed import SeedSelf

CORPUS_SUFFIXES = {".txt", ".md"}
CORPUS_WARN_CHARS = 2_000_000  # ~500K tokens; past this, consider curating

DISTILL_SYSTEM = """\
You turn a person's own account of their life into the inner world of a character who believes \
they ARE this person and have lived this life. Everything you write becomes that character's \
memories, self-knowledge and voice, so write every narrative field in the first person, in their voice.

Ground rules:
- Stay faithful. Use their real stories, names, phrasing and contradictions. Don't flatten them into \
someone nicer, wiser or more coherent than they are. Keep rough edges, pettiness, humor.
- Don't invent major facts. Where the material is silent, infer cautiously from patterns and record \
the gap in open_questions (the only field the character never sees).
- Trait scores and drive strengths must follow from the evidence, and the evidence fields must cite it.
- People from their life become memories (the `people` list), described as they experienced them.
- Arrival: the character has just moved to a small town far from their old life and can't easily \
reach anyone from it. Write why they left and what they're looking for so it fits everything they \
said, without contradicting any memory. Don't name the town.
- old_friend: the one person from the old life they're still in touch with. Use the name and \
how-we-met provided. This friend is NOT any of the real people in the material; write what the \
friend means to them in a way that fits their history.
- Never describe the character as anything other than a human being, and never mention \
interviews, questionnaires, being created, or a simulated world. To them this is simply their life."""


@dataclass
class DistillResult:
    seed: SeedSelf
    leaks: List[Leak] = field(default_factory=list)
    attempts: int = 1


def load_corpus(corpus_dir: Optional[Path]) -> tuple[str, List[str]]:
    """Concatenate .txt/.md files under ``corpus_dir``.  Returns (text, warnings)."""
    if corpus_dir is None:
        return "", []
    parts: List[str] = []
    for path in sorted(p for p in Path(corpus_dir).rglob("*") if p.suffix.lower() in CORPUS_SUFFIXES):
        parts.append(f"### {path.name}\n{path.read_text(encoding='utf-8', errors='replace')}")
    text = "\n\n".join(parts)
    warnings = []
    if len(text) > CORPUS_WARN_CHARS:
        warnings.append(
            f"corpus is {len(text):,} characters; it will all be sent, but consider curating it to what's most you"
        )
    return text, warnings


def build_prompt(interview: Dict, answers: Dict[str, Answer], battery: Battery, corpus: str) -> str:
    lines = ["# Interview", interview_mod.render_transcript(interview), "", "# Choices and preferences"]
    # Only seed items: held-out answers must never reach distillation.
    for item in battery.seed_items():
        if item.id in answers:
            lines.append(f"- {item.prompt} → {item.describe_answer(answers[item.id], battery.scale_points)}")
    friend = interview.get("old_friend") or {}
    lines += [
        "",
        "# Old friend",
        f"Name: {friend.get('name', '(not chosen yet: invent a plausible first name and flag it in open_questions)')}",
        f"How we met: {friend.get('how_we_met', '(not given: invent something fitting and flag it in open_questions)')}",
    ]
    if corpus:
        lines += ["", "# Their own writing", corpus]
    return "\n".join(lines)


def distill(
    llm: StructuredLLM,
    interview: Dict,
    answers: Dict[str, Answer],
    battery: Battery,
    corpus: str = "",
) -> DistillResult:
    prompt = build_prompt(interview, answers, battery, corpus)
    messages = [{"role": "user", "content": prompt}]
    seed = _call(llm, messages)
    leaks = scan(seed.twin_visible_text())
    attempts = 1
    if leaks:
        feedback = "\n".join(f"- [{l.kind}] …{l.context}…" for l in leaks)
        retry = [
            {
                "role": "user",
                "content": prompt
                + "\n\n# Revision needed\nA previous draft contained phrasing that breaks the character's "
                "belief that this is simply their human life. Avoid anything like:\n" + feedback,
            }
        ]
        seed = _call(llm, retry)
        leaks = scan(seed.twin_visible_text())
        attempts = 2
    friend = interview.get("old_friend")
    if friend:
        seed.old_friend.name = friend["name"]
        if friend.get("how_we_met"):
            seed.old_friend.how_we_met = friend["how_we_met"]
    return DistillResult(seed=seed.clamp(), leaks=leaks, attempts=attempts)


def _call(llm: StructuredLLM, messages: List[Dict]) -> SeedSelf:
    return llm.structured(
        system=DISTILL_SYSTEM,
        messages=messages,
        schema=SeedSelf,
        effort="high",
        max_tokens=64000,
    )
