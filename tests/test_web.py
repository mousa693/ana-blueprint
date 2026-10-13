"""Tests for the ANA web viewer (ana/web). Hermetic: deterministic engine, no
network, in-process TestClient."""
from __future__ import annotations

import json
import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

from ana.web.server import create_app, parse_jsonl


def _records(n: int = 3) -> list[str]:
    """Generate n real CycleRecord JSON lines from a deterministic session."""
    from ana.config import ANAConfig
    from ana.session import build_session
    from ana.web.seed import seed_identity

    sess = build_session(ANAConfig(use_llm=False, max_cycles=n, seed=7),
                         identity=seed_identity())
    try:
        return [r.model_dump_json() for r in sess.run(n)]
    finally:
        sess.close()


@pytest.fixture(scope="module")
def lines() -> list[str]:
    return _records(3)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def _read_sse(resp, want: int, limit: int = 2000) -> tuple[list[dict], list[str]]:
    """Consume an SSE stream; return cycle payloads and the event names seen."""
    cycles, events, event, data = [], [], None, []
    for i, line in enumerate(resp.iter_lines()):
        if i > limit:
            break
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif line == "":
            if event:
                events.append(event)
                if event == "cycle":
                    cycles.append(json.loads("\n".join(data)))
                if event == "done" or (event == "cycle" and len(cycles) >= want
                                       and "done" in events):
                    break
            event, data = None, []
        if "done" in events:
            break
    return cycles, events


def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<title>ANA Journey Viewer</title>" in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_list_runs_empty(client):
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == {"runs": []}


def test_upload_and_fetch(client, lines):
    # one extra unknown field, one bad JSON line, one non-record object
    extra = json.loads(lines[0])
    extra["future_field"] = {"anything": 1}
    body = "\n".join([json.dumps(extra), "{not json", '{"hello": 1}', *lines[1:], ""])
    r = client.post("/api/runs/upload",
                    files={"file": ("journey.jsonl", body.encode(), "application/x-ndjson")})
    assert r.status_code == 201, r.text
    meta = r.json()
    assert meta["cycles"] == 3 and meta["skipped"] == 2 and meta["name"] == "journey.jsonl"

    runs = client.get("/api/runs").json()["runs"]
    assert [x["id"] for x in runs] == [meta["id"]]

    full = client.get(f"/api/runs/{meta['id']}").json()
    assert len(full["records"]) == 3
    assert full["records"][0]["future_field"] == {"anything": 1}  # extras preserved
    assert [x["tick"] for x in full["records"]] == [0, 1, 2]
    assert client.get("/api/runs/nope").status_code == 404


def test_upload_raw_body_and_rejects(client, lines):
    r = client.post("/api/runs/upload?name=raw.jsonl", content="\n".join(lines).encode(),
                    headers={"content-type": "application/x-ndjson"})
    assert r.status_code == 201 and r.json()["cycles"] == 3
    # nothing usable -> 422
    r = client.post("/api/runs/upload", content=b"garbage\n[1,2]\n",
                    headers={"content-type": "text/plain"})
    assert r.status_code == 422


def test_upload_size_limit(lines):
    small = TestClient(create_app(max_upload_bytes=1024))
    big = ("\n".join(lines * 5)).encode()
    r = small.post("/api/runs/upload", files={"file": ("big.jsonl", big, "text/plain")})
    assert r.status_code == 413


def test_parser_tolerates_missing_optional_fields(lines):
    rec = json.loads(lines[0])
    for k in ("omega_surprise", "alpha_surprise", "ts", "convergence"):
        rec.pop(k)
    partial = {"tick": 9, "scene": {"title": "only a scene"}}
    res = parse_jsonl("\n".join([json.dumps(rec), json.dumps(partial), "", "NaN"]))
    assert len(res.records) == 2 and res.partial == 1 and res.skipped == 1


def test_log_preloaded(tmp_path, lines):
    p = tmp_path / "run.jsonl"
    p.write_text("\n".join(lines) + "\n")
    c = TestClient(create_app(logs=[str(p)]))
    runs = c.get("/api/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["cycles"] == 3 and runs[0]["source"] == "file"


def test_live_run_streams_three_cycles(client):
    r = client.post("/api/live", json={"cycles": 3, "seed": 42, "use_llm": False,
                                       "driver": "auto"})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    with client.stream("GET", f"/api/live/{run_id}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        cycles, events = _read_sse(resp, want=3)
    assert len(cycles) == 3
    assert [c["tick"] for c in cycles] == [0, 1, 2]
    assert all(c["scene"]["beats"] for c in cycles)
    assert events[-1] == "done"

    meta = client.get(f"/api/runs/{run_id}").json()
    assert meta["status"] == "complete" and meta["cycles"] == 3

    # a late subscriber replays the finished run
    with client.stream("GET", f"/api/live/{run_id}/stream") as resp:
        again, _ = _read_sse(resp, want=3)
    assert len(again) == 3


def test_live_validates_body(client):
    assert client.post("/api/live", json={"cycles": 0}).status_code == 422
    assert client.post("/api/live", json={"cycles": 2, "driver": "nope"}).status_code == 422
