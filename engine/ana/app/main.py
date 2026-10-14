"""ASGI entry point: ``uvicorn ana.app.main:app`` or ``python -m ana.app``.

``create_app(settings=None, gateway=None)`` builds an independent app (own DB
engine, runtime cache, rate limiters) — tests build several. ``app`` is created
lazily on first access so importing this module has no side effects.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import AppContext, build_router, check_db
from .auth import SessionManager, configure_hashing
from .config import ConfigError, Settings
from .db import init_db, make_engine, make_sessionmaker
from .metering import MeteredGateway
from .runtimes import JourneyBusy, RuntimeCache
from .security import ApiError, GuardMiddleware, RateLimiter

logger = logging.getLogger("ana.app")

STATIC_DIR = Path(__file__).resolve().parent / "static"

_HTTP_CODES = {400: "bad_request", 401: "unauthenticated", 403: "forbidden", 404: "not_found",
               405: "method_not_allowed", 409: "conflict", 413: "too_large",
               415: "unsupported_media_type", 422: "invalid_request", 429: "rate_limited"}


def _err(status: int, code: str, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status,
                        headers=headers)


def _resolve_llm(settings: Settings, gateway: Any) -> tuple[Any, bool]:
    """(shared gateway or None, llm_available)."""
    if settings.use_llm == "off":
        return None, False
    if gateway is not None:
        return gateway, bool(gateway.available)
    if settings.use_llm == "auto" and not settings.anthropic_key_present:
        return None, False
    from ana.llm.gateway import ModelGateway

    gw = ModelGateway()
    ok = bool(gw.available)
    if not ok:
        logger.warning("ANA_USE_LLM=%s but no usable Anthropic client: journeys run "
                       "deterministically", settings.use_llm)
        return None, False
    return gw, True


def create_app(settings: Optional[Settings] = None, *, gateway: Any = None) -> FastAPI:
    if not logging.getLogger().handlers and not logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = settings or Settings.from_env()
    if not settings.secret_key:
        raise ConfigError("ANA_SECRET_KEY is required (set ANA_DEV=1 for local development)")
    gw, llm_available = _resolve_llm(settings, gateway)
    if gw is not None and not isinstance(gw, MeteredGateway):
        gw = MeteredGateway(gw)          # every model call counts (ana/app/metering.py)
    configure_hashing(settings.password_hash_concurrency, settings.password_hash_wait_seconds)
    if (os.environ.get("FORWARDED_ALLOW_IPS", "").strip() == "*"
            and not settings.client_ip_header and settings.trusted_proxy_hops <= 0):
        logger.warning("FORWARDED_ALLOW_IPS=* without ANA_CLIENT_IP_HEADER or "
                       "ANA_TRUSTED_PROXY_HOPS: uvicorn takes the client IP from the LEFTMOST "
                       "X-Forwarded-For entry, which clients can forge to dodge per-IP rate "
                       "limits. See docs/DEPLOY.md#client-ip.")
    logger.info("ANA effective config: %s", settings.describe())
    logger.info("ANA model: %s", "available" if llm_available else "off (deterministic journeys)")

    engine = make_engine(settings.database_url)
    init_db(engine)
    sm = make_sessionmaker(engine)
    cache = RuntimeCache(max_size=settings.runtime_cache_size, shard_db=settings.shard_db,
                         gateway_for=lambda: gw if llm_available else None)
    ctx = AppContext(
        settings=settings, sessionmaker=sm,
        sessions=SessionManager(settings.secret_key, settings.session_days),
        cache=cache, gateway=gw, llm_available=llm_available,
        limiter_auth_ip=RateLimiter(settings.auth_per_ip_per_minute),
        limiter_auth_email=RateLimiter(settings.auth_per_email_per_minute),
        limiter_act=RateLimiter(settings.act_per_minute),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Runtimes drive their own asyncio loops: close them off this loop.
        await asyncio.to_thread(cache.clear, flush=True)
        engine.dispose()

    app = FastAPI(title="ANA", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx
    app.state.engine = engine
    app.add_middleware(GuardMiddleware, allowed_hosts=sorted(settings.host_allowlist),
                       public_origin=settings.public_origin, max_body_bytes=settings.max_body_bytes,
                       hsts=bool(settings.public_origin and settings.public_origin.startswith("https://")))

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        return _err(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(JourneyBusy)
    async def _busy(_req: Request, _exc: JourneyBusy):
        return _err(409, "busy", "Still dreaming the last turn — one moment.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _err(exc.status_code, _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}"),
                    msg, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        errs = exc.errors()
        for e in errs:
            loc = [str(x) for x in e.get("loc", ())]
            if "text" in loc and e.get("type") == "string_too_long":
                return _err(422, "action_too_long", "Please keep what you do under 400 characters.")
        if any(e.get("type") in ("json_invalid", "model_attributes_type", "dict_type")
               and e.get("loc", ("",))[0] == "body" for e in errs):
            return _err(422, "invalid_json", "The request body must be a JSON object.")
        first = errs[0] if errs else {}
        field = ".".join(str(x) for x in first.get("loc", ())[1:]) or "body"
        return _err(422, "invalid_request", f"Please check {field}: {first.get('msg', 'invalid')}.")

    @app.exception_handler(Exception)
    async def _unhandled(_req: Request, exc: Exception):  # pragma: no cover - safety net
        logger.exception("unhandled error: %s", type(exc).__name__)
        return _err(500, "internal", "Something went wrong on our side. Please try again in a moment.")

    app.include_router(build_router())

    @app.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def healthz():
        ok_db = check_db(sm)
        body = {"ok": ok_db, "db": ok_db, "llm": llm_available}
        return JSONResponse(body, status_code=200 if ok_db else 503)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    index = STATIC_DIR / "index.html"

    def _index():
        if not index.is_file():
            return _err(404, "not_found", "The web app is not installed.")
        return FileResponse(index, media_type="text/html")

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def root():
        return _index()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def spa_fallback(path: str):
        # Hash routes never reach the server, but a pasted /home, /play/... etc.
        # should still load the app. Anything that looks like a file is a 404.
        if path.startswith(("api/", "static/")) or "." in path.rsplit("/", 1)[-1]:
            return _err(404, "not_found", "Not found.")
        return _index()

    return app


def __getattr__(name: str):
    if name == "app":
        app = create_app()
        globals()["app"] = app
        return app
    raise AttributeError(name)
