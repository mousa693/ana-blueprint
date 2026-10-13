"""JSON schemas for ANA's structured model outputs.

The gateway asks the model for these shapes via structured outputs
(`output_config.format = {"type": "json_schema", ...}`), so a well-formed
response is guaranteed to parse. The schemas stay inside the conservative subset
structured outputs support: every object closes with `additionalProperties:
false`, every property is listed in `required`, optional values are expressed as
`anyOf [T, null]`, and numeric ranges are clamped in code rather than with
`minimum`/`maximum`.
"""
from __future__ import annotations

_NULLABLE_STR = {"anyOf": [{"type": "string"}, {"type": "null"}]}

SCENE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "theme": {"type": "string"},
        "intensity": {"type": "number"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": [
                        "description", "dialogue", "event", "choice", "synchronicity"]},
                    "text": {"type": "string"},
                    "speaker": _NULLABLE_STR,
                    "symbol": _NULLABLE_STR,
                },
                "required": ["kind", "text", "speaker", "symbol"],
                "additionalProperties": False,
            },
        },
        "choices": {"type": "array", "items": {"type": "string"}},
        "symbols": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "theme", "intensity", "beats", "choices", "symbols"],
    "additionalProperties": False,
}

OUTCOME_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "themes_touched": {"type": "array", "items": {"type": "string"}},
        "symbols": {"type": "array", "items": {"type": "string"}},
        "lesson": _NULLABLE_STR,
        "success": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "valence_delta": {"type": "number"},
        "arousal_delta": {"type": "number"},
        "stress_delta": {"type": "number"},
        "trait_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trait": {"type": "string"},
                    "delta": {"type": "number"},
                },
                "required": ["trait", "delta"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "themes_touched", "symbols", "lesson", "success",
                 "valence_delta", "arousal_delta", "stress_delta", "trait_updates"],
    "additionalProperties": False,
}

ACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "choice_index": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    },
    "required": ["action", "choice_index"],
    "additionalProperties": False,
}
