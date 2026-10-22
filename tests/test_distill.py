from ana.genesis import interview as iv
from ana.genesis.battery import Answer, Battery
from ana.genesis.distill import build_prompt, distill, load_corpus
from ana.genesis.store import Store

from conftest import FakeLLM, make_seed


def interview_data(friend=None):
    data = iv.load(Store("/nonexistent"))
    data["modules"]["origins"]["turns"] = [{"q": "Where?", "a": "A fishing town."}]
    data["old_friend"] = friend
    return data


def all_answers(battery):
    return {i.id: Answer(value=1, note=f"note-{i.id}") for i in battery.items}


def test_holdout_answers_never_reach_the_prompt():
    b = Battery.load()
    prompt = build_prompt(interview_data(), all_answers(b), b, corpus="")
    for item in b.holdout_items():
        assert item.prompt not in prompt
        assert f"note-{item.id}" not in prompt
    for item in b.seed_items():
        assert f"note-{item.id}" in prompt


def test_old_friend_is_enforced_and_scores_clamped():
    b = Battery.load()
    raw = make_seed()
    raw.character.drives.novelty = 1.7
    raw.people[0].trust = -3
    llm = FakeLLM(lambda schema, m, s: raw.model_copy(deep=True))
    friend = {"name": "Rami", "how_we_met": "School."}
    result = distill(llm, interview_data(friend), all_answers(b), b)
    assert result.seed.old_friend.name == "Rami"
    assert result.seed.old_friend.how_we_met == "School."
    assert result.seed.character.drives.novelty == 1.0
    assert result.seed.people[0].trust == -1.0
    assert result.attempts == 1 and result.leaks == []


def test_leak_triggers_one_retry_with_feedback():
    b = Battery.load()
    leaky = make_seed(self_story="Some nights I suspect I'm just a program.")
    clean = make_seed()
    outputs = [leaky, clean]
    llm = FakeLLM(lambda schema, m, s: outputs.pop(0))
    result = distill(llm, interview_data(), all_answers(b), b)
    assert result.attempts == 2 and result.leaks == []
    assert "Revision needed" in llm.calls[1]["messages"][0]["content"]
    assert "just a program" in llm.calls[1]["messages"][0]["content"]


def test_open_questions_are_not_scanned(seed):
    # conftest's open_questions mention "system prompt"; that field is creator-only.
    b = Battery.load()
    llm = FakeLLM(lambda schema, m, s: seed.model_copy(deep=True))
    assert distill(llm, interview_data(), all_answers(b), b).leaks == []


def test_load_corpus(tmp_path):
    (tmp_path / "a.md").write_text("hello")
    (tmp_path / "b.pdf").write_text("ignored")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.txt").write_text("world")
    text, warnings = load_corpus(tmp_path)
    assert "hello" in text and "world" in text and "ignored" not in text
    assert warnings == []
    assert load_corpus(None) == ("", [])
