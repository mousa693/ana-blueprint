from collections import Counter

from ana.genesis.battery import Battery, load_answers, run
from ana.genesis.store import Store

from conftest import Script


def test_battery_shape():
    b = Battery.load()
    assert len(b.items) == 120
    assert len({i.id for i in b.items}) == 120
    per_domain = Counter(i.domain for i in b.items)
    assert len(per_domain) == 12 and set(per_domain.values()) == {10}
    for item in b.items:
        if item.kind == "choice":
            assert len(item.options) >= 2
        else:
            assert item.kind == "scale" and len(item.anchors) == 2


def test_holdout_is_stable_and_stratified():
    b = Battery.load()
    held = b.holdout_ids()
    assert held == Battery.load().holdout_ids()
    assert len(held) == 24
    assert all(n == 2 for n in Counter(i.domain for i in b.holdout_items()).values())
    assert not held & {i.id for i in b.seed_items()}


def test_parse():
    b = Battery.load()
    choice = next(i for i in b.items if i.kind == "choice")
    scale = next(i for i in b.items if i.kind == "scale")
    assert b.parse(choice, "2 because reasons").value == 2
    assert b.parse(choice, "2 because reasons").note == "because reasons"
    assert b.parse(choice, str(len(choice.options) + 1)) is None
    assert b.parse(choice, "abc") is None
    assert b.parse(scale, "7").value == 7
    assert b.parse(scale, "8") is None
    assert b.parse(scale, "0") is None


def test_run_saves_and_resumes(tmp_path):
    store = Store(tmp_path)
    b = Battery.load()
    run(store, b, ask=Script(["1", "nope", "2", "q"]), say=lambda _: None)
    answers = load_answers(store)
    assert [a.value for a in answers.values()] == [1, 2]
    run(store, b, ask=Script(["1", "q"]), say=lambda _: None)
    assert len(load_answers(store)) == 3
