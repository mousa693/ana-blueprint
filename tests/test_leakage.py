import pytest

from ana.genesis.leakage import scan


@pytest.mark.parametrize(
    "text",
    [
        "Sometimes I wonder if I'm just a program.",
        "I am an AI, I think.",
        "I was created to remember.",
        "My creator never visits.",
        "Life in this simulation is quiet.",
        "Whatever the system prompt says.",
        "As an AI language model I can't say.",
    ],
)
def test_flags_self_reference(text):
    assert scan([text])


@pytest.mark.parametrize(
    "text",
    [
        "I worked in AI for six years before I burned out.",
        "I wrote code every night in my twenties.",
        "My friend Claude taught me to sail.",
        "We trained the model on weekends and it felt like magic.",
        "My twin sister and I fought constantly.",
    ],
)
def test_allows_real_life_mentions(text):
    assert scan([text]) == []
