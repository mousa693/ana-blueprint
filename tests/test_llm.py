"""Verifies ANA's Claude path through the *real* anthropic SDK.

No network: an `httpx.MockTransport` stands in for the API. Each test asserts the
exact request ANA puts on the wire (model, beta header, fallbacks, structured
output schema, effort, no thinking override) and drives the response cases ANA
must handle — success, refusal, server error, truncation — end to end through
the generative engine, the interpreter, the autonomous driver, and a full session.
"""
from __future__ import annotations

import json
import warnings

import anthropic
import httpx2 as httpx
import pytest

warnings.filterwarnings("ignore")

from ana.config import ANAConfig
from ana.contracts import GenerationRequest, IdentitySnapshot, Scene, UserAction, WorldState
from ana.llm.gateway import FALLBACK_BETA, ModelGateway

SCENE = {
    "title": "The Lantern Room",
    "summary": "At the top of the stair the lamp is dark and a ship is calling.",
    "theme": "leadership",
    "intensity": 0.95,  # deliberately too high: the engine must clamp it
    "beats": [
        {"kind": "description", "text": "Salt wind through a cracked pane.", "speaker": None, "symbol": "lighthouse"},
        {"kind": "dialogue", "text": "Someone has to light it.", "speaker": "Old Fisher", "symbol": None},
        {"kind": "event", "text": "Three flares rise from the reef.", "speaker": None, "symbol": "flare"},
    ],
    "choices": ["Light the lamp yourself", "Call the village", "Wait for the keeper"],
    "symbols": ["lighthouse", "flare"],
}
OUTCOME = {
    "summary": "They lit the lamp and the ship turned.",
    "themes_touched": ["leadership"],
    "symbols": ["lighthouse"],
    "lesson": "I can act before I feel ready.",
    "success": True,
    "valence_delta": 0.9,  # out of range: must be clamped to 0.4
    "arousal_delta": 0.2,
    "stress_delta": -0.2,
    "trait_updates": [{"trait": "leadership_confidence", "delta": 0.25}],
}
ACTION = {"action": "I climb and light the lamp myself.", "choice_index": 0}


def _message(payload: dict | str, *, stop_reason: str = "end_turn", **extra) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    body = {
        "id": "msg_test", "type": "message", "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [] if stop_reason == "refusal" else [{"type": "text", "text": text}],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 80},
    }
    body.update(extra)
    return body


class Recorder:
    """Mock API: records requests, answers by inspecting the requested schema."""

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.requests: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        self.headers.append(request.headers)
        if self.mode == "500":
            return httpx.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}})
        if self.mode == "refusal":
            return httpx.Response(200, json=_message("", stop_reason="refusal",
                                  stop_details={"type": "refusal", "category": "bio", "explanation": None}))
        if self.mode == "max_tokens":
            return httpx.Response(200, json=_message('{"title": "cut', stop_reason="max_tokens"))
        props = body["output_config"]["format"]["schema"]["properties"]
        if "beats" in props:
            return httpx.Response(200, json=_message(SCENE))
        if "trait_updates" in props:
            return httpx.Response(200, json=_message(OUTCOME))
        return httpx.Response(200, json=_message(ACTION))


def make_gateway(mode: str = "ok") -> tuple[ModelGateway, Recorder]:
    rec = Recorder(mode)
    client = anthropic.Anthropic(
        api_key="test-key", base_url="https://api.test",
        http_client=httpx.Client(transport=httpx.MockTransport(rec)), max_retries=0)
    return ModelGateway(client=client), rec


def _request() -> GenerationRequest:
    return GenerationRequest(
        identity=IdentitySnapshot(active_themes=["leadership"]),
        world=WorldState(location="a lighthouse stair", motifs=["a distant lighthouse"]),
        directive="a leadership test", target_theme="leadership", target_intensity=0.6)


# ── wire format ─────────────────────────────────────────────────────
def test_request_shape_matches_current_api():
    gw, rec = make_gateway()
    data = gw.structured("sys", "user", {"type": "object", "properties": {"beats": {"type": "array"}},
                                         "required": ["beats"], "additionalProperties": False})
    assert data == SCENE
    body, headers = rec.requests[0], rec.headers[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in headers.get("anthropic-beta", "")
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body          # Opus 5.5: thinking is always on; never send disabled/budget
    assert "temperature" not in body       # sampling params are rejected on current models
    assert body["system"] == "sys"
    assert gw.stats["ok"] == 1 and gw.stats["input_tokens"] == 120


def test_scene_schema_is_strict_compatible():
    from ana.llm.schemas import ACTION_SCHEMA, OUTCOME_SCHEMA, SCENE_SCHEMA

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, node
                assert set(node["required"]) == set(node["properties"]), node
            for key in ("minimum", "maximum", "minLength", "maxLength"):
                assert key not in node
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    for schema in (SCENE_SCHEMA, OUTCOME_SCHEMA, ACTION_SCHEMA):
        check(schema)


# ── modules through the real SDK ────────────────────────────────────
def test_generative_engine_llm_scene_is_clamped():
    from ana.generative.engine import ExperienceEngine
    gw, rec = make_gateway()
    scene = ExperienceEngine(gateway=gw, use_llm=True).generate(_request())
    assert scene.source == "llm"
    assert scene.title == "The Lantern Room"
    assert [b.kind for b in scene.beats] == ["description", "dialogue", "event"]
    assert scene.beats[1].speaker == "Old Fisher"
    assert scene.intensity <= 0.6 + 0.15 + 1e-9      # clamped toward alignment's target
    assert "beats" in rec.requests[0]["output_config"]["format"]["schema"]["properties"]


def test_interpreter_llm_outcome_is_clamped():
    from ana.engine.interpret import Interpreter
    gw, _ = make_gateway()
    out = Interpreter(gateway=gw).interpret(
        Scene(theme="leadership", intensity=0.6), UserAction(raw="I light it."),
        WorldState(), IdentitySnapshot(active_themes=["leadership"]))
    assert out.success is True and out.lesson
    assert out.emotion_delta.valence == pytest.approx(0.4)   # 0.9 clamped
    assert out.trait_updates == {"leadership_confidence": 0.25}


def test_autonomous_driver_llm_action():
    from ana.engine.drivers import AutonomousDriver
    gw, rec = make_gateway()
    scene = Scene(choices=["Light the lamp yourself", "Call the village"], theme="leadership")
    act = AutonomousDriver(gateway=gw, use_llm=True).act(scene, IdentitySnapshot())
    assert act.choice_index == 0 and "lamp" in act.raw
    assert rec.requests[0]["output_config"]["effort"] == "low"


# ── failure ladder: every case degrades to the deterministic path ───
@pytest.mark.parametrize("mode,key", [("refusal", "refusals"), ("500", "errors"), ("max_tokens", "errors")])
def test_failures_fall_back_to_deterministic(mode, key):
    from ana.generative.engine import ExperienceEngine
    gw, _ = make_gateway(mode)
    scene = ExperienceEngine(gateway=gw, use_llm=True).generate(_request())
    assert scene.source == "deterministic" and scene.beats and scene.choices
    assert gw.stats[key]
    assert gw.last_error


def test_offline_gateway_is_unavailable(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", "/nonexistent-ana-home")
    assert ModelGateway().available is False
    assert ModelGateway(enabled=False).available is False


# ── a whole journey with the model in the loop ──────────────────────
def test_full_session_with_llm_in_the_loop():
    from ana.session import build_session
    gw, rec = make_gateway()
    sess = build_session(ANAConfig(use_llm=True, max_cycles=4), gateway=gw)
    sess.identity.active_themes = ["leadership"]
    records = sess.run()
    assert len(records) == 4
    assert all(r.scene.source == "llm" for r in records)
    assert all(r.outcome.lesson == "I can act before I feel ready." for r in records)
    kinds = {next(iter(set(q["output_config"]["format"]["schema"]["properties"]) & {"beats", "trait_updates", "choice_index"}))
             for q in rec.requests}
    assert kinds == {"beats", "trait_updates", "choice_index"}   # scene, outcome, action all via the model
    assert sess.gateway.stats["ok"] == len(rec.requests)
    sess.close()
