"""Request guard, security headers, body limits, rate limiting, API errors.

The guard is a pure-ASGI middleware (pattern from the dev viewer,
``ana/web/server.py``, copied rather than imported):

* the Host header must name an allowed host (DNS-rebinding guard);
* on every non-GET/HEAD/OPTIONS request an ``Origin`` (or, failing that, a
  ``Referer``) header, when present, must be this server's own origin, and
  ``Sec-Fetch-Site: cross-site`` is refused (CSRF, together with
  ``SameSite=Lax`` cookies);
* non-GET ``/api`` bodies must be JSON and at most ``max_body_bytes`` (counted
  as they stream in, chunked or not);
* every response gets the security headers; the strict CSP from spec §4.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Iterable, Optional
from urllib.parse import urlsplit

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; "
       "base-uri 'none'; form-action 'self'")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class ApiError(Exception):
    """Rendered as ``{"error": {"code", "message"}}`` with ``status``."""

    def __init__(self, status: int, code: str, message: str,
                 headers: Optional[dict[str, str]] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


def error_body(code: str, message: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message}}).encode()


def _normalize_host(host: str) -> str:
    h = host.strip().lower()
    if ":" in h and not h.startswith("["):
        h = f"[{h}]"
    return h


def host_only(host_header: str) -> str:
    h = host_header.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        return h[: end + 1] if end != -1 else h
    return h.split(":", 1)[0]


def _origin_of(url: str) -> Optional[str]:
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return None
    if not p.scheme or not p.netloc:
        return None
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


class GuardMiddleware:
    def __init__(self, app: Any, *, allowed_hosts: Iterable[str], public_origin: Optional[str],
                 max_body_bytes: int, hsts: bool = False):
        self.app = app
        self.allowed = {_normalize_host(h) for h in allowed_hosts if h}
        self.public_origin = public_origin.lower().rstrip("/") if public_origin else None
        self.max_body = max_body_bytes
        self.hsts = hsts

    async def _reject(self, send: Any, status: int, code: str, message: str) -> None:
        body = error_body(code, message)
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"cache-control", b"no-store"),
                                (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

    def _origin_ok(self, origin: str, host: str) -> bool:
        o = _origin_of(origin)
        if o is None:
            return False
        own = {f"http://{host}", f"https://{host}"}
        if self.public_origin:
            own.add(self.public_origin)
        return o in own

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        host = headers.get("host", "").strip().lower()
        if host_only(host) not in self.allowed:
            await self._reject(send, 400, "bad_host", "Invalid Host header.")
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        is_api = path.startswith("/api/")
        if method not in SAFE_METHODS:
            if headers.get("sec-fetch-site", "").lower() == "cross-site":
                await self._reject(send, 403, "bad_origin", "Cross-site request refused.")
                return
            origin = headers.get("origin")
            if origin is not None and origin.strip().lower() != "null":
                if not self._origin_ok(origin, host):
                    await self._reject(send, 403, "bad_origin", "Cross-origin request refused.")
                    return
            elif origin is not None:      # "Origin: null" (sandboxed / file://)
                await self._reject(send, 403, "bad_origin", "Cross-origin request refused.")
                return
            elif headers.get("referer"):
                if not self._origin_ok(headers["referer"], host):
                    await self._reject(send, 403, "bad_origin", "Cross-origin request refused.")
                    return
            if is_api:
                cl = headers.get("content-length")
                if cl is not None:
                    if not (cl.isascii() and cl.isdigit() and len(cl) < 19):
                        await self._reject(send, 400, "bad_request", "Bad Content-Length.")
                        return
                    if int(cl) > self.max_body:
                        await self._reject(send, 413, "too_large", "That request is too large.")
                        return
                has_body = (cl not in (None, "0")) or "transfer-encoding" in headers
                ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if has_body and ctype != "application/json":
                    await self._reject(send, 415, "unsupported_media_type", "Requests must be JSON.")
                    return
                body = bytearray()
                more = True
                while more:
                    message = await receive()
                    if message["type"] != "http.request":   # client went away
                        return
                    body += message.get("body", b"")
                    more = message.get("more_body", False)
                    if len(body) > self.max_body:
                        await self._reject(send, 413, "too_large", "That request is too large.")
                        return
                receive = _replay(bytes(body), receive)

        page = not is_api

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                hs = list(message.get("headers", []))
                present = {k.lower() for k, _ in hs}
                extra = [(b"x-content-type-options", b"nosniff"),
                         (b"referrer-policy", b"same-origin"),
                         (b"x-frame-options", b"DENY"),
                         (b"cross-origin-opener-policy", b"same-origin"),
                         (b"cross-origin-resource-policy", b"same-origin"),
                         (b"permissions-policy",
                          b"camera=(), microphone=(), geolocation=(), interest-cohort=()"),
                         (b"content-security-policy", CSP.encode())]
                if self.hsts:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if is_api or path == "/healthz":
                    extra.append((b"cache-control", b"no-store"))
                elif page and not path.startswith("/static/"):
                    extra.append((b"cache-control", b"no-cache"))
                hs += [(k, v) for k, v in extra if k not in present]
                message = {**message, "headers": hs}
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _replay(body: bytes, receive: Any):
    sent = False

    async def wrapped() -> Any:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return wrapped


# ─────────────────────────────────────────────────────────────
# Rate limiting — in-process token buckets (v1: one worker)
# ─────────────────────────────────────────────────────────────

class RateLimiter:
    """``allow(key)`` takes one token from ``key``'s bucket of ``capacity``
    tokens refilled at ``per_minute`` per minute."""

    def __init__(self, per_minute: int, capacity: Optional[int] = None, *, clock=time.monotonic):
        self.rate = per_minute / 60.0
        self.capacity = float(capacity if capacity is not None else per_minute)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._ops = 0

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            tokens, ts = self._buckets.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - ts) * self.rate)
            ok = tokens >= 1.0
            if ok:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            self._ops += 1
            if self._ops % 1024 == 0:
                self._prune(now)
            return ok

    def retry_after(self) -> int:
        return max(1, int(round(1.0 / self.rate))) if self.rate > 0 else 60

    def _prune(self, now: float) -> None:
        full = self.capacity / self.rate if self.rate else 0
        dead = [k for k, (_, ts) in self._buckets.items() if now - ts > full]
        for k in dead:
            del self._buckets[k]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
