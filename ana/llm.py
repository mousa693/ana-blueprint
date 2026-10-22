"""
Thin wrapper over the Claude API.

Everything in ANA that talks to a model goes through ``LLM`` so that tests can
swap in a fake and so model choice, effort and refusal handling live in one
place.  Only one call shape is needed today: a system prompt plus messages in,
a validated Pydantic object out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol, Type, TypeVar

import anthropic
from anthropic.types.beta import BetaMessageParam
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMRefusal(RuntimeError):
    """The model (and its server-side fallback) declined the request."""


class LLMTruncated(RuntimeError):
    """The response hit max_tokens before the structured output was complete."""


class StructuredLLM(Protocol):
    """What the rest of ANA depends on.  ``LLM`` and test fakes both satisfy it."""

    def structured(
        self,
        *,
        system: str,
        messages: List[BetaMessageParam],
        schema: Type[T],
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> T: ...


@dataclass
class LLM:
    """Claude-backed implementation of ``StructuredLLM``.

    Requests are streamed (structured outputs can be long) and opt into the
    server-side refusal fallback, so a declined request is retried on
    Anthropic's recommended fallback model inside the same call.
    """

    model: str = DEFAULT_MODEL
    client: anthropic.Anthropic | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = anthropic.Anthropic()

    def structured(
        self,
        *,
        system: str,
        messages: List[BetaMessageParam],
        schema: Type[T],
        effort: str = "medium",
        max_tokens: int = 16000,
    ) -> T:
        assert self.client is not None
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            output_format=schema,
            output_config={"effort": effort},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            category = message.stop_details.category if message.stop_details else None
            raise LLMRefusal(f"request declined (category: {category})")
        if message.stop_reason == "max_tokens":
            raise LLMTruncated(f"hit max_tokens={max_tokens}; raise it and retry")
        parsed = message.parsed_output
        if parsed is None:
            raise RuntimeError(f"no structured output (stop_reason={message.stop_reason})")
        return parsed
