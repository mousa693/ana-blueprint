"""HTTP API (docs/PRODUCT_SPEC.md §3). All routes are JSON; errors are
``{"error": {"code", "message"}}``.

Every query that touches a person's data is filtered by the authenticated
user's id; a journey that exists but belongs to someone else is a 404 (never a
403), so existence does not leak.

Handlers are plain ``def`` (FastAPI runs them in its threadpool) because the
engine and the database calls are blocking.
"""
from __future__ import annotations

import inspect
import json
import logging
import secrets
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from ana import onboarding as onboarding_mod
from ana import safety as safety_mod
from ana.contracts import CycleRecord, UserAction
from ana.engine.arc import normalize_stage
from ana.runtime import (IdentitySeed, JourneyClosedError, JourneyRuntime, NoPendingSceneError,
                         PendingScene)
from ana.subsystems.alignment import mentions_forbidden

from . import metering, quotas, shards
from .auth import (COOKIE_NAME, PASSWORD_MAX, PASSWORD_MIN, SessionManager, burn_hash, find_user,
                   hash_password, needs_rehash, normalise_email, password_ok, valid_email,
                   verify_password)
from .config import Settings
from .models import (AuthSession, Cycle, DebriefRow, Identity, Journey, SafetyEvent, Usage, User,
                     utcnow)
from .redact import redact_title, redact_value
from .runtimes import JourneyBusy, RuntimeCache
from .security import ApiError, RateLimiter, client_ip
from .userlocks import UserBusy, UserLocks

logger = logging.getLogger("ana.app.api")

MAX_ACTION_CHARS = 400
MAX_NAME = 40
MAX_FORBIDDEN_ITEMS = 50
MAX_FORBIDDEN_CHARS = 60
OPEN_STATUSES = ("active", "paused_safety")
DONE_STATUSES = ("closed", "ended")


# ─────────────────────────────────────────────────────────────
# Application context
# ─────────────────────────────────────────────────────────────

@dataclass
class AppContext:
    settings: Settings
    sessionmaker: sessionmaker
    sessions: SessionManager
    cache: RuntimeCache
    gateway: Any                     # shared ModelGateway, or None
    llm_available: bool
    limiter_auth_ip: RateLimiter
    limiter_auth_email: RateLimiter          # FAILED logins per (email, ip)
    limiter_act: RateLimiter
    limiter_auth_fail_global: RateLimiter = None   # FAILED logins per email, all IPs
    limiter_create: RateLimiter = None
    limiter_identity: RateLimiter = None
    limiter_onboarding: RateLimiter = None
    limiter_safety: RateLimiter = None       # model safety screens per user
    user_locks: UserLocks = field(default_factory=UserLocks)
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        s = self.settings
        if self.limiter_auth_fail_global is None:
            self.limiter_auth_fail_global = RateLimiter(s.auth_email_global_per_minute,
                                                        s.auth_email_global_burst)
        if self.limiter_create is None:
            self.limiter_create = RateLimiter(s.create_per_minute)
        if self.limiter_identity is None:
            self.limiter_identity = RateLimiter(s.identity_per_minute)
        if self.limiter_onboarding is None:
            self.limiter_onboarding = RateLimiter(s.onboarding_per_minute)
        if self.limiter_safety is None:
            per_hour = max(0, s.safety_model_per_hour)
            self.limiter_safety = RateLimiter(per_hour / 60.0, per_hour)

    def model_gateway(self):
        return self.gateway if self.llm_available else None


# ─────────────────────────────────────────────────────────────
# Model metering (every call counts against the global budget)
# ─────────────────────────────────────────────────────────────

@contextmanager
def metered(ctx: AppContext, db: Session) -> Iterator[metering.Meter]:
    """Count the model calls this block makes and settle the global counter:
    ``+calls - reserved`` (a reservation that made no call is refunded)."""
    m = metering.Meter()
    tok = metering.activate(m)
    try:
        yield m
    except BaseException:
        try:
            db.rollback()
        except Exception:  # pragma: no cover
            pass
        raise
    finally:
        metering.deactivate(tok)
        delta = m.calls - m.reserved
        take = getattr(ctx.gateway, "take_stray", None)
        if take is not None:
            delta += take()
        if delta:
            try:
                quotas.adjust_llm(db, delta)
            except Exception:  # pragma: no cover - accounting must not break a reply
                logger.exception("settling the model budget failed (delta=%d)", delta)
                try:
                    db.rollback()
                except Exception:
                    pass


def reserve_model(ctx: AppContext, db: Session) -> bool:
    """Atomically take one unit of the global model budget for the current
    request (settled against the real call count by :func:`metered`)."""
    if not ctx.llm_available:
        return False
    if not quotas.reserve_llm(db, ctx.settings.daily_llm_cycles_total):
        return False
    m = metering.current()
    if m is not None:
        m.reserved += 1
    else:  # pragma: no cover - every model-using route is metered
        logger.warning("model budget reserved outside a metered block")
    return True


def safety_gate(ctx: AppContext, db: Session, user_id: str):
    """The safety screen's gateway for this person, or None (no model)."""
    gw = ctx.gateway if ctx.llm_available else None
    if gw is None:
        return None
    return metering.SafetyGate(gw, allow=lambda: ctx.limiter_safety.allow("safety:" + user_id),
                               reserve=lambda: reserve_model(ctx, db))


def screen(ctx: AppContext, db: Session, user: User, text: str):
    """Screen person-written text: the deterministic floor always runs; the
    raise-only model second opinion is added while the person's safety
    bucket (``safety_model_per_hour``) and the global budget allow."""
    gate = safety_gate(ctx, db, user.id)
    if gate is not None:
        return safety_mod.screen_text_llm(text, gate)
    return safety_mod.screen_text(text)


def ctx_of(request: Request) -> AppContext:
    return request.app.state.ctx


def get_db(request: Request) -> Iterator[Session]:
    db = ctx_of(request).sessionmaker()
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = ctx_of(request).sessions.user_for(db, request.cookies.get(COOKIE_NAME))
    if user is None:
        raise ApiError(401, "unauthenticated", "Please log in.")
    return user


# ─────────────────────────────────────────────────────────────
# Bodies
# ─────────────────────────────────────────────────────────────

class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SignupBody(_Body):
    email: str = Field("", max_length=1000)
    password: str = Field("", max_length=4096)
    age_confirmed: Any = False
    invite_code: Optional[str] = Field(None, max_length=200)


class LoginBody(_Body):
    email: str = Field("", max_length=1000)
    password: str = Field("", max_length=4096)


class OnboardingBody(_Body):
    answers: dict[str, Any]


class IdentityPatch(_Body):
    name: Optional[str] = Field(None, max_length=200)
    forbidden_themes: Optional[list[str]] = None


class ActBody(_Body):
    choice_index: Optional[StrictInt] = None
    text: Optional[str] = Field(None, max_length=MAX_ACTION_CHARS)
    # The ``pending.journey_tick`` of the scene this action answers. Clients
    # must send it (a stale tab gets 409 scene_changed; a retried request is
    # replayed instead of played twice). Accepted missing for older clients,
    # which then get neither protection.
    expected_tick: Optional[StrictInt] = None


class ResumeBody(_Body):
    acknowledged: StrictBool = False


class DeleteBody(_Body):
    password: str = Field("", max_length=4096)


# ─────────────────────────────────────────────────────────────
# Serialisation
# ─────────────────────────────────────────────────────────────

def iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def public_user(u: User) -> dict:
    return {"id": u.id, "email": u.email, "created_at": iso(u.created_at)}


def journey_meta(j: Journey, forbidden: Optional[list[str]] = None) -> dict:
    title = redact_title(j.title, forbidden, fallback=f"Journey {j.id[:4]}") if forbidden \
        else j.title
    return {"id": j.id, "status": j.status, "title": title, "cycles": j.cycles,
            "created_at": iso(j.created_at), "updated_at": iso(j.updated_at),
            "chapter": j.chapter, "use_llm": bool(j.use_llm)}


def pending_json(p: PendingScene) -> dict:
    d = p.model_dump(mode="json")
    d["convergence"] = round(float(d.get("convergence") or 0.0), 4)
    return d


def record_lite(rec: CycleRecord, chapter: str) -> dict:
    """CycleRecord-lite: what the browser may see of a cycle (no alignment
    nudges, harmonizer internals, identity snapshots or requests)."""
    s = rec.scene
    ua = rec.outcome.user_action
    return {
        "tick": rec.tick,
        "stage": normalize_stage(s.stage),
        "chapter": chapter,
        "scene": {"title": s.title, "summary": s.summary,
                  "beats": [b.model_dump(mode="json") for b in s.beats],
                  "choices": list(s.choices), "symbols": list(s.symbols),
                  "is_vision": s.is_vision, "source": s.source},
        "action": {"raw": ua.raw, "choice_index": ua.choice_index} if ua else None,
        "npc_events": list(rec.outcome.npc_events),
        "lesson": rec.outcome.lesson,
        "convergence": round(float(rec.convergence), 4),
        "success": rec.outcome.success,
    }


def safety_json(s: safety_mod.SafetyResult) -> dict:
    return s.model_dump(mode="json")


def _not_forbidden(text_: str, forbidden: list[str]) -> bool:
    return not (text_ and forbidden and mentions_forbidden(text_, forbidden, synonyms=True))


def identity_view(ident: Identity) -> dict:
    seed = json.loads(ident.seed_json)
    state = json.loads(ident.state_json) if ident.state_json else None
    forbidden = list(seed.get("forbidden_themes") or [])
    fb_lower = {f.lower() for f in forbidden}

    learned = {}
    extra_goals = []
    seed_keys = {g["description"].strip().lower() for g in seed.get("goals") or []}
    for g in (state or {}).get("goals", []):
        key = str(g.get("description", "")).strip().lower()
        learned[key] = g
        if key not in seed_keys:
            extra_goals.append(g)
    goals = []
    for g in [*(seed.get("goals") or []), *extra_goals]:
        st = learned.get(g["description"].strip().lower(), {})
        goals.append({"description": g["description"],
                      "horizon": g.get("horizon", "mid"),
                      "progress": round(float(st.get("progress", 0.0)), 3),
                      "realised": bool(st.get("realised", False)),
                      "is_future_memory": bool(g.get("is_future_memory", False))})
    if state and state.get("traits"):
        traits = {t["name"]: round(float(t.get("value", 0.5)), 3) for t in state["traits"]}
    else:
        traits = {k: round(float(v), 3) for k, v in (seed.get("traits") or {}).items()}
    themes = list(dict.fromkeys([*((state or {}).get("active_themes") or []),
                                 *(seed.get("active_themes") or [])]))
    themes = [t for t in themes if t.lower() not in fb_lower]
    insights = json.loads(ident.insights_json) if ident.insights_json else []
    insights = [s for s in insights if _not_forbidden(s, forbidden)]
    return {"name": seed.get("name") or ident.name, "active_themes": themes,
            "forbidden_themes": forbidden, "goals": goals, "traits": traits,
            "insights": insights}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _identity(db: Session, user: User) -> Optional[Identity]:
    return db.execute(select(Identity).where(Identity.user_id == user.id)).scalars().first()


def _journey(db: Session, user: User, jid: str) -> Journey:
    if not isinstance(jid, str) or len(jid) > 64:
        raise ApiError(404, "not_found", "That journey doesn't exist.")
    j = db.execute(select(Journey).where(Journey.id == jid, Journey.user_id == user.id)
                   ).scalars().first()
    if j is None:
        raise ApiError(404, "not_found", "That journey doesn't exist.")
    return j


def _client_ip(request: Request) -> str:
    s = ctx_of(request).settings
    peer = request.client.host if request.client else None
    if not s.client_ip_header and s.trusted_proxy_hops <= 0:
        return peer or "unknown"
    hs = [(k.decode("latin-1").lower(), v.decode("latin-1"))
          for k, v in request.scope.get("headers", [])]
    return client_ip(hs, peer, header=s.client_ip_header, hops=s.trusted_proxy_hops)


def _forbidden_of(db: Session, user: User) -> list[str]:
    ident = _identity(db, user)
    if ident is None:
        return []
    try:
        return list(json.loads(ident.seed_json).get("forbidden_themes") or [])
    except ValueError:  # pragma: no cover
        return []


@contextmanager
def _user_lock(ctx: AppContext, db: Session, user: User, *, exclusive: bool,
               timeout: float = 30.0) -> Iterator[None]:
    """Take the person's lock (see ``userlocks``) and re-check, under it,
    that the account still exists (a deletion may have committed while we
    waited)."""
    locks = ctx.user_locks
    cm = locks.exclusive(user.id, timeout) if exclusive else locks.shared(user.id, timeout)
    try:
        cm.__enter__()
    except UserBusy:
        raise _busy() from None
    try:
        alive = db.execute(select(User.id).where(User.id == user.id,
                                                 User.deleted_at.is_(None))).first()
        if alive is None:
            raise ApiError(401, "unauthenticated", "Please log in.")
        yield
    finally:
        cm.__exit__(None, None, None)


def _rate(limiter: RateLimiter, key: str) -> None:
    if not limiter.allow(key):
        raise ApiError(429, "rate_limited", "Too many attempts. Please wait a moment and try again.",
                       headers={"Retry-After": str(limiter.retry_after())})


def _set_cookie(ctx: AppContext, resp: Response, value: str) -> None:
    resp.set_cookie(COOKIE_NAME, value, max_age=ctx.sessions.max_age, httponly=True,
                    samesite="lax", secure=ctx.settings.secure_cookies, path="/")


def _clear_cookie(ctx: AppContext, resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax",
                       secure=ctx.settings.secure_cookies)


def _model_on(ctx: AppContext, db: Session, use_llm: bool) -> bool:
    """Whether the model may be used for this step: atomically reserves one
    unit of the global budget (settled by :func:`metered`)."""
    return bool(use_llm and ctx.llm_available and reserve_model(ctx, db))


def _take_turn(ctx: AppContext, db: Session, user: User) -> None:
    """Atomically take one of the person's daily turns (or 429). The caller
    gives it back with ``quotas.release_turn`` if the turn does not happen."""
    if not quotas.reserve_turn(db, user.id, ctx.settings.daily_cycles_per_user):
        raise ApiError(429, "quota_exceeded",
                       "You've walked far today. The story will be waiting for you tomorrow "
                       "(turns reset at 00:00 UTC).")


@contextmanager
def _turn(ctx: AppContext, db: Session, user: User) -> Iterator[None]:
    """A daily turn that is given back if the block fails."""
    _take_turn(ctx, db, user)
    try:
        yield
    except BaseException:
        try:
            db.rollback()
            quotas.release_turn(db, user.id)
        except Exception:  # pragma: no cover
            logger.exception("releasing a turn failed")
        raise


def _runtime(ctx: AppContext, j: Journey) -> JourneyRuntime:
    try:
        return ctx.cache.get(j.id, j.snapshot)
    except LookupError:
        raise ApiError(500, "journey_unavailable", "This journey can't be resumed.") from None


def _ensure_pending(ctx: AppContext, db: Session, j: Journey, rt: JourneyRuntime) -> PendingScene:
    """The pending scene; dreams one (counting model use) if none is waiting."""
    if rt.has_pending():
        return rt.pending()
    on = _model_on(ctx, db, j.use_llm)
    rt.set_model_enabled(on)
    p = rt.pending()
    j.chapter = p.chapter[:120]
    j.snapshot = rt.snapshot()
    db.commit()
    return p


def _finalize(db: Session, j: Journey, rt: JourneyRuntime, status: str) -> None:
    """The journey came home / was ended: debrief, carry the self forward."""
    j.status = status
    d = rt.debrief()
    row = db.get(DebriefRow, j.id)
    if row is None:
        row = DebriefRow(journey_id=j.id, json="", markdown="")
        db.add(row)
    row.json = d.model_dump_json()
    row.markdown = d.to_markdown()
    row.created_at = utcnow()
    if d.title:
        j.title = d.title[:200]
    ident = db.execute(select(Identity).where(Identity.user_id == j.user_id)).scalars().first()
    if ident is not None:
        ident.state_json = json.dumps(rt.identity_state())
        ident.insights_json = json.dumps(rt.insights())
        ident.updated_at = utcnow()


def _record_safety(db: Session, user: User, level: str, journey_id: Optional[str] = None) -> None:
    db.add(SafetyEvent(user_id=user.id, journey_id=journey_id, level=level, created_at=utcnow()))


def _busy() -> ApiError:
    return ApiError(409, "busy", "Still dreaming the last turn — one moment.")


# ─────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────

def build_router() -> APIRouter:
    r = APIRouter(prefix="/api")

    # -- auth ------------------------------------------------------------
    @r.post("/auth/signup", status_code=201)
    def signup(body: SignupBody, request: Request, db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        email = normalise_email(body.email)
        _rate(ctx.limiter_auth_ip, "ip:" + _client_ip(request))
        _rate(ctx.limiter_auth_email, "signup:" + email[:254])
        s = ctx.settings
        if not s.allow_signups:
            raise ApiError(403, "signups_closed", "ANA isn't accepting new sign-ups right now.")
        if not valid_email(email):
            raise ApiError(422, "invalid_email", "That doesn't look like an email address.")
        if not password_ok(body.password):
            raise ApiError(422, "weak_password",
                           f"Your password needs at least {PASSWORD_MIN} characters "
                           "(not counting spaces at either end).")
        if len(body.password) > PASSWORD_MAX:
            raise ApiError(422, "password_too_long",
                           f"Please use a password of at most {PASSWORD_MAX} characters.")
        if body.age_confirmed is not True:
            raise ApiError(422, "age_required",
                           "ANA is for adults — please confirm you are 18 or older.")
        if s.invite_codes:
            code = (body.invite_code or "").strip()
            if not code:
                raise ApiError(403, "invite_required",
                               "ANA is invite-only right now. Please enter your invite code.")
            if not any(secrets.compare_digest(code, c) for c in s.invite_codes):
                raise ApiError(403, "invalid_invite", "That invite code isn't valid.")
        if find_user(db, email) is not None:
            # Same cost as a real sign-up, so timing alone does not say the
            # account exists (the 409 itself still does: see DEPLOY.md).
            burn_hash(body.password)
            raise ApiError(409, "email_taken",
                           "An account with that email already exists. Try logging in.")
        user = User(id=secrets.token_hex(8), email=email, password_hash=hash_password(body.password),
                    created_at=utcnow())
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, "email_taken",
                           "An account with that email already exists. Try logging in.") from None
        cookie = ctx.sessions.create(db, user)
        db.commit()
        resp = JSONResponse({"user": public_user(user)}, status_code=201)
        _set_cookie(ctx, resp, cookie)
        return resp

    @r.post("/auth/login")
    def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        email = normalise_email(body.email)[:254]
        ip = _client_ip(request)
        _rate(ctx.limiter_auth_ip, "ip:" + ip)
        # Only FAILED attempts are throttled per email: strictly per
        # (email, client IP), and loosely per email across all IPs — so a
        # stranger's wrong guesses cannot lock the owner out (short of the
        # global ceiling, ``auth_email_global_*``).
        k_pair, k_email = f"fail:{email}|{ip}", f"fail:{email}"
        if not ctx.limiter_auth_email.peek(k_pair):
            _rate(ctx.limiter_auth_email, k_pair)
        if not ctx.limiter_auth_fail_global.peek(k_email):
            _rate(ctx.limiter_auth_fail_global, k_email)
        user = find_user(db, email) if valid_email(email) else None
        password = body.password[:PASSWORD_MAX]
        ok = verify_password(user.password_hash if user else None, password)
        if not ok or user is None:
            ctx.limiter_auth_email.allow(k_pair)
            ctx.limiter_auth_fail_global.allow(k_email)
            raise ApiError(401, "invalid_credentials", "That email and password don't match.")
        if needs_rehash(user.password_hash):      # older / stronger-memory params
            try:
                user.password_hash = hash_password(password)
            except ApiError:                       # hasher busy: rehash next time
                pass
        cookie = ctx.sessions.create(db, user)
        SessionManager.purge_expired(db)
        db.commit()
        resp = JSONResponse({"user": public_user(user)})
        _set_cookie(ctx, resp, cookie)
        return resp

    @r.post("/auth/logout", status_code=204)
    def logout(request: Request, db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        ctx.sessions.revoke(db, request.cookies.get(COOKIE_NAME))
        db.commit()
        resp = Response(status_code=204)
        _clear_cookie(ctx, resp)
        return resp

    # -- me --------------------------------------------------------------
    @r.get("/me")
    def me(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        return {"user": public_user(user), "has_identity": _identity(db, user) is not None,
                "usage": {"today": quotas.cycles_today(db, user.id),
                          "limit": ctx.settings.daily_cycles_per_user},
                "llm_available": ctx.llm_available}

    @r.get("/me/export")
    def export(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        ident = _identity(db, user)
        journeys = list(db.execute(select(Journey).where(Journey.user_id == user.id)
                                   .order_by(Journey.created_at)).scalars())
        out_j = []
        for j in journeys:
            cycles = db.execute(select(Cycle).where(Cycle.journey_id == j.id)
                                .order_by(Cycle.tick)).scalars()
            drow = db.get(DebriefRow, j.id)
            out_j.append({**journey_meta(j), "seed_value": j.seed_value,
                          "records": [json.loads(c.record_json) for c in cycles],
                          "debrief": ({"debrief": json.loads(drow.json), "markdown": drow.markdown}
                                      if drow else None)})
        data = {
            "exported_at": iso(utcnow()),
            "user": public_user(user),
            "identity": None if ident is None else {
                "identity_id": ident.identity_id,
                "created_at": iso(ident.created_at), "updated_at": iso(ident.updated_at),
                "seed": json.loads(ident.seed_json),
                "state": json.loads(ident.state_json) if ident.state_json else None,
                "view": identity_view(ident),
            },
            "journeys": out_j,
            "usage": [{"day": u.day, "cycles": u.cycles, "llm_cycles": u.llm_cycles}
                      for u in quotas.usage_rows(db, user.id)],
            "safety_events": [{"level": e.level, "journey_id": e.journey_id,
                               "created_at": iso(e.created_at)}
                              for e in db.execute(select(SafetyEvent)
                                                  .where(SafetyEvent.user_id == user.id)
                                                  .order_by(SafetyEvent.id)).scalars()],
            "shards": shards.export_shards(ctx.settings.shard_db, ident.identity_id) if ident else [],
        }
        return Response(json.dumps(data, indent=2, ensure_ascii=False),
                        media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="ana-export.json"'})

    @r.delete("/me", status_code=204)
    def delete_me(body: DeleteBody, request: Request, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        _rate(ctx.limiter_auth_email, "delete:" + user.id)
        if not verify_password(user.password_hash, body.password[:PASSWORD_MAX]):
            raise ApiError(403, "invalid_password", "That password isn't right.")
        # The exclusive user lock: every in-flight request of this person has
        # finished and none can start (create/onboarding/act/... take it too)
        # until the rows are gone — so the journey list below is complete and
        # nothing can write shards or cache a runtime after we purge them.
        with _user_lock(ctx, db, user, exclusive=True, timeout=60):
            jids = sorted(db.execute(select(Journey.id)
                                     .where(Journey.user_id == user.id)).scalars())
            ident = _identity(db, user)
            iid = ident.identity_id if ident is not None else None
            with ExitStack() as stack:
                for jid in jids:     # sorted: a fixed lock order, no deadlock
                    try:
                        stack.enter_context(ctx.cache.locked(jid, timeout=60))
                    except JourneyBusy:
                        raise _busy() from None
                # 1. stop every cached runtime WITHOUT flushing its shards back
                for jid in jids:
                    ctx.cache.drop(jid, flush=False)
                # 2. the person's shards
                if iid is not None:
                    n = shards.delete_shards(ctx.settings.shard_db, iid)
                    logger.info("deleted account %s: %d shards removed", user.id, n)
                # 3. every row
                if jids:
                    db.execute(delete(Cycle).where(Cycle.journey_id.in_(jids)))
                    db.execute(delete(DebriefRow).where(DebriefRow.journey_id.in_(jids)))
                    db.execute(delete(Journey).where(Journey.user_id == user.id))
                db.execute(delete(SafetyEvent).where(SafetyEvent.user_id == user.id))
                db.execute(delete(Usage).where(Usage.user_id == user.id))
                db.execute(delete(Identity).where(Identity.user_id == user.id))
                SessionManager.revoke_all(db, user.id)
                db.execute(delete(User).where(User.id == user.id))
                db.commit()
            # 4. belt and braces, after the commit: no runtime of this identity
            #    stays cached and no shard written meanwhile survives.
            if iid is not None:
                from ana.identity.memory import scope_id
                ctx.cache.drop_identity(scope_id(iid), flush=False)
                late = shards.delete_shards(ctx.settings.shard_db, iid)
                if late:
                    logger.warning("deleted account %s: %d late shards purged", user.id, late)
        for jid in jids:
            ctx.cache.forget_lock(jid)
        resp = Response(status_code=204)
        _clear_cookie(ctx, resp)
        return resp

    # -- onboarding & identity ---------------------------------------------
    @r.get("/onboarding")
    def onboarding_questions(request: Request):
        ctx = ctx_of(request)
        return {"questions": onboarding_mod.QUESTIONS, "disclaimer": safety_mod.DISCLAIMER,
                "invite_required": bool(ctx.settings.invite_codes)}

    @r.post("/onboarding", status_code=201)
    def onboarding_submit(body: OnboardingBody, request: Request,
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        _rate(ctx.limiter_onboarding, "onboarding:" + user.id)
        with _user_lock(ctx, db, user, exclusive=True), metered(ctx, db), \
                _turn(ctx, db, user):
            # one daily turn per submission (it may make several model calls,
            # each counted against the global budget by the meter)
            on = _model_on(ctx, db, True)
            result = onboarding_mod.build_seed(body.answers,
                                               gateway=ctx.model_gateway() if on else None,
                                               safety_gateway=safety_gate(ctx, db, user.id),
                                               use_llm=on)
            if result.seed is None or result.safety.level == "crisis":
                _record_safety(db, user, "crisis")
                db.commit()
                return JSONResponse({"safety": safety_json(result.safety)}, status_code=200)
            seed = IdentitySeed.model_validate(result.seed.model_dump())
            ident = _identity(db, user)
            if ident is None:
                ident = Identity(user_id=user.id, identity_id="ana-" + secrets.token_hex(12),
                                 created_at=utcnow())
                db.add(ident)
            ident.name = seed.name[:80]
            ident.seed_json = seed.model_dump_json()
            ident.updated_at = utcnow()
            out: dict[str, Any] = {}
            if result.safety.level != "none":
                _record_safety(db, user, result.safety.level)
                out["safety"] = safety_json(result.safety)
            db.commit()
            out["identity"] = identity_view(ident)
            return JSONResponse(out, status_code=201)

    @r.get("/identity")
    def identity_get(user: User = Depends(current_user), db: Session = Depends(get_db)):
        ident = _identity(db, user)
        if ident is None:
            raise ApiError(404, "no_identity", "Finish onboarding first.")
        return {"identity": identity_view(ident)}

    @r.patch("/identity")
    def identity_patch(body: IdentityPatch, request: Request,
                       user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        _rate(ctx.limiter_identity, "identity:" + user.id)
        with _user_lock(ctx, db, user, exclusive=True), metered(ctx, db):
            ident = _identity(db, user)
            if ident is None:
                raise ApiError(404, "no_identity", "Finish onboarding first.")
            seed = IdentitySeed.model_validate_json(ident.seed_json)
            upd: dict[str, Any] = {}
            if body.forbidden_themes is not None:
                ft = body.forbidden_themes
                if len(ft) > MAX_FORBIDDEN_ITEMS or any(len(x) > MAX_FORBIDDEN_CHARS for x in ft):
                    raise ApiError(422, "invalid_forbidden_themes",
                                   f"Send a list of up to {MAX_FORBIDDEN_ITEMS} words or phrases "
                                   f"({MAX_FORBIDDEN_CHARS} characters each).")
                clean = [onboarding_mod.clean_text(x, MAX_FORBIDDEN_CHARS).lower() for x in ft]
                upd["forbidden_themes"] = list(dict.fromkeys(x for x in clean if x))
            if body.name is not None:
                name = onboarding_mod.clean_text(body.name, MAX_NAME)
                if name and name != seed.name:      # unchanged name: nothing to screen
                    s = screen(ctx, db, user, name)
                    if s.level == "crisis":
                        _record_safety(db, user, "crisis")
                        db.commit()
                        return {"safety": safety_json(s)}
                upd["name"] = name or "the wanderer"
            if upd:
                seed = seed.model_copy(update=upd)
                ident.seed_json = seed.model_dump_json()
                ident.name = seed.name[:80]
                ident.updated_at = utcnow()
                db.commit()
            return {"identity": identity_view(ident)}

    # -- journeys ----------------------------------------------------------
    @r.get("/journeys")
    def journeys_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
        fb = _forbidden_of(db, user)
        js = db.execute(select(Journey).where(Journey.user_id == user.id)
                        .order_by(Journey.updated_at.desc())).scalars()
        return {"journeys": [journey_meta(j, fb) for j in js]}

    @r.post("/journeys", status_code=201)
    def journeys_create(request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        s = ctx.settings
        _rate(ctx.limiter_create, "create:" + user.id)
        # exclusive: the active-journey check and the insert below cannot
        # interleave with another create (or a deletion) of this person
        with _user_lock(ctx, db, user, exclusive=True), metered(ctx, db):
            ident = _identity(db, user)
            if ident is None:
                raise ApiError(409, "no_identity", "Finish onboarding first.")
            active = db.execute(select(func.count()).select_from(Journey).where(
                Journey.user_id == user.id, Journey.status.in_(OPEN_STATUSES))).scalar_one()
            if active >= s.max_active_journeys:
                raise ApiError(409, "too_many_active",
                               f"You can have up to {s.max_active_journeys} journeys open at "
                               "once. Finish or end one to begin another.")
            with _turn(ctx, db, user):        # the first scene is a turn
                n = db.execute(select(func.count()).select_from(Journey)
                               .where(Journey.user_id == user.id)).scalar_one() + 1
                seed = IdentitySeed.model_validate_json(ident.seed_json)
                state = json.loads(ident.state_json) if ident.state_json else None
                use_llm = ctx.llm_available
                on = _model_on(ctx, db, use_llm)
                seed_value = secrets.randbelow(2 ** 31 - 1)
                rt = JourneyRuntime.start(seed, identity_id=ident.identity_id,
                                          shard_db=s.shard_db, use_llm=use_llm,
                                          gateway=ctx.model_gateway(), seed_value=seed_value,
                                          max_cycles=s.max_cycles, identity_state=state)
                try:
                    rt.set_model_enabled(on)
                    p = rt.pending()
                    now = utcnow()
                    j = Journey(id=secrets.token_hex(8), user_id=user.id, status="active",
                                use_llm=use_llm, seed_value=seed_value, created_at=now,
                                updated_at=now, cycles=0, title=f"Journey {n}",
                                chapter=p.chapter[:120], snapshot=rt.snapshot())
                    db.add(j)
                    db.commit()
                except Exception:
                    rt.close(flush=False)
                    raise
            ctx.cache.put(j.id, rt)
            return JSONResponse({"journey": journey_meta(j), "pending": pending_json(p)},
                                status_code=201)

    @r.get("/journeys/{jid}")
    def journey_get(jid: str, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        fb = _forbidden_of(db, user)
        records = [json.loads(c.record_json) for c in db.execute(
            select(Cycle).where(Cycle.journey_id == j.id).order_by(Cycle.tick)).scalars()]
        out: dict[str, Any] = {"journey": journey_meta(j, fb), "records": records}
        if j.status in OPEN_STATUSES:
            with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
                try:
                    with ctx.cache.locked(j.id, timeout=10):
                        db.refresh(j)
                        if j.status in OPEN_STATUSES:
                            rt = _runtime(ctx, j)
                            if not rt.closed:
                                out["pending"] = pending_json(_ensure_pending(ctx, db, j, rt))
                        out["journey"] = journey_meta(j, fb)
                except JourneyBusy:
                    raise _busy() from None
        return out

    @r.get("/journeys/{jid}/pending")
    def journey_pending(jid: str, request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        if j.status in DONE_STATUSES:
            return {"closed": True}
        with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
            try:
                with ctx.cache.locked(j.id, timeout=10):
                    db.refresh(j)
                    if j.status in DONE_STATUSES:
                        return {"closed": True}
                    rt = _runtime(ctx, j)
                    if rt.closed:
                        return {"closed": True}
                    out = {"pending": pending_json(_ensure_pending(ctx, db, j, rt))}
                    if j.status == "paused_safety":
                        out["paused"] = True
                    return out
            except JourneyBusy:
                raise _busy() from None

    @r.post("/journeys/{jid}/act")
    def journey_act(jid: str, body: ActBody, request: Request,
                    user: User = Depends(current_user), db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        text_ = (body.text or "").strip()
        if body.choice_index is None and not text_:
            raise ApiError(422, "empty_action", "Choose something, or write what you do.")
        _rate(ctx.limiter_act, "act:" + user.id)
        with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
            try:
                with ctx.cache.locked(j.id):
                    db.refresh(j)
                    return _act_locked(ctx, db, user, j, body.choice_index, text_,
                                       body.expected_tick)
            except JourneyBusy:
                raise _busy() from None

    @r.post("/journeys/{jid}/resume")
    def journey_resume(jid: str, body: ResumeBody, request: Request,
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        if body.acknowledged is not True:
            raise ApiError(422, "acknowledgement_required",
                           "Please confirm you're ready to continue.")
        with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
            try:
                with ctx.cache.locked(j.id):
                    db.refresh(j)
                    if j.status in DONE_STATUSES:
                        raise ApiError(409, "journey_closed", "This journey has come home.")
                    if j.status == "paused_safety":
                        j.status = "active"
                        j.updated_at = utcnow()
                        db.commit()
                    rt = _runtime(ctx, j)
                    out: dict[str, Any] = {"journey": journey_meta(j, _forbidden_of(db, user))}
                    if not rt.closed:
                        out["pending"] = pending_json(_ensure_pending(ctx, db, j, rt))
                    return out
            except JourneyBusy:
                raise _busy() from None

    @r.post("/journeys/{jid}/end")
    def journey_end(jid: str, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
            try:
                with ctx.cache.locked(j.id):
                    db.refresh(j)
                    if j.status in OPEN_STATUSES or db.get(DebriefRow, j.id) is None:
                        rt = _runtime(ctx, j)
                        rt.set_model_enabled(_model_on(ctx, db, j.use_llm))
                        if j.status in OPEN_STATUSES:
                            rt.end()
                            status = "ended"
                        else:
                            status = j.status
                        _finalize(db, j, rt, status)
                        j.updated_at = utcnow()
                        j.snapshot = rt.snapshot()
                        db.commit()
                        ctx.cache.drop(j.id)
                    return {"journey": journey_meta(j, _forbidden_of(db, user)),
                            "debrief_ready": True}
            except JourneyBusy:
                raise _busy() from None

    @r.get("/journeys/{jid}/debrief")
    def journey_debrief(jid: str, request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        j = _journey(db, user, jid)
        if j.status not in DONE_STATUSES:
            raise ApiError(409, "not_ready",
                           "The debrief is written when the journey comes home or ends.")
        row = db.get(DebriefRow, j.id)
        if row is None:     # e.g. a crash between closing and writing it
            with _user_lock(ctx, db, user, exclusive=False), metered(ctx, db):
                try:
                    with ctx.cache.locked(j.id, timeout=10):
                        rt = _runtime(ctx, j)
                        rt.set_model_enabled(_model_on(ctx, db, j.use_llm))
                        _finalize(db, j, rt, j.status)
                        db.commit()
                        ctx.cache.drop(j.id)
                except JourneyBusy:
                    raise _busy() from None
            row = db.get(DebriefRow, j.id)
        fb = _forbidden_of(db, user)
        # never-show words are redacted at read time (current list)
        return {"debrief": redact_value(json.loads(row.json), fb),
                "markdown": redact_value(row.markdown, fb)}

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                 include_in_schema=False)
    def api_404(rest: str):
        raise ApiError(404, "not_found", "No such endpoint.")

    return r


def _same_action(stored: Optional[dict], choice_index: Optional[int], text_: str) -> bool:
    if not stored:
        return False
    if choice_index is not None:
        return stored.get("choice_index") == choice_index and (
            not text_ or (stored.get("raw") or "") == text_[:MAX_ACTION_CHARS])
    return stored.get("choice_index") is None and \
        (stored.get("raw") or "") == text_[:MAX_ACTION_CHARS]


def _act_kwargs(concern: bool) -> dict:
    """``gentle=True`` for a "concern" turn, when the engine supports it
    (``JourneyRuntime.act(action, *, gentle=False)``)."""
    if not concern:
        return {}
    try:
        params = inspect.signature(JourneyRuntime.act).parameters
    except (TypeError, ValueError):  # pragma: no cover
        return {}
    return {"gentle": True} if "gentle" in params else {}


def _scene_changed(j: Journey, pending: Optional[PendingScene], forbidden: list[str]):
    body: dict[str, Any] = {
        "error": {"code": "scene_changed",
                  "message": "The story moved on since this page was loaded — here is "
                             "where it stands now."},
        "journey": journey_meta(j, forbidden),
        "closed": j.status in DONE_STATUSES,
    }
    if pending is not None:
        body["pending"] = pending_json(pending)
    return JSONResponse(body, status_code=409)


def _act_locked(ctx: AppContext, db: Session, user: User, j: Journey,
                choice_index: Optional[int], text_: str,
                expected_tick: Optional[int] = None):
    fb = _forbidden_of(db, user)

    # -- idempotent replay: the client is (re)sending the turn it already
    #    played (lost response + retry). No new turn, no quota charge.
    if expected_tick is not None:
        last = db.execute(select(Cycle).where(Cycle.journey_id == j.id)
                          .order_by(Cycle.tick.desc()).limit(1)).scalars().first()
        if last is not None and last.tick == expected_tick:
            lite = json.loads(last.record_json)
            if _same_action(lite.get("action"), choice_index, text_):
                out: dict[str, Any] = {"record": lite, "replayed": True,
                                       "closed": j.status in DONE_STATUSES}
                out["debrief_ready"] = out["closed"]
                if j.status in OPEN_STATUSES:
                    rt = _runtime(ctx, j)
                    if not rt.closed:
                        out["pending"] = pending_json(_ensure_pending(ctx, db, j, rt))
                    if j.status == "paused_safety":
                        out["paused"] = True
                out["journey"] = journey_meta(j, fb)
                resp = JSONResponse(out)
                resp.headers["X-ANA-Replayed"] = "1"
                return resp

    if j.status in DONE_STATUSES:
        raise ApiError(409, "journey_closed", "This journey has come home.")
    if j.status == "paused_safety":
        raise ApiError(409, "journey_paused",
                       "This journey is paused. Resume it when you're ready.")

    rt = _runtime(ctx, j)
    if rt.closed:
        _finalize(db, j, rt, "closed")
        j.snapshot = rt.snapshot()
        db.commit()
        ctx.cache.drop(j.id)
        raise ApiError(409, "journey_closed", "This journey has come home.")

    # -- the scene this action answers must be the one waiting
    if expected_tick is not None:
        pending_now = _ensure_pending(ctx, db, j, rt)
        if pending_now.journey_tick != expected_tick:
            # a stale tab: nothing is played — except that a crisis message is
            # never dropped on the floor (it pauses the journey below)
            if not (text_ and safety_mod.screen_text(text_).level == "crisis"):
                return _scene_changed(j, pending_now, fb)

    _take_turn(ctx, db, user)
    try:
        return _play_turn(ctx, db, user, j, rt, choice_index, text_, fb)
    except BaseException:
        try:
            db.rollback()
            quotas.release_turn(db, user.id)      # the turn did not happen
        except Exception:  # pragma: no cover
            logger.exception("releasing a turn failed")
        raise


def _play_turn(ctx: AppContext, db: Session, user: User, j: Journey, rt: JourneyRuntime,
               choice_index: Optional[int], text_: str, fb: list[str]):
    concern = None
    if text_:
        s = screen(ctx, db, user, text_)
        if s.level == "crisis":
            j.status = "paused_safety"
            j.updated_at = utcnow()
            _record_safety(db, user, "crisis", j.id)
            db.commit()
            quotas.release_turn(db, user.id)      # no turn was played
            return {"record": None, "closed": False, "debrief_ready": False,
                    "journey": journey_meta(j, fb), "safety": safety_json(s)}
        if s.level == "concern":
            concern = s

    try:
        on = _model_on(ctx, db, j.use_llm)
        rt.set_model_enabled(on)
        if rt.closed:
            raise JourneyClosedError("closed")
        chapter = rt.pending().chapter
        action = UserAction(raw=text_, choice_index=choice_index)
        kw = _act_kwargs(concern is not None)
        try:
            rec = rt.act(action, **kw)
        except NoPendingSceneError:   # pragma: no cover - pending() above guarantees one
            rt.pending()
            rec = rt.act(action, **kw)
    except ValueError as e:
        raise ApiError(422, "bad_choice", "That choice isn't available.") from e
    except JourneyClosedError:
        db.rollback()
        _finalize(db, j, rt, "closed")
        j.snapshot = rt.snapshot()
        db.commit()
        ctx.cache.drop(j.id)
        raise ApiError(409, "journey_closed", "This journey has come home.") from None
    except Exception:
        # The in-memory runtime may be half-way through a cycle: forget it, the
        # last snapshot in the DB is the truth.
        ctx.cache.drop(j.id, flush=False)
        raise

    lite = record_lite(rec, chapter)
    db.add(Cycle(journey_id=j.id, tick=rec.tick, record_json=json.dumps(lite), created_at=utcnow()))
    if on:
        quotas.mark_llm_turn(db, user.id)
    if concern is not None:
        _record_safety(db, user, "concern", j.id)
    j.cycles = len(rt.history)
    j.updated_at = utcnow()
    out: dict[str, Any] = {"record": lite}
    try:
        if rt.closed:
            _finalize(db, j, rt, "closed")
            out.update(closed=True, debrief_ready=True)
        else:
            p = rt.pending()          # dreamed with the same model decision as this turn
            j.chapter = p.chapter[:120]
            out.update(pending=pending_json(p), closed=False, debrief_ready=False)
        j.snapshot = rt.snapshot()
        db.commit()
    except Exception:
        ctx.cache.drop(j.id, flush=False)
        raise
    if out["closed"]:
        ctx.cache.drop(j.id)
    out["journey"] = journey_meta(j, fb)
    if concern is not None:
        out["safety"] = safety_json(concern)
    return out


def check_db(sm: sessionmaker) -> bool:
    try:
        with sm() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
