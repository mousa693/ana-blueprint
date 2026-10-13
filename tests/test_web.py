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


def _read_events(resp, limit: int = 5000) -> list[tuple[str, dict]]:
    """Consume an SSE stream to `done`; return (event, payload) pairs."""
    out, event, data = [], None, []
    for i, line in enumerate(resp.iter_lines()):
        if i > limit:
            break
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data.append(line.split(":", 1)[1].strip())
        elif line == "":
            if event:
                out.append((event, json.loads("\n".join(data)) if data else {}))
                if event == "done":
                    break
            event, data = None, []
    return out


def test_debrief_and_arc_for_uploaded_run(client, lines):
    # an old-style log: no stage on the scene, newer identity fields missing
    old = []
    for ln in lines:
        rec = json.loads(ln)
        rec["scene"].pop("stage", None)
        rec["request"].pop("stage", None)
        for k in ("insights", "recurring_symbols", "callback_symbols", "fulfilled_visions"):
            rec["identity_before"].pop(k, None)
        old.append(json.dumps(rec))
    partial = json.dumps({"tick": 99, "scene": {"title": "only a scene"}})
    body = "\n".join([*old, partial]).encode()
    meta = client.post("/api/runs/upload", files={"file": ("old.jsonl", body, "text/plain")}).json()
    assert meta["cycles"] == 4 and meta["partial"] == 1

    arc = client.get(f"/api/runs/{meta['id']}/arc")
    assert arc.status_code == 200
    a = arc.json()
    assert a["cycles"] == 3  # the partial record is skipped
    assert a["chapters"] and a["chapters"][0]["start_tick"] == 0
    assert a["milestones"] and a["chapter"].startswith("Chapter")
    assert [e["tick"] for e in a["entries"]] == [0, 1, 2]
    assert sum(a["stage_counts"].values()) == 3

    d = client.get(f"/api/runs/{meta['id']}/debrief")
    assert d.status_code == 200
    data = d.json()
    assert data["debrief"]["title"] and data["debrief"]["arc_summary"]
    assert data["debrief"]["chapters"]
    assert data["markdown"].startswith("# ")
    assert data["cast"] is None

    md = client.get(f"/api/runs/{meta['id']}/debrief?format=md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md.headers["content-disposition"]
    assert md.text == data["markdown"]

    assert client.get("/api/runs/nope/debrief").status_code == 404
    assert client.get("/api/runs/nope/arc").status_code == 404


def test_live_until_close_reports_closed_and_debrief(client):
    r = client.post("/api/live", json={"cycles": 24, "seed": 42, "until_close": True})
    assert r.status_code == 201, r.text
    run_id = r.json()["id"]
    assert r.json()["run"]["params"]["until_close"] is True
    with client.stream("GET", f"/api/live/{run_id}/stream") as resp:
        events = _read_events(resp)
    names = [e for e, _ in events]
    cycles = [p for e, p in events if e == "cycle"]
    arcs = [p for e, p in events if e == "arc"]
    done = events[-1]
    assert done[0] == "done"
    assert done[1]["closed"] is True and done[1]["status"] == "complete"
    assert done[1]["cycles"] == len(cycles) < 24
    # cycle events unchanged; one arc event after each
    assert names.count("arc") == len(cycles)
    assert all("scene" in c and "tick" in c for c in cycles)
    assert arcs[-1]["stage"] == "return" and arcs[-1]["chapter"].startswith("Chapter")
    assert cycles[-1]["scene"]["stage"] == "return"

    meta = client.get(f"/api/runs/{run_id}").json()
    assert meta["closed"] is True and meta["cycles"] == len(cycles)

    d = client.get(f"/api/runs/{run_id}/debrief").json()
    assert d["from"] == "session" and d["closed"] is True
    assert d["debrief"]["chapters"] and d["debrief"]["lessons"]
    assert d["debrief"]["companions"]
    assert d["cast"] and {"name", "role", "times_met", "trend"} <= set(d["cast"][0])
    assert "## " in d["markdown"]

    a = client.get(f"/api/runs/{run_id}/arc").json()
    assert a["stage"] == "return" and a["furthest_stage"] == "return"
    assert a["cycles"] == len(cycles) and a["milestones"]
    assert a["chapters"][-1]["stage"] == "return"


def test_live_without_until_close_runs_all_cycles(client):
    r = client.post("/api/live", json={"cycles": 3, "until_close": False})
    run_id = r.json()["id"]
    with client.stream("GET", f"/api/live/{run_id}/stream") as resp:
        events = _read_events(resp)
    assert events[-1][0] == "done" and events[-1][1]["cycles"] == 3
    assert events[-1][1]["closed"] is False


def test_debrief_conflict_while_running():
    from ana.web.server import Run

    app = create_app()
    c = TestClient(app)
    run = app.state.store.add(Run(id="live-x", name="x", source="live", status="running"))
    assert c.get(f"/api/runs/{run.id}/debrief").status_code == 409
    assert c.get(f"/api/runs/{run.id}/arc").json()["cycles"] == 0
