import re

import pytest

from ana.genesis.battery import Answer, Battery
from ana.genesis.fidelity import AnswerSheet, ItemAnswer, chance_score, evaluate, item_score


def test_item_scoring():
    b = Battery.load()
    choice = next(i for i in b.items if i.kind == "choice")
    scale = next(i for i in b.items if i.kind == "scale")
    assert item_score(choice, 2, 2, 7) == 1.0
    assert item_score(choice, 1, 2, 7) == 0.0
    assert item_score(scale, 4, 4, 7) == 1.0
    assert item_score(scale, 5, 4, 7) == pytest.approx(5 / 6)
    assert item_score(scale, 7, 1, 7) == 0.0
    assert chance_score(choice, 1, 7) == pytest.approx(1 / len(choice.options))
    assert 0 < chance_score(scale, 4, 7) < 1


def test_evaluate_twin_vs_baseline(seed):
    b = Battery.load()
    truth = {i.id: Answer(value=1) for i in b.items}

    def respond(schema, messages, system):
        ids = re.findall(r"\[([a-z]+-\d\d)\]", messages[0]["content"])
        # The seeded twin matches the creator; the baseline always answers 2.
        value = 1 if "This is you" in system else 2
        return AnswerSheet(answers=[ItemAnswer(id=i, value=value) for i in ids])

    from conftest import FakeLLM

    llm = FakeLLM(respond)
    report = evaluate(llm, seed, b, truth)
    assert report["overall"]["n"] == 24
    assert report["overall"]["twin"] == 1.0
    assert report["overall"]["baseline"] < 1.0
    assert len(report["by_domain"]) == 12
    # Only held-out items are asked, and open_questions never reach the twin.
    asked = set(re.findall(r"\[([a-z]+-\d\d)\]", llm.calls[0]["messages"][0]["content"]))
    assert asked == b.holdout_ids()
    assert "open_questions" not in llm.calls[0]["system"]


def test_evaluate_requires_answers(seed):
    from conftest import FakeLLM

    with pytest.raises(ValueError):
        evaluate(FakeLLM(lambda *a: None), seed, Battery.load(), {})
