"""HTTP API (docs/PRODUCT_SPEC.md §3). All routes are JSON; errors are
``{"error": {"code", "message"}}``.

Every query that touches a person's data is filtered by the authenticated
user's id; a journey that exists but belongs to someone else is a 404 (never a
403), so existence does not leak.

Handlers are plain ``def`` (FastAPI runs them in its threadpool) because the
engine and the database calls are blocking.
"""
from __future__ import annotations

import json
import logging
import secrets
from contextlib import ExitStack
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

from . import quotas, shards
from .auth import (COOKIE_NAME, PASSWORD_MAX, PASSWORD_MIN, SessionManager, find_user,
                   hash_password, normalise_email, valid_email, verify_password)
from .config import Settings
from .models import (AuthSession, Cycle, DebriefRow, Identity, Journey, SafetyEvent, Usage, User,
                     utcnow)
from .runtimes import JourneyBusy, RuntimeCache
from .security import ApiError, RateLimiter

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
    limiter_auth_email: RateLimiter
    limiter_act: RateLimiter
    extra: dict = field(default_factory=dict)

    def model_gateway(self):
        return self.gateway if self.llm_available else None


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


def journey_meta(j: Journey) -> dict:
    return {"id": j.id, "status": j.status, "title": j.title, "cycles": j.cycles,
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
    return request.client.host if request.client else "unknown"


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
    return bool(use_llm and ctx.llm_available
                and quotas.llm_budget_left(db, ctx.settings.daily_llm_cycles_total))


def _quota_check(ctx: AppContext, db: Session, user: User) -> None:
    if quotas.cycles_today(db, user.id) >= ctx.settings.daily_cycles_per_user:
        raise ApiError(429, "quota_exceeded",
                       "You've walked far today. The story will be waiting for you tomorrow "
                       "(turns reset at 00:00 UTC).")


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
    if on:
        quotas.record_llm(db)
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
        _rate(ctx.limiter_auth_email, "email:" + email[:254])
        s = ctx.settings
        if not s.allow_signups:
            raise ApiError(403, "signups_closed", "ANA isn't accepting new sign-ups right now.")
        if not valid_email(email):
            raise ApiError(422, "invalid_email", "That doesn't look like an email address.")
        if len(body.password) < PASSWORD_MIN:
            raise ApiError(422, "weak_password",
                           f"Your password needs at least {PASSWORD_MIN} characters.")
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
        email = normalise_email(body.email)
        _rate(ctx.limiter_auth_ip, "ip:" + _client_ip(request))
        _rate(ctx.limiter_auth_email, "email:" + email[:254])
        user = find_user(db, email) if valid_email(email) else None
        ok = verify_password(user.password_hash if user else None, body.password[:PASSWORD_MAX])
        if not ok or user is None:
            raise ApiError(401, "invalid_credentials", "That email and password don't match.")
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
        jids = sorted(db.execute(select(Journey.id).where(Journey.user_id == user.id)).scalars())
        ident = _identity(db, user)
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
            if ident is not None:
                n = shards.delete_shards(ctx.settings.shard_db, ident.identity_id)
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
        _rate(ctx.limiter_act, "act:" + user.id)
        on = _model_on(ctx, db, True)
        result = onboarding_mod.build_seed(body.answers, gateway=ctx.model_gateway() if on else None,
                                           use_llm=on)
        if result.seed is None or result.safety.level == "crisis":
            _record_safety(db, user, "crisis")
            db.commit()
            return JSONResponse({"safety": safety_json(result.safety)}, status_code=200)
        if on:      # a model call was attempted: it counts against the global budget
            quotas.record_llm(db)
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
    def identity_patch(body: IdentityPatch, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
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
            if name:
                s = safety_mod.screen_text(name)
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
        js = db.execute(select(Journey).where(Journey.user_id == user.id)
                        .order_by(Journey.updated_at.desc())).scalars()
        return {"journeys": [journey_meta(j) for j in js]}

    @r.post("/journeys", status_code=201)
    def journeys_create(request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
        ctx = ctx_of(request)
        s = ctx.settings
        ident = _identity(db, user)
        if ident is None:
            raise ApiError(409, "no_identity", "Finish onboarding first.")
        active = db.execute(select(func.count()).select_from(Journey).where(
            Journey.user_id == user.id, Journey.status.in_(OPEN_STATUSES))).scalar_one()
        if active >= s.max_active_journeys:
            raise ApiError(409, "too_many_active",
                           f"You can have up to {s.max_active_journeys} journeys open at once. "
                           "Finish or end one to begin another.")
        _quota_check(ctx, db, user)
        n = db.execute(select(func.count()).select_from(Journey)
                       .where(Journey.user_id == user.id)).scalar_one() + 1
        seed = IdentitySeed.model_validate_json(ident.seed_json)
        state = json.loads(ident.state_json) if ident.state_json else None
        use_llm = ctx.llm_available
        on = _model_on(ctx, db, use_llm)
        seed_value = secrets.randbelow(2 ** 31 - 1)
        rt = JourneyRuntime.start(seed, identity_id=ident.identity_id, shard_db=s.shard_db,
                                  use_llm=use_llm, gateway=ctx.model_gateway(),
                                  seed_value=seed_value, max_cycles=s.max_cycles,
                                  identity_state=state)
        try:
            rt.set_model_enabled(on)
            p = rt.pending()
            if on:
                quotas.record_llm(db)
            now = utcnow()
            j = Journey(id=secrets.token_hex(8), user_id=user.id, status="active",
                        use_llm=use_llm, seed_value=seed_value, created_at=now, updated_at=now,
                        cycles=0, title=f"Journey {n}", chapter=p.chapter[:120],
                        snapshot=rt.snapshot())
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
        records = [json.loads(c.record_json) for c in db.execute(
            select(Cycle).where(Cycle.journey_id == j.id).order_by(Cycle.tick)).scalars()]
        out: dict[str, Any] = {"journey": journey_meta(j), "records": records}
        if j.status in OPEN_STATUSES:
            try:
                with ctx.cache.locked(j.id, timeout=10):
                    db.refresh(j)
                    if j.status in OPEN_STATUSES:
                        rt = _runtime(ctx, j)
                        if not rt.closed:
                            out["pending"] = pending_json(_ensure_pending(ctx, db, j, rt))
                    out["journey"] = journey_meta(j)
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
        try:
            with ctx.cache.locked(j.id):
                db.refresh(j)
                return _act_locked(ctx, db, user, j, body.choice_index, text_)
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
                out: dict[str, Any] = {"journey": journey_meta(j)}
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
        try:
            with ctx.cache.locked(j.id):
                db.refresh(j)
                if j.status in OPEN_STATUSES or db.get(DebriefRow, j.id) is None:
                    rt = _runtime(ctx, j)
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
                return {"journey": journey_meta(j), "debrief_ready": True}
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
            try:
                with ctx.cache.locked(j.id, timeout=10):
                    rt = _runtime(ctx, j)
                    _finalize(db, j, rt, j.status)
                    db.commit()
                    ctx.cache.drop(j.id)
            except JourneyBusy:
                raise _busy() from None
            row = db.get(DebriefRow, j.id)
        return {"debrief": json.loads(row.json), "markdown": row.markdown}

    @r.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                 include_in_schema=False)
    def api_404(rest: str):
        raise ApiError(404, "not_found", "No such endpoint.")

    return r


def _act_locked(ctx: AppContext, db: Session, user: User, j: Journey,
                choice_index: Optional[int], text_: str) -> dict:
    if j.status in DONE_STATUSES:
        raise ApiError(409, "journey_closed", "This journey has come home.")
    if j.status == "paused_safety":
        raise ApiError(409, "journey_paused",
                       "This journey is paused. Resume it when you're ready.")
    _quota_check(ctx, db, user)

    concern = None
    if text_:
        s = safety_mod.screen_text(text_)
        if s.level == "crisis":
            j.status = "paused_safety"
            j.updated_at = utcnow()
            _record_safety(db, user, "crisis", j.id)
            db.commit()
            return {"record": None, "closed": False, "debrief_ready": False,
                    "journey": journey_meta(j), "safety": safety_json(s)}
        if s.level == "concern":
            concern = s

    rt = _runtime(ctx, j)
    try:
        on = _model_on(ctx, db, j.use_llm)
        rt.set_model_enabled(on)
        if rt.closed:
            raise JourneyClosedError("closed")
        chapter = rt.pending().chapter
        action = UserAction(raw=text_, choice_index=choice_index)
        try:
            rec = rt.act(action)
        except NoPendingSceneError:   # pragma: no cover - pending() above guarantees one
            rt.pending()
            rec = rt.act(action)
    except ValueError as e:
        raise ApiError(422, "bad_choice", "That choice isn't available.") from e
    except JourneyClosedError:
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
    quotas.record_turn(db, user.id, llm=on)
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
    out["journey"] = journey_meta(j)
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
