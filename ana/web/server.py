"""FastAPI server for the ANA journey viewer.

Endpoints
---------
GET  /                         single-page app (static, offline, no build step)
GET  /api/runs                 list loaded runs (metadata only)
POST /api/runs/upload          upload a JSONL run log (multipart `file` or raw body)
GET  /api/runs/{id}            a run's metadata + cycle records
POST /api/live                 start an in-process deterministic session
GET  /api/live/{id}/stream     Server-Sent Events: one `cycle` event per record

Records are kept as plain JSON dicts (not re-serialised pydantic models) so any
extra fields other engineers add to `CycleRecord` survive the round-trip to the
browser, and records missing optional fields are still accepted.
"""
from __future__ import annotations

import json
import math
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..contracts import CycleRecord

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_LIVE_CYCLES = 200
MAX_CONCURRENT_LIVE = 4
MAX_RUNS = 64
SSE_KEEPALIVE_S = 15.0


# ─────────────────────────────────────────────────────────────
# Tolerant JSONL parsing
# ─────────────────────────────────────────────────────────────


@dataclass
class ParseResult:
    records: list[dict] = field(default_factory=list)
    skipped: int = 0        # lines that were not usable at all
    partial: int = 0        # kept, but did not fully validate as CycleRecord
    errors: list[str] = field(default_factory=list)  # first few problems, for the UI


def _no_nan(token: str) -> None:
    # NaN/Infinity are legal in Python's json but not in browser JSON.
    return None


def _clean(obj: Any) -> Any:
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def parse_jsonl(text: str, max_errors: int = 5) -> ParseResult:
    """Parse JSONL into record dicts, skipping bad lines instead of failing.

    A line is kept if it validates as a CycleRecord (extra fields are fine), or
    — more leniently — if it is an object with a `tick` and a `scene` object, so
    logs from a slightly different schema version still render.
    """
    res = ParseResult()
    if text.startswith("﻿"):
        text = text[1:]
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s, parse_constant=_no_nan)
        except (ValueError, RecursionError) as e:
            res.skipped += 1
            if len(res.errors) < max_errors:
                res.errors.append(f"line {lineno}: invalid JSON ({str(e)[:80]})")
            continue
        if not isinstance(obj, dict):
            res.skipped += 1
            if len(res.errors) < max_errors:
                res.errors.append(f"line {lineno}: not a JSON object")
            continue
        try:
            CycleRecord.model_validate(obj)
        except ValidationError as e:
            if isinstance(obj.get("scene"), dict) and isinstance(obj.get("tick"), (int, float)):
                res.partial += 1
            else:
                res.skipped += 1
                if len(res.errors) < max_errors:
                    res.errors.append(
                        f"line {lineno}: not a cycle record ({e.error_count()} schema errors)")
                continue
        res.records.append(_clean(obj))
    return res


# ─────────────────────────────────────────────────────────────
# Run store
# ─────────────────────────────────────────────────────────────


@dataclass
class Run:
    id: str
    name: str
    source: Literal["file", "upload", "live"]
    records: list[dict] = field(default_factory=list)
    status: Literal["complete", "running", "error"] = "complete"
    skipped: int = 0
    partial: int = 0
    errors: list[str] = field(default_factory=list)
    error: Optional[str] = None
    params: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    cond: threading.Condition = field(default_factory=threading.Condition, repr=False)

    def meta(self) -> dict:
        with self.cond:
            n = len(self.records)
        return {
            "id": self.id, "name": self.name, "source": self.source,
            "status": self.status, "cycles": n, "skipped": self.skipped,
            "partial": self.partial, "errors": self.errors, "error": self.error,
            "params": self.params, "created": self.created,
        }

    def push(self, record: dict) -> None:
        with self.cond:
            self.records.append(record)
            self.cond.notify_all()

    def finish(self, status: str, error: Optional[str] = None) -> None:
        with self.cond:
            self.status = status  # type: ignore[assignment]
            self.error = error
            self.cond.notify_all()


class RunStore:
    def __init__(self, max_runs: int = MAX_RUNS):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self.max_runs = max_runs

    def add(self, run: Run) -> Run:
        with self._lock:
            self._runs[run.id] = run
            # Evict the oldest finished runs beyond the cap.
            while len(self._runs) > self.max_runs:
                victims = [r for r in self._runs.values() if r.status != "running"]
                if not victims:
                    break
                oldest = min(victims, key=lambda r: r.created)
                del self._runs[oldest.id]
        return run

    def get(self, run_id: str) -> Run:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return run

    def all(self) -> list[Run]:
        with self._lock:
            return sorted(self._runs.values(), key=lambda r: r.created, reverse=True)

    def running(self) -> int:
        with self._lock:
            return sum(1 for r in self._runs.values() if r.status == "running")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ─────────────────────────────────────────────────────────────
# Live sessions
# ─────────────────────────────────────────────────────────────


class LiveRequest(BaseModel):
    cycles: int = Field(default=8, ge=1, le=MAX_LIVE_CYCLES)
    seed: int = 42
    use_llm: bool = False
    driver: Literal["auto", "scripted"] = "auto"


class _RunObserver:
    """Observer that pushes each completed CycleRecord into the run's buffer.

    The buffer is an append-only list guarded by a Condition: it behaves like a
    queue for the SSE consumer, but any number of subscribers (or a late one)
    can replay from the start.
    """

    def __init__(self, run: Run):
        self.run = run

    def on_cycle(self, record: CycleRecord) -> None:
        self.run.push(_clean(json.loads(record.model_dump_json())))


_LIVE_DATA_DIR = Path(tempfile.gettempdir()) / "ana_web_live"


def _run_live(run: Run, req: LiveRequest) -> None:
    from ..config import ANAConfig
    from ..engine.drivers import ScriptedDriver
    from ..session import build_session
    from .seed import DEMO_ACTIONS, seed_identity

    sess = None
    try:
        cfg = ANAConfig(use_llm=req.use_llm, max_cycles=req.cycles, seed=req.seed,
                        data_dir=_LIVE_DATA_DIR,
                        shard_db=str(_LIVE_DATA_DIR / "shards.db"))
        driver = ScriptedDriver(DEMO_ACTIONS) if req.driver == "scripted" else None
        sess = build_session(cfg, identity=seed_identity(),
                             observers=[_RunObserver(run)], driver=driver)
        for _ in range(req.cycles):
            sess.step()
        run.finish("complete")
    except Exception as e:  # surfaced to the UI via the SSE `error` event
        run.finish("error", f"{type(e).__name__}: {e}")
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass


def _sse(event: str, data: Any, event_id: Optional[int] = None) -> str:
    payload = json.dumps(data, separators=(",", ":"))
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {payload}\n\n"


def _stream_run(run: Run, start: int = 0,
                keepalive: float = SSE_KEEPALIVE_S) -> Iterator[str]:
    i = max(0, start)
    yield "retry: 3000\n\n"
    while True:
        with run.cond:
            if i >= len(run.records) and run.status == "running":
                run.cond.wait(timeout=keepalive)
            batch = run.records[i:]
            status, error = run.status, run.error
        for rec in batch:
            yield _sse("cycle", rec, i)
            i += 1
        if status != "running" and not batch:
            if status == "error":
                yield _sse("error", {"error": error})
            yield _sse("done", {"status": status, "cycles": i})
            return
        if not batch:
            yield ": keepalive\n\n"


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────


def _load_file(store: RunStore, path: str | Path) -> Run:
    p = Path(path)
    data = p.read_bytes()[: MAX_UPLOAD_BYTES * 5]
    res = parse_jsonl(data.decode("utf-8", errors="replace"))
    return store.add(Run(id=_new_id("file"), name=p.name, source="file",
                         records=res.records, skipped=res.skipped,
                         partial=res.partial, errors=res.errors))


def create_app(logs: Optional[list[str]] = None,
               max_upload_bytes: int = MAX_UPLOAD_BYTES) -> FastAPI:
    app = FastAPI(title="ANA journey viewer", docs_url=None, redoc_url=None)
    store = RunStore()
    app.state.store = store

    for path in logs or []:
        _load_file(store, path)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                "script-src 'self'; connect-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'")
        return resp

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/runs")
    def list_runs() -> dict:
        return {"runs": [r.meta() for r in store.all()]}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> JSONResponse:
        run = store.get(run_id)
        meta = run.meta()
        with run.cond:
            meta["records"] = list(run.records)
        return JSONResponse(meta)

    @app.post("/api/runs/upload")
    async def upload(request: Request) -> JSONResponse:
        too_big = HTTPException(
            status_code=413,
            detail=f"upload exceeds {max_upload_bytes // (1024 * 1024)} MB limit")
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > max_upload_bytes + 64 * 1024:
            raise too_big
        ctype = request.headers.get("content-type", "")
        name = request.query_params.get("name") or "upload.jsonl"
        buf = bytearray()
        if ctype.startswith("multipart/form-data"):
            form = await request.form(max_files=1, max_fields=8)
            up = form.get("file")
            if up is None or isinstance(up, str):
                raise HTTPException(status_code=400, detail="missing 'file' field")
            name = up.filename or name
            while chunk := await up.read(1024 * 1024):
                buf += chunk
                if len(buf) > max_upload_bytes:
                    raise too_big
            await up.close()
        else:
            async for chunk in request.stream():
                buf += chunk
                if len(buf) > max_upload_bytes:
                    raise too_big
        res = parse_jsonl(bytes(buf).decode("utf-8", errors="replace"))
        if not res.records:
            raise HTTPException(status_code=422, detail={
                "message": "no usable cycle records found",
                "skipped": res.skipped, "errors": res.errors})
        run = store.add(Run(id=_new_id("up"), name=Path(name).name[:120] or "upload.jsonl",
                            source="upload", records=res.records, skipped=res.skipped,
                            partial=res.partial, errors=res.errors))
        return JSONResponse(run.meta(), status_code=201)

    @app.post("/api/live")
    def start_live(req: Optional[LiveRequest] = None) -> JSONResponse:
        req = req or LiveRequest()
        if store.running() >= MAX_CONCURRENT_LIVE:
            raise HTTPException(status_code=429, detail="too many live runs in progress")
        run = store.add(Run(
            id=_new_id("live"),
            name=f"live · seed {req.seed} · {req.driver}",
            source="live", status="running", params=req.model_dump()))
        threading.Thread(target=_run_live, args=(run, req), daemon=True,
                         name=f"ana-live-{run.id}").start()
        return JSONResponse({"id": run.id, "run": run.meta(),
                             "stream": f"/api/live/{run.id}/stream"}, status_code=201)

    @app.get("/api/live/{run_id}/stream")
    def stream(run_id: str, request: Request) -> StreamingResponse:
        run = store.get(run_id)
        start = 0
        last = request.headers.get("last-event-id", "")
        if last.isdigit():
            start = int(last) + 1
        return StreamingResponse(
            _stream_run(run, start), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
