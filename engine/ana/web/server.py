"""FastAPI server for the ANA journey viewer.

Endpoints
---------
GET  /                         single-page app (static, offline, no build step)
GET  /api/runs                 list loaded runs (metadata only)
POST /api/runs/upload          upload a JSONL run log (multipart `file`, or a raw
                               application/x-ndjson | application/jsonl body)
GET  /api/runs/{id}            a run's metadata + cycle records
GET  /api/runs/{id}/arc        Hero's-journey chapters + milestones
GET  /api/runs/{id}/debrief    the journey debrief (JSON + markdown; ?format=md
                               downloads the markdown)
POST /api/live                 start an in-process deterministic session
                               (Content-Type: application/json)
GET  /api/live/{id}/stream     Server-Sent Events: `cycle` per record (+ `arc`),
                               then `done` {status, cycles, closed}

Records are kept as plain JSON dicts (not re-serialised pydantic models) so any
extra fields other engineers add to `CycleRecord` survive the round-trip to the
browser, and records missing optional fields are still accepted. Unknown
top-level fields are kept up to a small per-record allowance.

Security model (local tool, no auth): requests must carry an allowed Host
header (DNS-rebinding guard) and, when an Origin header is present, it must be
same-origin (CSRF guard). State-changing endpoints require non-"simple"
content types where possible. Memory is bounded by a byte budget over the run
store; uploads are size-limited while streaming, before any parsing; SSE
streams are async (no threadpool worker per subscriber) and capped.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Literal, Optional
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from starlette.background import BackgroundTask

from ..contracts import CycleRecord

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MULTIPART_OVERHEAD = 64 * 1024     # boundary + part headers allowance
MAX_LIVE_BODY = 16 * 1024
MAX_LIVE_CYCLES = 200
MAX_CONCURRENT_LIVE = 4
MAX_RUNS = 32
# Memory budget, in *estimated retained bytes* of the parsed records (a
# conservative model of CPython object sizes: real CycleRecord logs come out
# at ~5x their JSONL size here, ~3.7x actual). A single run may not exceed
# MAX_RUN_BYTES (a 20 MB upload of real records is ~110 MB); the whole store
# is kept under MAX_STORE_BYTES by evicting the oldest uploads/finished live
# runs. Running live runs and --log preloads are never evicted.
MAX_RUN_BYTES = 160 * 1024 * 1024
MAX_STORE_BYTES = 480 * 1024 * 1024
MAX_EXTRA_BYTES = 4 * 1024          # unknown top-level fields kept per record
MAX_DEPTH = 64                      # JSON nesting depth accepted per line
MAX_PARALLEL_PARSES = 2
SSE_KEEPALIVE_S = 15.0
SSE_POLL_S = 1.0                    # disconnect-check cadence while idle
MAX_SUBSCRIBERS_PER_RUN = 8
MAX_SUBSCRIBERS_TOTAL = 64
DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "[::1]")
UPLOAD_RAW_TYPES = ("application/x-ndjson", "application/jsonl")
_KNOWN_FIELDS = frozenset(CycleRecord.model_fields)


# ─────────────────────────────────────────────────────────────
# Tolerant JSONL parsing
# ─────────────────────────────────────────────────────────────


@dataclass
class ParseResult:
    records: list[dict] = field(default_factory=list)
    skipped: int = 0        # lines that were not usable at all
    partial: int = 0        # kept, but did not fully validate as CycleRecord
    errors: list[str] = field(default_factory=list)  # first few problems, for the UI
    trimmed: int = 0        # kept, but unknown extra fields were dropped
    nbytes: int = 0         # estimated retained size of `records`
    too_big: bool = False   # stopped early: exceeded max_bytes


def _no_nan(token: str) -> None:
    # NaN/Infinity are legal in Python's json but not in browser JSON.
    return None


def _clean(obj: Any, _depth: int = 0) -> Any:
    if _depth > MAX_DEPTH:
        raise ValueError(f"nested deeper than {MAX_DEPTH} levels")
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v, _depth + 1) for v in obj]
    return obj


def _estimate_size(obj: Any) -> int:
    """Conservative estimate of the memory a parsed JSON value retains."""
    n, stack = 0, [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            n += 64 + 40 * len(x)
            for k, v in x.items():
                n += 49 + len(k)
                stack.append(v)
        elif isinstance(x, list):
            n += 56 + 8 * len(x)
            stack.extend(x)
        elif isinstance(x, str):
            n += 49 + len(x)
        else:
            n += 32
    return n


def _trim_extras(obj: dict, keep_extras: bool) -> tuple[dict, bool]:
    """Drop unknown top-level fields beyond a small allowance (all of them for
    partial records). Returns (record, trimmed?)."""
    extras = [k for k in obj if k not in _KNOWN_FIELDS]
    if not extras:
        return obj, False
    out = {k: v for k, v in obj.items() if k in _KNOWN_FIELDS}
    trimmed, budget = False, MAX_EXTRA_BYTES if keep_extras else 0
    for k in extras:
        size = len(k) + len(json.dumps(obj[k], separators=(",", ":")))
        if size <= budget:
            out[k] = obj[k]
            budget -= size
        else:
            trimmed = True
    return out, trimmed


def parse_jsonl(text: str, max_errors: int = 5,
                max_bytes: Optional[int] = None) -> ParseResult:
    """Parse JSONL into record dicts, skipping bad lines instead of failing.

    A line is kept if it validates as a CycleRecord (extra fields are fine, up
    to MAX_EXTRA_BYTES of them), or — more leniently — if it is an object with
    a `tick` and a `scene` object, so logs from a slightly different schema
    version still render (partial records keep only known fields). Lines
    nested deeper than MAX_DEPTH are skipped. With `max_bytes`, parsing stops
    (and `too_big` is set) once the kept records' estimated size exceeds it.
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
        full = True
        try:
            CycleRecord.model_validate(obj)
        except ValidationError as e:
            if isinstance(obj.get("scene"), dict) and isinstance(obj.get("tick"), (int, float)):
                full = False
            else:
                res.skipped += 1
                if len(res.errors) < max_errors:
                    res.errors.append(
                        f"line {lineno}: not a cycle record ({e.error_count()} schema errors)")
                continue
        except (ValueError, RecursionError, TypeError):
            full = False  # treated as partial if it has the shape; else skipped below
            if not (isinstance(obj.get("scene"), dict)
                    and isinstance(obj.get("tick"), (int, float))):
                res.skipped += 1
                continue
        try:
            rec, trimmed = _trim_extras(_clean(obj), keep_extras=full)
        except (ValueError, RecursionError, TypeError) as e:
            res.skipped += 1
            if len(res.errors) < max_errors:
                res.errors.append(f"line {lineno}: unusable ({str(e)[:80]})")
            continue
        if not full:
            res.partial += 1
        if trimmed:
            res.trimmed += 1
        res.nbytes += _estimate_size(rec)
        res.records.append(rec)
        if max_bytes is not None and res.nbytes > max_bytes:
            res.too_big = True
            break
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
    # Journey shape. `arcs[i]` is the arc snapshot taken right after record i
    # (live runs only; drives the SSE `arc` event).
    arcs: list[Optional[dict]] = field(default_factory=list, repr=False)
    closed: Optional[bool] = None          # live: did the journey come home?
    live_arc: Any = field(default=None, repr=False)  # live: the session's JourneyArc
    arc_payload: Optional[dict] = None     # captured / cached /arc response
    debrief: Optional[dict] = None         # captured / cached /debrief response
    trimmed: int = 0                       # records whose unknown extras were dropped
    nbytes: int = 0                        # estimated retained size of `records`
    subscribers: int = 0                   # open SSE streams (guarded by the store lock)
    # Async SSE subscribers: (event loop, asyncio.Event) pairs woken from the
    # producer thread via call_soon_threadsafe. Guarded by `cond`.
    _waiters: set = field(default_factory=set, repr=False)

    def meta(self) -> dict:
        with self.cond:
            n = len(self.records)
        return {
            "id": self.id, "name": self.name, "source": self.source,
            "status": self.status, "cycles": n, "skipped": self.skipped,
            "partial": self.partial, "trimmed": self.trimmed, "errors": self.errors,
            "error": self.error, "params": self.params, "created": self.created,
            "closed": self.closed,
        }

    def _wake(self) -> None:  # call with self.cond held
        self.cond.notify_all()
        for loop, ev in list(self._waiters):
            try:
                loop.call_soon_threadsafe(ev.set)
            except RuntimeError:  # loop closed
                self._waiters.discard((loop, ev))

    def push(self, record: dict, arc: Optional[dict] = None) -> None:
        size = _estimate_size(record) + (_estimate_size(arc) if arc else 0)
        with self.cond:
            self.records.append(record)
            self.arcs.append(arc)
            self.nbytes += size
            self._wake()

    def finish(self, status: str, error: Optional[str] = None) -> None:
        with self.cond:
            self.status = status  # type: ignore[assignment]
            self.error = error
            self._wake()

    def add_waiter(self, waiter: tuple) -> None:
        with self.cond:
            self._waiters.add(waiter)

    def remove_waiter(self, waiter: tuple) -> None:
        with self.cond:
            self._waiters.discard(waiter)


class StoreFull(Exception):
    pass


class RunStore:
    def __init__(self, max_runs: int = MAX_RUNS, max_bytes: int = MAX_STORE_BYTES,
                 max_live: int = MAX_CONCURRENT_LIVE,
                 max_subscribers_per_run: int = MAX_SUBSCRIBERS_PER_RUN,
                 max_subscribers_total: int = MAX_SUBSCRIBERS_TOTAL):
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self.max_runs = max_runs
        self.max_bytes = max_bytes
        self.max_live = max_live
        self.max_subscribers_per_run = max_subscribers_per_run
        self.max_subscribers_total = max_subscribers_total
        self._subscribers = 0

    def _evictable(self) -> list[Run]:
        # Oldest first; uploads before finished live runs. Running runs and
        # operator-preloaded files are never evicted.
        rank = {"upload": 0, "live": 1}
        return sorted((r for r in self._runs.values()
                       if r.status != "running" and r.source in rank),
                      key=lambda r: (rank[r.source], r.created))

    def total_bytes(self) -> int:
        with self._lock:
            return sum(r.nbytes for r in self._runs.values())

    def _make_room(self, incoming: int, force: bool) -> None:
        """Evict until one more run of `incoming` bytes fits. Raises StoreFull
        (without evicting anything) when it cannot fit and not `force`."""
        victims = self._evictable()
        count = len(self._runs) + 1
        total = sum(r.nbytes for r in self._runs.values()) + incoming
        drop: list[Run] = []
        for v in victims:
            if count <= self.max_runs and total <= self.max_bytes:
                break
            drop.append(v)
            count -= 1
            total -= v.nbytes
        if not force and (count > self.max_runs or total > self.max_bytes):
            raise StoreFull("run store is full (memory budget); try again later")
        for v in drop:
            del self._runs[v.id]

    def add(self, run: Run, force: bool = False) -> Run:
        """Register a run, evicting older ones to stay within the run-count
        and byte budgets. `force` (preloaded --log files) always adds."""
        with self._lock:
            self._make_room(run.nbytes, force)
            self._runs[run.id] = run
        return run

    def add_live(self, run: Run) -> bool:
        """Atomically check the concurrent-live cap and register `run`."""
        with self._lock:
            if sum(1 for r in self._runs.values() if r.status == "running") >= self.max_live:
                return False
            # A live run is at most MAX_LIVE_CYCLES records; reserve nothing up
            # front but still evict to honour the run-count cap.
            self._make_room(0, force=True)
            self._runs[run.id] = run
            return True

    def acquire_subscriber(self, run: Run) -> Optional[Callable[[], None]]:
        """Reserve an SSE slot; returns an idempotent release, or None if full."""
        with self._lock:
            if (run.subscribers >= self.max_subscribers_per_run
                    or self._subscribers >= self.max_subscribers_total):
                return None
            run.subscribers += 1
            self._subscribers += 1
        done = [False]

        def release() -> None:
            with self._lock:
                if done[0]:
                    return
                done[0] = True
                run.subscribers -= 1
                self._subscribers -= 1
        return release

    def subscriber_count(self) -> int:
        with self._lock:
            return self._subscribers

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
    # With until_close, `cycles` is the maximum: the run stops as soon as the
    # journey comes home (JourneyArc.should_close).
    cycles: int = Field(default=40, ge=1, le=MAX_LIVE_CYCLES)
    until_close: bool = True
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
        self.arc: Any = None  # set once the session exists

    def on_cycle(self, record: CycleRecord) -> None:
        snap = None
        if self.arc is not None:
            # The engine records the cycle into its arc before notifying observers.
            try:
                snap = {"chapter": self.arc.current_chapter(), "stage": self.arc.stage,
                        "furthest_stage": self.arc.furthest_stage,
                        "cycle": len(self.arc.entries), "tick": record.tick}
            except Exception:
                snap = None
        self.run.push(_clean(json.loads(record.model_dump_json())), snap)


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
        observer = _RunObserver(run)
        sess = build_session(cfg, identity=seed_identity(),
                             observers=[observer], driver=driver)
        observer.arc = getattr(sess, "arc", None)
        run.live_arc = observer.arc
        for _ in range(req.cycles):
            sess.step()
            if req.until_close and getattr(sess.engine, "closed", False):
                break
        _capture_live_journey(run, sess)
        run.finish("complete")
    except Exception as e:  # surfaced to the UI via the SSE `error` event
        run.finish("error", f"{type(e).__name__}: {e}")
    finally:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass


def _capture_live_journey(run: Run, sess: Any) -> None:
    """Snapshot the session's own debrief (with its cast) and arc before the
    session is closed. Failures here never fail the run: the endpoints fall
    back to rebuilding from the records."""
    run.closed = bool(getattr(sess.engine, "closed", False))
    cast = None
    try:
        cast = _clean(json.loads(json.dumps(sess.engine.npc.cast_summary(), default=str)))
    except Exception:
        cast = None
    try:
        if sess.arc is not None:
            run.arc_payload = _arc_payload(sess.arc)
    except Exception:
        run.arc_payload = None
    try:
        d = sess.debrief()
        run.debrief = {"debrief": _clean(d.model_dump()), "markdown": d.to_markdown(),
                       "cast": cast, "closed": run.closed, "cycles": len(run.records),
                       "from": "session"}
    except Exception:
        run.debrief = None
        if cast is not None:
            run.debrief = {"cast": cast}  # keep the cast for a records-based debrief


# ─────────────────────────────────────────────────────────────
# Arc + debrief (built from records for files/uploads)
# ─────────────────────────────────────────────────────────────


def _to_cycle_records(records: list[dict]) -> list[CycleRecord]:
    """Parse record dicts back to CycleRecords, skipping partial ones."""
    out: list[CycleRecord] = []
    for r in records:
        try:
            out.append(CycleRecord.model_validate(r))
        except (ValidationError, TypeError, ValueError):
            continue
    return out


def _arc_payload(arc: Any) -> dict:
    """JourneyArc -> JSON: to_dict() plus per-cycle entries and chapter ticks so
    the UI can map chapters onto record ticks."""
    base = arc.to_dict()
    entries = list(arc.entries)
    by_cycle = {e.cycle: e.tick for e in entries}
    chapters = []
    for c in arc.chapters():
        chapters.append({"stage": c["stage"], "name": c["name"], "start": c["start"],
                         "end": c["end"], "length": len(c["cycles"]),
                         "start_tick": by_cycle.get(c["start"]),
                         "end_tick": by_cycle.get(c["end"])})
    base["chapters"] = chapters
    base["entries"] = [{"cycle": e.cycle, "tick": e.tick, "stage": e.stage} for e in entries]
    base["stage_counts"] = {st: sum(1 for e in entries if e.stage == st)
                            for st in ("call", "threshold", "mentor", "ordeal", "reward", "return")}
    return _clean(base)


def _arc_from_records(records: list[dict]) -> dict:
    from ..engine.arc import JourneyArc

    arc = JourneyArc(max_cycles=None, max_chapters=10_000)
    for r in _to_cycle_records(records):
        arc.record(r, stage=r.scene.stage)  # exact stage when logged, else inferred
    return _arc_payload(arc)


def _debrief_from_records(records: list[dict], cast: Optional[list] = None) -> dict:
    from ..engine.arc import build_debrief

    recs = _to_cycle_records(records)
    d = build_debrief(recs, stages=[r.scene.stage for r in recs], cast=cast)
    return {"debrief": _clean(d.model_dump()), "markdown": d.to_markdown(),
            "cast": cast, "cycles": len(recs), "from": "records"}


def _sse(event: str, data: Any, event_id: Optional[int] = None) -> str:
    payload = json.dumps(data, separators=(",", ":"))
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {payload}\n\n"


async def _stream_run(run: Run, start: int = 0, request: Optional[Request] = None,
                      keepalive: float = SSE_KEEPALIVE_S,
                      poll: float = SSE_POLL_S,
                      release: Optional[Callable[[], None]] = None) -> AsyncIterator[str]:
    """Async SSE generator: waits on an asyncio.Event woken by the producer
    thread, so an idle subscriber holds no threadpool worker."""
    loop = asyncio.get_running_loop()
    ev = asyncio.Event()
    waiter = (loop, ev)
    run.add_waiter(waiter)
    try:
        i = max(0, start)
        idle = 0.0
        yield "retry: 3000\n\n"
        while True:
            ev.clear()  # before the snapshot, so a push after it is not lost
            with run.cond:
                batch = run.records[i:]
                arcs = run.arcs[i:i + len(batch)]
                status, error = run.status, run.error
            for k, rec in enumerate(batch):
                yield _sse("cycle", rec, i)
                snap = arcs[k] if k < len(arcs) else None
                if snap:
                    yield _sse("arc", snap)
                i += 1
            if batch:
                idle = 0.0
                continue
            if status != "running":
                if status == "error":
                    yield _sse("error", {"error": error})
                yield _sse("done", {"status": status, "cycles": i, "closed": run.closed})
                return
            if request is not None and await request.is_disconnected():
                return
            try:
                await asyncio.wait_for(ev.wait(), timeout=poll)
            except asyncio.TimeoutError:
                idle += poll
                if idle >= keepalive:
                    idle = 0.0
                    yield ": keepalive\n\n"
    finally:
        run.remove_waiter(waiter)
        if release is not None:
            release()


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────


def _load_file(store: RunStore, path: str | Path) -> Run:
    p = Path(path)
    data = p.read_bytes()[: MAX_UPLOAD_BYTES * 5]
    res = parse_jsonl(data.decode("utf-8", errors="replace"))
    return store.add(Run(id=_new_id("file"), name=p.name, source="file",
                         records=res.records, skipped=res.skipped,
                         partial=res.partial, errors=res.errors,
                         trimmed=res.trimmed, nbytes=res.nbytes), force=True)


_PARSE_SEM = threading.BoundedSemaphore(MAX_PARALLEL_PARSES)


def _parse_bounded(data: bytes, max_bytes: int) -> ParseResult:
    with _PARSE_SEM:  # bound the transient memory of concurrent big uploads
        return parse_jsonl(data.decode("utf-8", errors="replace"), max_bytes=max_bytes)


def _normalize_host(host: str) -> str:
    h = host.strip().lower()
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"  # bare IPv6 literal
    return h


def _host_only(host_header: str) -> str:
    """Host header -> hostname without port ('[::1]:8765' -> '[::1]')."""
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[: end + 1] if end != -1 else h
    return h.split(":", 1)[0]


def _mime(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


class _GuardMiddleware:
    """Pure-ASGI guard + security headers.

    * Host header must name an allowed host (blocks DNS rebinding).
    * If an Origin header is present it must be this server's own origin
      (blocks cross-site form/fetch POSTs, i.e. CSRF).
    """

    CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
           "script-src 'self'; connect-src 'self'; object-src 'none'; "
           "base-uri 'none'; frame-ancestors 'none'")

    def __init__(self, app: Any, allowed_hosts: Iterable[str]):
        self.app = app
        self.allowed = {_normalize_host(h) for h in allowed_hosts if h}

    async def _reject(self, send: Any, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        if _host_only(host) not in self.allowed:
            await self._reject(send, 400, "invalid host header")
            return
        origin = headers.get("origin")
        if origin is not None:
            o = origin.strip().lower()
            if o not in (f"http://{host.strip().lower()}", f"https://{host.strip().lower()}"):
                await self._reject(send, 403, "cross-origin request rejected")
                return
        path = scope.get("path", "")
        page = path == "/" or path.startswith("/static/")

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                hs = list(message.get("headers", []))
                present = {k.lower() for k, _ in hs}
                extra = [(b"x-content-type-options", b"nosniff"),
                         (b"referrer-policy", b"no-referrer")]
                if page:
                    extra.append((b"content-security-policy", self.CSP.encode()))
                hs += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": hs}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(logs: Optional[list[str]] = None,
               max_upload_bytes: int = MAX_UPLOAD_BYTES, *,
               allowed_hosts: Optional[Iterable[str]] = None,
               allow_llm: bool = False,
               max_store_bytes: int = MAX_STORE_BYTES,
               max_run_bytes: int = MAX_RUN_BYTES,
               max_runs: int = MAX_RUNS,
               max_subscribers_per_run: int = MAX_SUBSCRIBERS_PER_RUN,
               max_subscribers_total: int = MAX_SUBSCRIBERS_TOTAL) -> FastAPI:
    """Build the viewer app.

    `allowed_hosts` are accepted Host-header names *in addition to*
    localhost / 127.0.0.1 / [::1]. `allow_llm` lets POST /api/live request
    `use_llm=true` (default: 403).
    """
    app = FastAPI(title="ANA journey viewer", docs_url=None, redoc_url=None)
    store = RunStore(max_runs=max_runs, max_bytes=max_store_bytes,
                     max_subscribers_per_run=max_subscribers_per_run,
                     max_subscribers_total=max_subscribers_total)
    app.state.store = store
    app.state.allow_llm = allow_llm
    hosts = [*DEFAULT_ALLOWED_HOSTS, *(allowed_hosts or [])]
    app.state.allowed_hosts = sorted({_normalize_host(h) for h in hosts})
    app.add_middleware(_GuardMiddleware, allowed_hosts=hosts)

    for path in logs or []:
        _load_file(store, path)

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

    @app.get("/api/runs/{run_id}/arc")
    def get_arc(run_id: str) -> JSONResponse:
        run = store.get(run_id)
        if run.arc_payload is not None:
            return JSONResponse(run.arc_payload)
        if run.status == "running" and run.live_arc is not None:
            try:
                return JSONResponse(_arc_payload(run.live_arc))
            except Exception:
                pass  # mid-update; fall back to the records
        with run.cond:
            records = list(run.records)
        payload = _arc_from_records(records)
        if run.status != "running":
            run.arc_payload = payload
        return JSONResponse(payload)

    @app.get("/api/runs/{run_id}/debrief")
    def get_debrief(run_id: str, format: str = "json"):
        run = store.get(run_id)
        if run.status == "running":
            raise HTTPException(status_code=409,
                                detail="the journey is still running; the debrief comes at the end")
        data = run.debrief
        if not data or "debrief" not in data:
            with run.cond:
                records = list(run.records)
            cast = (data or {}).get("cast")
            data = _debrief_from_records(records, cast=cast)
            data["closed"] = run.closed
            run.debrief = data
        if format == "md":
            stem = re.sub(r"[^A-Za-z0-9_]+", "-", Path(run.name).stem)[:60].strip("-") or "journey"
            return PlainTextResponse(
                data["markdown"], media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{stem}-debrief.md"'})
        return JSONResponse(data)

    async def _read_body(request: Request, limit: int, too_big: HTTPException) -> bytes:
        """Read the body, counting bytes as they arrive (chunked or not)."""
        cl = request.headers.get("content-length")
        if cl is not None:
            if not (cl.isascii() and cl.isdigit() and len(cl) < 19):
                raise HTTPException(status_code=400, detail="bad content-length")
            if int(cl) > limit:
                raise too_big
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > limit:
                raise too_big
        return bytes(buf)

    @app.post("/api/runs/upload")
    async def upload(request: Request) -> JSONResponse:
        mime = _mime(request)
        multipart = mime == "multipart/form-data"
        if not multipart and mime not in UPLOAD_RAW_TYPES:
            raise HTTPException(
                status_code=415,
                detail="send multipart/form-data (field 'file') or an "
                       "application/x-ndjson / application/jsonl body")
        too_big = HTTPException(
            status_code=413,
            detail=f"upload exceeds {max_upload_bytes // (1024 * 1024)} MB limit")
        limit = max_upload_bytes + (MULTIPART_OVERHEAD if multipart else 0)
        body = await _read_body(request, limit, too_big)
        name = request.query_params.get("name") or "upload.jsonl"
        if multipart:
            # Parse multipart from the already-bounded bytes, never the socket.
            sent = [False]

            async def replay() -> dict:
                if not sent[0]:
                    sent[0] = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            form = await Request(request.scope, replay).form(max_files=1, max_fields=8)
            try:
                up = form.get("file")
                if up is None or isinstance(up, str):
                    raise HTTPException(status_code=400, detail="missing 'file' field")
                name = up.filename or name
                data = await up.read(max_upload_bytes + 1)
                if len(data) > max_upload_bytes:
                    raise too_big
            finally:
                await form.close()
            del body
        else:
            data = body
        res = await anyio.to_thread.run_sync(_parse_bounded, data, max_run_bytes)
        del data
        if res.too_big:
            raise HTTPException(status_code=413, detail=(
                f"upload is too large once parsed (over {max_run_bytes // (1024 * 1024)} MB "
                "in memory)"))
        if not res.records:
            raise HTTPException(status_code=422, detail={
                "message": "no usable cycle records found",
                "skipped": res.skipped, "errors": res.errors})
        try:
            run = store.add(Run(id=_new_id("up"), name=Path(name).name[:120] or "upload.jsonl",
                                source="upload", records=res.records, skipped=res.skipped,
                                partial=res.partial, errors=res.errors,
                                trimmed=res.trimmed, nbytes=res.nbytes))
        except StoreFull as e:
            raise HTTPException(status_code=507, detail=str(e))
        return JSONResponse(run.meta(), status_code=201)

    @app.post("/api/live")
    async def start_live(request: Request) -> JSONResponse:
        if _mime(request) != "application/json":
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
        body = await _read_body(request, MAX_LIVE_BODY,
                                HTTPException(status_code=413, detail="request body too large"))
        try:
            req = LiveRequest.model_validate_json(body) if body.strip() else LiveRequest()
        except ValidationError as e:
            return JSONResponse({"detail": json.loads(e.json(include_url=False))},
                                status_code=422)
        if req.use_llm and not allow_llm:
            raise HTTPException(status_code=403,
                                detail="use_llm is disabled on this server (start it with --allow-llm)")
        run = Run(id=_new_id("live"),
                  name=f"live · seed {req.seed} · {req.driver}" + (" · until home" if req.until_close else ""),
                  source="live", status="running", params=req.model_dump())
        if not store.add_live(run):
            raise HTTPException(status_code=429, detail="too many live runs in progress",
                                headers={"Retry-After": "5"})
        threading.Thread(target=_run_live, args=(run, req), daemon=True,
                         name=f"ana-live-{run.id}").start()
        return JSONResponse({"id": run.id, "run": run.meta(),
                             "stream": f"/api/live/{run.id}/stream"}, status_code=201)

    @app.get("/api/live/{run_id}/stream")
    async def stream(run_id: str, request: Request) -> StreamingResponse:
        run = store.get(run_id)
        start = 0
        last = request.headers.get("last-event-id", "")
        if last.isascii() and last.isdigit() and len(last) < 10:
            start = int(last) + 1
        release = store.acquire_subscriber(run)
        if release is None:
            raise HTTPException(status_code=429, detail="too many open streams",
                                headers={"Retry-After": "5"})
        return StreamingResponse(
            _stream_run(run, start, request, release=release),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(release))

    return app


app = create_app()
