"""Tests for the ANA web viewer (ana/web). Hermetic: deterministic engine, no
network, in-process TestClient."""
from __future__ import annotations

import json
import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

from ana.web import server as web_server
from ana.web.server import create_app as _create_app, parse_jsonl


def create_app(*args, **kwargs):
    """TestClient sends `Host: testserver`; allow it here rather than in the
    production defaults."""
    kwargs.setdefault("allowed_hosts", ["testserver"])
    return _create_app(*args, **kwargs)


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
    r = client.post("/api/runs/upload", content="\n".join(lines).encode(),
                    headers={"content-type": "application/jsonl"})
    assert r.status_code == 201
    # nothing usable -> 422
    r = client.post("/api/runs/upload", content=b"garbage\n[1,2]\n",
                    headers={"content-type": "application/x-ndjson"})
    assert r.status_code == 422
    # CORS-"simple" raw content types are refused (CSRF)
    r = client.post("/api/runs/upload", content="\n".join(lines).encode(),
                    headers={"content-type": "text/plain"})
    assert r.status_code == 415


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


# ─────────────────────────────────────────────────────────────
# Security / robustness
# ─────────────────────────────────────────────────────────────


def test_host_header_allowlist():
    c = TestClient(_create_app())  # production defaults: testserver not allowed
    assert c.get("/api/runs").status_code == 400
    for host in ("127.0.0.1:8765", "localhost", "LOCALHOST:1", "[::1]:8765", "127.0.0.1"):
        assert c.get("/api/runs", headers={"host": host}).status_code == 200, host
    for host in ("evil.example", "evil.example:8765", "127.0.0.1.evil.example", "[::2]", ""):
        r = c.get("/api/runs", headers={"host": host})
        assert r.status_code == 400, host
    # rejected before the route runs: no live run is started
    r = c.post("/api/live", json={"cycles": 1}, headers={"host": "evil.example"})
    assert r.status_code == 400
    assert c.get("/api/runs", headers={"host": "localhost"}).json()["runs"] == []
    extra = TestClient(_create_app(allowed_hosts=["viewer.lan"]))
    assert extra.get("/api/runs", headers={"host": "viewer.lan:8765"}).status_code == 200


def test_cross_origin_requests_rejected(client):
    for origin in ("http://evil.example", "null", "http://testserver.evil", "http://localhost"):
        r = client.post("/api/live", json={"cycles": 1}, headers={"origin": origin})
        assert r.status_code == 403, origin
        r = client.post("/api/runs/upload", content=b'{"tick":1,"scene":{}}',
                        headers={"origin": origin, "content-type": "application/x-ndjson"})
        assert r.status_code == 403, origin
    assert client.get("/api/runs").json()["runs"] == []
    # same-origin (what the browser sends for the app's own fetches) is fine
    r = client.post("/api/live", json={"cycles": 1}, headers={"origin": "http://testserver"})
    assert r.status_code == 201
    assert client.get("/", headers={"origin": "http://testserver"}).status_code == 200


def test_live_requires_json_content_type(client, monkeypatch):
    monkeypatch.setattr(web_server, "_run_live", lambda run, req: run.finish("complete"))
    # an empty text/plain POST is a CORS "simple" request (no preflight)
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data", None):
        headers = {"content-type": ctype} if ctype else {}
        r = client.post("/api/live", content=b"", headers=headers)
        assert r.status_code == 415, ctype
    r = client.post("/api/live", content=b"", headers={"content-type": "application/json"})
    assert r.status_code == 201  # empty JSON body -> defaults
    assert r.json()["run"]["params"]["cycles"] == 40
    r = client.post("/api/live", content=b"{bad", headers={"content-type": "application/json"})
    assert r.status_code == 422
    r = client.post("/api/live", content=b" " * (64 * 1024),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_use_llm_gated_by_server_flag(monkeypatch):
    started = []
    monkeypatch.setattr(web_server, "_run_live",
                        lambda run, req: (started.append(req.use_llm), run.finish("complete")))
    c = TestClient(create_app())
    r = c.post("/api/live", json={"cycles": 1, "use_llm": True})
    assert r.status_code == 403 and "--allow-llm" in r.json()["detail"]
    assert c.get("/api/runs").json()["runs"] == [] and started == []
    assert c.post("/api/live", json={"cycles": 1, "use_llm": False}).status_code == 201

    c2 = TestClient(create_app(allow_llm=True))
    assert c2.post("/api/live", json={"cycles": 1, "use_llm": True}).status_code == 201
    import time
    for _ in range(50):
        if len(started) == 2:
            break
        time.sleep(0.02)
    assert started == [False, True]


def test_last_event_id_is_bounded(client):
    run_id = client.post("/api/live", json={"cycles": 3, "until_close": False}).json()["id"]
    with client.stream("GET", f"/api/live/{run_id}/stream") as resp:
        _read_events(resp)
    for value, want in (("9" * 5000, 3), ("²", 3), ("１", 3), ("-1", 3), ("0", 2), ("1", 1)):
        with client.stream("GET", f"/api/live/{run_id}/stream",
                           headers=[(b"last-event-id", value.encode("utf-8"))]) as resp:
            assert resp.status_code == 200, value
            events = _read_events(resp)
        assert sum(1 for e, _ in events if e == "cycle") == want, value
        assert events[-1][0] == "done"


def test_deeply_nested_line_is_skipped(client, lines):
    deep = '{"tick":1,"scene":{"title":"x"},"extra":' + "[" * 980 + "]" * 980 + "}"
    deep_scene = '{"tick":2,"scene":{"title":"x","k":' + "[" * 200 + "]" * 200 + "}}"
    res = parse_jsonl("\n".join([deep, deep_scene, lines[0]]))
    assert len(res.records) == 1 and res.skipped == 2
    body = "\n".join([deep, lines[0], deep_scene]).encode()
    r = client.post("/api/runs/upload", content=body,
                    headers={"content-type": "application/x-ndjson"})
    assert r.status_code == 201, r.text
    assert r.json()["cycles"] == 1 and r.json()["skipped"] == 2
    assert client.get(f"/api/runs/{r.json()['id']}").status_code == 200


def test_unknown_extras_are_bounded(lines):
    rec = json.loads(lines[0])
    rec["small"] = {"a": 1}
    rec["huge"] = "x" * 10_000
    partial = {"tick": 5, "scene": {"title": "p"}, "pad": [[]] * 1000}
    res = parse_jsonl("\n".join([json.dumps(rec), json.dumps(partial)]))
    full, part = res.records
    assert full["small"] == {"a": 1} and "huge" not in full
    assert "pad" not in part and part["scene"] == {"title": "p"}
    assert res.trimmed == 2 and res.partial == 1 and res.nbytes > 0


def test_store_byte_budget_evicts_oldest_uploads(lines):
    body = "\n".join(lines).encode()
    one = parse_jsonl(body.decode()).nbytes
    app = create_app(max_store_bytes=int(one * 2.5))
    c = TestClient(app)
    hdr = {"content-type": "application/x-ndjson"}
    ids = [c.post(f"/api/runs/upload?name=r{i}.jsonl", content=body, headers=hdr).json()["id"]
           for i in range(4)]
    kept = [r["id"] for r in c.get("/api/runs").json()["runs"]]
    assert sorted(kept) == sorted(ids[2:])  # the two oldest were evicted
    assert app.state.store.total_bytes() <= int(one * 2.5)

    # a running live run is never evicted; if nothing evictable is left, 507
    from ana.web.server import Run
    store = app.state.store
    store.add(Run(id="live-hog", name="hog", source="live", status="running",
                  nbytes=int(one * 1.2)), force=True)
    r = c.post("/api/runs/upload", content=body, headers=hdr)
    assert r.status_code == 201  # evicts the remaining uploads
    assert c.get("/api/runs/live-hog").status_code == 200
    store.get("live-hog").nbytes = int(one * 2)
    r = c.post("/api/runs/upload", content=body, headers=hdr)
    assert r.status_code == 507
    assert c.get("/api/runs/live-hog").status_code == 200


def test_upload_too_big_once_parsed(lines):
    body = "\n".join(lines * 4).encode()
    c = TestClient(create_app(max_run_bytes=parse_jsonl("\n".join(lines)).nbytes))
    r = c.post("/api/runs/upload", content=body, headers={"content-type": "application/x-ndjson"})
    assert r.status_code == 413
    assert c.get("/api/runs").json()["runs"] == []


def test_size_limit_on_streamed_body_without_content_length(lines):
    c = TestClient(create_app(max_upload_bytes=1024))
    consumed = []

    def raw():
        for _ in range(200):
            consumed.append(1)
            yield ("\n".join(lines) + "\n").encode()

    r = c.post("/api/runs/upload", content=raw(), headers={"content-type": "application/x-ndjson"})
    assert r.status_code == 413

    def multipart():
        yield (b'--BB\r\nContent-Disposition: form-data; name="file"; filename="a.jsonl"\r\n'
               b"Content-Type: application/octet-stream\r\n\r\n")
        for _ in range(200):
            yield ("\n".join(lines) + "\n").encode()
        yield b"\r\n--BB--\r\n"

    r = c.post("/api/runs/upload", content=multipart(),
               headers={"content-type": "multipart/form-data; boundary=BB"})
    assert r.status_code == 413
    r = c.post("/api/runs/upload", content=b"x", headers={"content-type": "application/x-ndjson",
                                                          "content-length": "9" * 5000})
    assert r.status_code in (400, 413)
    assert c.get("/api/runs").json()["runs"] == []


def test_sse_subscriber_cap():
    from ana.web.server import Run
    app = create_app(max_subscribers_per_run=2, max_subscribers_total=3)
    c = TestClient(app)
    store = app.state.store
    a = store.add(Run(id="live-a", name="a", source="live", status="running"))
    b = store.add(Run(id="live-b", name="b", source="live", status="running"))
    held = [store.acquire_subscriber(a), store.acquire_subscriber(a)]
    assert c.get("/api/live/live-a/stream").status_code == 429  # per-run cap
    held.append(store.acquire_subscriber(b))
    assert store.acquire_subscriber(b) is None                   # global cap
    assert c.get("/api/live/live-b/stream").status_code == 429
    for rel in held:
        rel()
        rel()  # idempotent
    assert store.subscriber_count() == 0
    b.finish("complete")
    with c.stream("GET", "/api/live/live-b/stream") as resp:
        assert resp.status_code == 200
        assert _read_events(resp)[-1][0] == "done"
    assert store.subscriber_count() == 0  # released when the stream ends


def test_sse_wakes_on_push_from_another_thread(lines):
    import threading
    import time
    from ana.web.server import Run
    app = create_app()
    c = TestClient(app)
    run = app.state.store.add(Run(id="live-t", name="t", source="live", status="running"))

    def produce():
        for ln in lines:
            time.sleep(0.05)
            run.push(json.loads(ln), {"stage": "call"})
        run.finish("complete")

    threading.Thread(target=produce, daemon=True).start()
    t0 = time.time()
    with c.stream("GET", "/api/live/live-t/stream") as resp:
        events = _read_events(resp)
    assert [e for e, _ in events].count("cycle") == 3
    assert events[-1] == ("done", {"status": "complete", "cycles": 3, "closed": None})
    assert time.time() - t0 < 5  # woken by the producer, not by keepalive timeouts
    assert not run._waiters


def test_live_cap_is_atomic():
    import threading
    from ana.web.server import Run, RunStore
    store = RunStore(max_live=4)
    results = []
    barrier = threading.Barrier(32)

    def go(i):
        barrier.wait()
        results.append(store.add_live(Run(id=f"live-{i}", name="x", source="live",
                                           status="running")))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(32)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 4 and store.running() == 4
