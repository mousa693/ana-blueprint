"""Model gateway — the replaceable bridge to a generative model.

ANA's "dream-maker" (the Generative Experience Engine) and some NPC dialogue go
through here. The gateway has two properties the blueprint cares about:

1. **Replaceable dependency.** Nothing else in ANA imports `anthropic`. Swap the
   provider here and the rest of the system is untouched.
2. **Always-runnable.** If no API key is configured, or the SDK is missing, or a
   call fails, the gateway reports `available == False` and callers fall back to
   their deterministic path. The simulation never hard-depends on the network.

It returns structured JSON when asked (for scene generation) using the Messages
API with Opus 5.5 and adaptive thinking, per current API guidance.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("ana.llm")


class ModelGateway:
    def __init__(self, model: str = "claude-opus-5-5", max_tokens: int = 2000,
                 enabled: bool = True):
        self.model = model
        self.max_tokens = max_tokens
        self.enabled = enabled
        self._client = None
        self._checked = False

    # -- availability ----------------------------------------------------
    def _ensure_client(self) -> Optional[Any]:
        if not self.enabled:
            return None
        if self._checked:
            return self._client
        self._checked = True
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            logger.info("No Anthropic credentials found; gateway runs in offline mode.")
            return None
        try:
            import anthropic  # type: ignore

            self._client = anthropic.Anthropic()
        except Exception as e:  # pragma: no cover - env dependent
            logger.warning("anthropic SDK unavailable (%s); offline mode.", e)
            self._client = None
        return self._client

    @property
    def available(self) -> bool:
        return self._ensure_client() is not None

    # -- calls -----------------------------------------------------------
    def complete(self, system: str, user: str) -> Optional[str]:
        """Return plain text, or None if the model is unavailable / errored."""
        client = self._ensure_client()
        if client is None:
            return None
        try:
            # Adaptive thinking per current Opus 5.5 guidance; stream for headroom.
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                system=system,
                messages=[{"role": "user", "content": user}],
            ) as stream:
                msg = stream.get_final_message()
            parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
            return "\n".join(parts).strip() or None
        except Exception as e:  # pragma: no cover - network dependent
            logger.warning("Model call failed (%s); falling back.", e)
            return None

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        """Ask for a JSON object and parse it defensively.

        We instruct JSON-only output rather than forcing tool use, because
        forced tool_choice is rejected on Opus 5.5. The parser tolerates stray
        prose by extracting the outermost JSON object.
        """
        text = self.complete(
            system + "\n\nRespond with a single valid JSON object and nothing else.",
            user,
        )
        if not text:
            return None
        return _extract_json(text)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first balanced {...} object out of a text blob."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        return None
    return None
