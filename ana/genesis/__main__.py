"""Genesis command line.  See ``ana/genesis/__init__.py`` for the commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anthropic

from ..llm import DEFAULT_MODEL, LLM, LLMRefusal, LLMTruncated
from . import battery as battery_mod
from . import distill as distill_mod
from . import fidelity as fidelity_mod
from . import interview as interview_mod
from .seed import SeedSelf
from .store import DEFAULT_DIR, FIDELITY, SEED_SELF, Store


def cmd_status(store: Store, _: argparse.Namespace) -> int:
    battery = battery_mod.Battery.load()
    answers = battery_mod.load_answers(store)
    data = interview_mod.load(store)
    print(f"Data directory: {store.root.resolve()}")
    print(f"Battery:   {len(answers)}/{len(battery.items)} answered")
    for m in interview_mod.MODULES:
        state = data["modules"][m.id]
        mark = "✓" if state["complete"] else f"{len(state['turns'])} answers"
        print(f"Interview: {m.title:<22} {mark}")
    friend = data.get("old_friend")
    print(f"Old friend: {friend['name'] if friend else 'not chosen'}")
    print(f"Seed self: {'written' if store.exists(SEED_SELF) else 'not yet'}")
    if store.exists(FIDELITY):
        o = store.read(FIDELITY)["overall"]
        print(f"Fidelity:  twin {o['twin']:.2f} · baseline {o['baseline']:.2f} · chance {o['chance']:.2f}")
    return 0


def cmd_battery(store: Store, _: argparse.Namespace) -> int:
    battery_mod.run(store, battery_mod.Battery.load())
    return 0


def cmd_interview(store: Store, args: argparse.Namespace) -> int:
    interview_mod.run(store, LLM(model=args.model))
    return 0


def cmd_distill(store: Store, args: argparse.Namespace) -> int:
    battery = battery_mod.Battery.load()
    answers = battery_mod.load_answers(store)
    data = interview_mod.load(store)
    if not interview_mod.is_complete(data) and not args.partial:
        print("The interview isn't finished. Finish it, or pass --partial to distill what's there.")
        return 1
    seed_answered = sum(1 for i in battery.seed_items() if i.id in answers)
    if seed_answered < len(battery.seed_items()) and not args.partial:
        print(f"Only {seed_answered}/{len(battery.seed_items())} seed battery items answered. Pass --partial to go ahead anyway.")
        return 1
    corpus, warnings = distill_mod.load_corpus(args.corpus)
    for w in warnings:
        print(f"warning: {w}")
    print("Distilling… (this is one long model call)")
    result = distill_mod.distill(LLM(model=args.model), data, answers, battery, corpus)
    path = store.write(SEED_SELF, result.seed.model_dump())
    print(f"Wrote {path}")
    if result.leaks:
        print(f"\n{len(result.leaks)} possible cover-story leak(s) remain after {result.attempts} attempts. Review and edit by hand:")
        for leak in result.leaks:
            print(f"  [{leak.kind}] …{leak.context}…")
    if result.seed.open_questions:
        print("\nOpen questions to review (the twin never sees these):")
        for q in result.seed.open_questions:
            print(f"  - {q}")
    return 0


def cmd_fidelity(store: Store, args: argparse.Namespace) -> int:
    if not store.exists(SEED_SELF):
        print("No seed_self.json yet. Run distill first.")
        return 1
    seed = SeedSelf(**store.read(SEED_SELF))
    report = fidelity_mod.evaluate(
        LLM(model=args.model), seed, battery_mod.Battery.load(), battery_mod.load_answers(store)
    )
    store.write(FIDELITY, report)
    o = report["overall"]
    print(f"Held-out items: {o['n']}")
    print(f"  twin     {o['twin']:.2f}")
    print(f"  baseline {o['baseline']:.2f}   (same model, no seed)")
    print(f"  chance   {o['chance']:.2f}")
    print("\nBy domain (twin / baseline / chance):")
    for domain, s in report["by_domain"].items():
        print(f"  {domain:<10} {s['twin']:.2f} / {s['baseline']:.2f} / {s['chance']:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ana.genesis", description="Make the twin you, at birth.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DIR, help="where Genesis keeps its files")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("battery")
    sub.add_parser("interview")
    p = sub.add_parser("distill")
    p.add_argument("--corpus", type=Path, help="folder of your own .txt/.md writing")
    p.add_argument("--partial", action="store_true", help="distill even if the interview or battery is unfinished")
    sub.add_parser("fidelity")
    args = parser.parse_args(argv)
    store = Store(args.data)
    commands = {
        "status": cmd_status,
        "battery": cmd_battery,
        "interview": cmd_interview,
        "distill": cmd_distill,
        "fidelity": cmd_fidelity,
    }
    try:
        return commands[args.command](store, args)
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Everything answered so far is saved.")
        return 130
    except anthropic.AuthenticationError:
        print("\nNo valid Claude API credentials. Set ANTHROPIC_API_KEY and rerun; your answers are saved.")
        return 1
    except (LLMRefusal, LLMTruncated) as e:
        print(f"\nThe model call didn't complete: {e}. Your answers are saved.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
