"""Model gateway — the single, replaceable bridge to a generative model.

ANA's dream-maker (Generative Experience Engine), the interpreter, and the
autonomous driver can use a model through this class. Three properties matter:

1. **Replaceable dependency.** Nothing else in ANA imports `anthropic`. Swap the
   provider here and the rest of the system is untouched.
2. **Always-runnable.** With no credentials, no SDK, a refusal, or any API error,
   calls return ``None`` and callers fall back to their deterministic paths. The
   simulation never hard-depends on the network.
3. **Correct against the current API.** Requests target Claude Opus 5.5 with:
     * structured outputs (`output_config.format` JSON schema) so scene /
       outcome payloads are guaranteed to parse — no prose-scraping;
     * an explicit `effort` (Opus 5.5 defaults to `medium`; we set it on purpose);
     * adaptive thinking left at its default (Opus 5.5 thinking is always on and
       rejects `disabled`/budgets, so the parameter is omitted);
     * server-side refusal fallbacks (`fallbacks: "default"` under beta
       `server-side-fallback-2026-07-01`) so a classifier false positive on dark
       symbolic material degrades to another model instead of an outage;
     * `stop_reason == "refusal"` checked before content is read.

Every call is counted in `stats` (calls, successes, refusals, fallbacks used,
errors by class, input/output tokens) for `ana-run doctor` / `llm-check`.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ana.llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _credentials_present() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("ANTHROPIC_PROFILE"):
        return True
    # `ant auth login` stores a profile the SDK picks up with no env var.
    return (Path.home() / ".config" / "anthropic").exists()


class ModelGateway:
    def __init__(self, model: str = "claude-opus-5-5", max_tokens: int = 16000,
                 enabled: bool = True, effort: str = "medium",
                 use_fallbacks: bool = True, client: Any = None):
        self.model = model
        self.max_tokens = max_tokens
        self.enabled = enabled
        self.effort = effort
        self.use_fallbacks = use_fallbacks
        self._client = client
        self._checked = client is not None
        self.last_error: Optional[str] = None
        self.stats: dict[str, Any] = {
            "calls": 0, "ok": 0, "refusals": 0, "fallbacks_used": 0,
            "errors": {}, "input_tokens": 0, "output_tokens": 0,
        }

    # -- availability ----------------------------------------------------
    def _ensure_client(self) -> Optional[Any]:
        if not self.enabled:
            return None
        if self._checked:
            return self._client
        self._checked = True
        if not _credentials_present():
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

    # -- core call -------------------------------------------------------
    def _call(self, system: str, user: str, *, schema: Optional[dict],
              effort: Optional[str], max_tokens: Optional[int]) -> Optional[str]:
        """Run one request; return the text of the answer, or None on any failure."""
        client = self._ensure_client()
        if client is None:
            return None
        self.stats["calls"] += 1
        output_config: dict[str, Any] = {"effort": effort or self.effort}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        try:
            import anthropic  # type: ignore

            try:
                resp = client.beta.messages.create(**kwargs)
            except anthropic.RateLimitError as e:
                return self._fail("RateLimitError", e)
            except anthropic.AuthenticationError as e:
                return self._fail("AuthenticationError", e)
            except anthropic.BadRequestError as e:
                return self._fail("BadRequestError", e)
            except anthropic.APIStatusError as e:
                return self._fail(f"APIStatusError:{e.status_code}", e)
            except anthropic.APIConnectionError as e:
                return self._fail("APIConnectionError", e)
        except Exception as e:  # SDK missing or unexpected failure
            return self._fail(type(e).__name__, e)

        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.stats["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
            self.stats["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)
            iters = getattr(usage, "iterations", None) or []
            if any(getattr(it, "type", None) == "fallback_message" for it in iters):
                self.stats["fallbacks_used"] += 1

        # Check the stop reason BEFORE reading content.
        if getattr(resp, "stop_reason", None) == "refusal":
            self.stats["refusals"] += 1
            details = getattr(resp, "stop_details", None)
            cat = getattr(details, "category", None) if details else None
            self.last_error = f"refusal ({cat})"
            logger.info("Model declined (category=%s); falling back to deterministic.", cat)
            return None
        if getattr(resp, "stop_reason", None) == "max_tokens":
            return self._fail("max_tokens", RuntimeError("response truncated"))

        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        text = "\n".join(t for t in texts if t).strip()
        if not text:
            return self._fail("empty", RuntimeError("no text content"))
        self.stats["ok"] += 1
        self.last_error = None
        return text

    def _fail(self, kind: str, err: Exception) -> None:
        self.stats["errors"][kind] = self.stats["errors"].get(kind, 0) + 1
        self.last_error = f"{kind}: {str(err)[:160]}"
        logger.warning("Model call failed (%s); falling back.", self.last_error)
        return None

    # -- public API ------------------------------------------------------
    def structured(self, system: str, user: str, schema: dict, *,
                   effort: Optional[str] = None,
                   max_tokens: Optional[int] = None) -> Optional[dict]:
        """Return a dict conforming to `schema`, or None."""
        text = self._call(system, user, schema=schema, effort=effort, max_tokens=max_tokens)
        if text is None:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = _extract_json(text)
        if not isinstance(data, dict):
            self._fail("invalid_json", ValueError("unparseable structured output"))
            return None
        return data

    def complete(self, system: str, user: str, *, effort: Optional[str] = None,
                 max_tokens: Optional[int] = None) -> Optional[str]:
        """Return plain text, or None."""
        return self._call(system, user, schema=None, effort=effort, max_tokens=max_tokens)

    def complete_json(self, system: str, user: str) -> Optional[dict]:
        """Legacy schema-less JSON request (kept for callers without a schema)."""
        text = self.complete(system + "\n\nRespond with a single valid JSON object and nothing else.", user)
        return _extract_json(text) if text else None


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
