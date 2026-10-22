from ana.genesis import interview as iv
from ana.genesis.store import INTERVIEW, Store

from conftest import FakeLLM, Script


def interviewer(complete_after: int):
    """Fake interviewer: asks numbered questions, says complete after N answers."""

    def respond(schema, messages, system):
        transcript = messages[0]["content"]
        answered = transcript.count("\nA: ")
        return schema(
            question=f"Question {answered + 1}?",
            module_complete=answered >= complete_after,
            coverage_notes=f"{answered} answered",
        )

    return respond


def test_opening_question_needs_no_model_call():
    llm = FakeLLM(interviewer(0))
    turn = iv.next_turn(llm, iv.MODULES[0], {"turns": [], "complete": False, "notes": ""})
    assert turn.question == iv.MODULES[0].opening
    assert llm.calls == []


def test_completion_is_refused_before_min_turns():
    module = iv.MODULES[0]
    llm = FakeLLM(interviewer(0))  # always tries to finish
    state = {"turns": [{"q": "q", "a": "a"}], "complete": False, "notes": ""}
    assert iv.next_turn(llm, module, state).module_complete is False


def test_skips_do_not_count_toward_min_turns():
    module = iv.MODULES[0]
    llm = FakeLLM(interviewer(0))
    state = {"turns": [{"q": "q", "a": "(skipped)"}] * module.min_turns, "complete": False, "notes": ""}
    assert iv.next_turn(llm, module, state).module_complete is False


def test_max_turns_forces_completion():
    module = iv.MODULES[0]
    llm = FakeLLM(interviewer(99))
    state = {"turns": [{"q": "q", "a": "a"}] * module.max_turns, "complete": False, "notes": ""}
    assert iv.next_turn(llm, module, state).module_complete is True
    assert llm.calls == []


def test_full_run_with_pause_and_resume(tmp_path):
    store = Store(tmp_path)
    llm = FakeLLM(interviewer(complete_after=10))
    # First module: two answers then pause.
    iv.run(store, llm, ask=Script(["I grew up by the sea.", "/skip", "/q"]), say=lambda _: None)
    data = store.read(INTERVIEW)
    turns = data["modules"]["origins"]["turns"]
    assert [t["a"] for t in turns] == ["I grew up by the sea.", "(skipped)"]
    assert not iv.is_complete(data)

    # Resume: /next through every module, then name the old friend.
    answers = ["/next"] * len(iv.MODULES) + ["Rami", "We shared a desk at school."]
    iv.run(store, llm, ask=Script(answers), say=lambda _: None)
    data = store.read(INTERVIEW)
    assert iv.is_complete(data)
    assert data["old_friend"] == {"name": "Rami", "how_we_met": "We shared a desk at school."}
    assert data["modules"]["origins"]["turns"][0]["a"] == "I grew up by the sea."


def test_render_transcript_skips_empty_modules():
    data = iv.load(Store("/nonexistent"))
    data["modules"]["love"]["turns"] = [{"q": "How?", "a": "Badly."}]
    text = iv.render_transcript(data)
    assert "## Love" in text and "Badly." in text and "## Origins" not in text
