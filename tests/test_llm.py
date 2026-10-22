from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from ana.llm import FALLBACK_BETA, LLM, LLMRefusal, LLMTruncated


class Out(BaseModel):
    x: int


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


def client_returning(message, seen):
    def stream(**kwargs):
        seen.update(kwargs)
        return FakeStream(message)

    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))


def test_request_shape_and_parsed_output():
    seen = {}
    msg = SimpleNamespace(stop_reason="end_turn", stop_details=None, parsed_output=Out(x=3))
    llm = LLM(client=client_returning(msg, seen))
    out = llm.structured(system="s", messages=[{"role": "user", "content": "hi"}], schema=Out, effort="high")
    assert out == Out(x=3)
    assert seen["model"] == "claude-opus-5-5"
    assert seen["output_format"] is Out
    assert seen["output_config"] == {"effort": "high"}
    assert seen["betas"] == [FALLBACK_BETA]
    assert seen["fallbacks"] == "default"


def test_refusal_and_truncation_raise():
    seen = {}
    refusal = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="bio"), parsed_output=None)
    with pytest.raises(LLMRefusal, match="bio"):
        LLM(client=client_returning(refusal, seen)).structured(system="s", messages=[], schema=Out)
    cut = SimpleNamespace(stop_reason="max_tokens", stop_details=None, parsed_output=None)
    with pytest.raises(LLMTruncated):
        LLM(client=client_returning(cut, seen)).structured(system="s", messages=[], schema=Out)
