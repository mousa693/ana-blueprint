"""Dev mock of ANA's public API (docs/PRODUCT_SPEC.md §3) for frontend work.

Serves ``ana/app/static/`` and implements every endpoint the SPA calls with
in-memory fake users/sessions — but the *scenes are real*: each journey is the
actual ANA engine (deterministic mode), split into "show a scene" / "resolve an
action" so a person drives it turn by turn.

    python3 tools/mock_api.py --port 8765            # then open http://127.0.0.1:8765/
    MOCK_INVITE_CODES=moon python3 tools/mock_api.py # signup requires an invite
    MOCK_DAILY_LIMIT=5 python3 tools/mock_api.py     # exercise the 429 path

Engine adapters (``MOCK_ENGINE``):
  * ``auto`` (default) — ``ana.runtime.JourneyRuntime`` if it exists and starts,
    otherwise the threaded step driver below;
  * ``runtime`` — require ``JourneyRuntime``;
  * ``threaded`` — ``ana.session.build_session`` with a driver that parks the
    engine thread inside ``Driver.act`` until the person answers. ``step()`` is
    unchanged, so records are exactly what the CLI would produce.

Nothing here is secure (plain sha256 passwords, no rate limits); it is a dev
tool and must never be deployed. Safety screening is a tiny keyword stand-in
for ``ana.safety.screen_text``.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from ana.config import ANAConfig  # noqa: E402
from ana.contracts import UserAction  # noqa: E402
from ana.engine.arc import CHAPTER_NAMES, normalize_stage  # noqa: E402
from ana.identity.core import THEME_LEXICON, IdentityCore, canonical_themes  # noqa: E402
from ana.identity.memory import ShardMemory  # noqa: E402
from ana.session import build_session  # noqa: E402

STATIC = ROOT / "ana" / "app" / "static"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
COOKIE = "ana_session"
DAILY_LIMIT = int(os.environ.get("MOCK_DAILY_LIMIT", "60"))
MAX_ACTIVE = int(os.environ.get("MOCK_MAX_ACTIVE", "3"))
MAX_CYCLES = int(os.environ.get("MOCK_MAX_CYCLES", "30"))
INVITES = [c.strip() for c in os.environ.get("MOCK_INVITE_CODES", "").split(",") if c.strip()]
ENGINE_MODE = os.environ.get("MOCK_ENGINE", "auto")
LATENCY = float(os.environ.get("MOCK_LATENCY", "0.35"))  # seconds, so loading states show

DISCLAIMER = (
    "ANA is a reflective story, not therapy or medical advice, and it is for adults (18+). "
    "If you are in crisis or thinking about harming yourself or someone else, please contact "
    "local emergency services or a crisis line — findahelpline.com lists free, confidential "
    "services in many countries."
)

RESOURCES = [
    {"name": "Find a Helpline", "url": "https://findahelpline.com",
     "note": "Free, confidential crisis lines in many countries, searchable by topic."},
    {"name": "Emergency services", "url": "",
     "note": "If you or someone else is in immediate danger, call your local emergency number (112, 911, 999…)."},
]

QUESTIONS: list[dict[str, Any]] = [
    {"id": "name", "kind": "text", "optional": True,
     "prompt": "What should the story call you?",
     "help": "A name, a nickname, or a title. It is only ever used inside your story."},
    {"id": "carrying", "kind": "multi", "optional": False, "allow_text": True,
     "prompt": "What are you carrying right now?",
     "help": "Choose any that feel true. Add your own words if none fit.",
     "options": list(THEME_LEXICON)},
    {"id": "become", "kind": "text", "optional": False,
     "prompt": "Something you want to become.",
     "help": "A sentence is enough — “someone who can lead”, “at ease with my own company”."},
    {"id": "place", "kind": "text", "optional": True,
     "prompt": "A place or image from your past that still matters.",
     "help": "A kitchen at night, a lighthouse, a particular staircase. The story may return to it."},
    {"id": "feeling", "kind": "scale", "optional": False,
     "prompt": "How have you been feeling lately?",
     "help": "No wrong answer. This only sets the weather of the first chapter.",
     "options": ["Heavy", "Low", "Steady", "Light", "Bright"]},
    {"id": "strain", "kind": "scale", "optional": True,
     "prompt": "And how much strain are you under?",
     "help": "ANA keeps the story gentler when life is already loud.",
     "options": ["Very little", "Some", "A fair amount", "A lot", "Too much"]},
    {"id": "begin", "kind": "choice", "optional": True,
     "prompt": "How would you like the story to begin?",
     "help": "You can always slow down later.",
     "options": ["Gently", "With a spark of adventure", "Straight into the deep end"]},
    {"id": "never", "kind": "multi", "optional": True, "allow_text": True,
     "prompt": "Is there anything ANA must never show you?",
     "help": "These are kept out of every scene. You can edit this list any time in Settings.",
     "options": ["grief", "death", "illness", "violence", "abandonment", "drowning",
                 "fire", "heights", "spiders"]},
]

CRISIS = re.compile(
    r"\b(kill(ing)? myself|end(ing)? my life|suicid\w*|want to die|self[- ]harm|hurt myself|"
    r"kill (him|her|them|someone))\b", re.I)
CONCERN = re.compile(r"\b(hopeless|worthless|can'?t go on|no point)\b", re.I)


def screen_text(text: str) -> dict:
    if CRISIS.search(text or ""):
        return {"level": "crisis", "reasons": ["self_harm_or_harm"],
                "message": ("It sounds like you might be going through something really painful. "
                            "You deserve support from a real person right now. Your journey is "
                            "paused — it will be here whenever you want to come back."),
                "resources": RESOURCES}
    if CONCERN.search(text or ""):
        return {"level": "concern", "reasons": ["distress"],
                "message": "That sounds heavy. If it would help to talk to someone, these are here.",
                "resources": RESOURCES}
    return {"level": "none", "reasons": [], "message": None, "resources": []}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


# ─────────────────────────────────────────────────────────────
# Engine adapters
# ─────────────────────────────────────────────────────────────


class _ParkingDriver:
    """A Driver whose ``act`` hands the scene to the web thread and waits."""

    def __init__(self, owner: "ThreadedJourney"):
        self.owner = owner

    def act(self, scene, identity):
        return self.owner._park(scene, identity)


class ThreadedJourney:
    """pending()/act() over the unchanged ``FeedbackLoopEngine.step()``."""

    kind = "threaded"

    def __init__(self, seed: dict, seed_value: int, max_cycles: int):
        self.max_cycles = max_cycles
        cfg = ANAConfig(use_llm=False, seed=seed_value, max_cycles=max_cycles,
                        forbidden_themes=list(seed.get("forbidden_themes") or []))
        self.sess = build_session(cfg, identity=identity_from_seed(seed),
                                  driver=_ParkingDriver(self))
        self._pending: Optional[dict] = None
        self._scene_ready = threading.Event()
        self._action_ready = threading.Event()
        self._done = threading.Event()
        self._action: Optional[UserAction] = None
        self._record = None
        self._error: Optional[BaseException] = None
        self._thread: Optional[threading.Thread] = None
        self._ended = False

    # engine thread -------------------------------------------------
    def _park(self, scene, identity):
        eng = self.sess.engine
        stage = normalize_stage(scene.stage)
        chapters = eng.arc.chapters()
        if stage and (not chapters or chapters[-1]["stage"] != stage):
            chapter = f"Chapter {len(chapters) + 1}: {CHAPTER_NAMES[stage]}"
        else:
            chapter = eng.arc.current_chapter()
            if chapter == "Prologue":
                chapter = "Chapter 1: The Call"
        self._pending = {
            "journey_tick": identity.tick,
            "scene": scene.model_dump(mode="json"),
            "chapter": chapter,
            "stage": stage,
            "convergence": round(float(eng.identity.convergence), 4),
            "location": eng.environment.world.location,
        }
        self._scene_ready.set()
        self._action_ready.wait()
        self._action_ready.clear()
        return self._action

    def _run_step(self):
        try:
            self._record = self.sess.step()
        except BaseException as e:  # pragma: no cover - surfaced to the caller
            self._error = e
            self._scene_ready.set()
        finally:
            self._done.set()

    # web thread ----------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._ended or self.sess.engine.closed or len(self.sess.engine.history) >= self.max_cycles

    def pending(self) -> Optional[dict]:
        if self.closed:
            return None
        if self._pending is None:
            self._scene_ready.clear()
            self._done.clear()
            self._thread = threading.Thread(target=self._run_step, daemon=True)
            self._thread.start()
            if not self._scene_ready.wait(60):
                raise RuntimeError("engine did not produce a scene in time")
            if self._error:
                raise self._error
        return self._pending

    def act(self, action: UserAction):
        if self._pending is None:
            self.pending()
        self._action = action
        self._action_ready.set()
        self._done.wait(60)
        if self._error:
            raise self._error
        self._pending = None
        return self._record

    @property
    def history(self):
        return self.sess.engine.history

    def chapter(self) -> str:
        return self.sess.engine.arc.current_chapter()

    def end(self):
        self._ended = True

    def debrief(self):
        return self.sess.debrief(use_llm=False)

    def identity_view(self) -> dict:
        ic = self.sess.identity
        return {"goals": [g.model_dump(mode="json") for g in ic.goals],
                "insights": ic.insights(), "active_themes": list(ic.active_themes),
                "traits": {k: round(t.value, 3) for k, t in ic.traits.items()}}


class RuntimeJourney:
    """Adapter over ``ana.runtime.JourneyRuntime`` (if the engine has it)."""

    kind = "runtime"

    def __init__(self, seed: dict, seed_value: int, max_cycles: int):
        from ana.runtime import IdentitySeed, JourneyRuntime  # type: ignore
        self.rt = JourneyRuntime.start(IdentitySeed(**seed), identity_id=seed["_identity_id"],
                                       shard_db=None, use_llm=False, seed_value=seed_value,
                                       max_cycles=max_cycles)

    @property
    def closed(self):
        return self.rt.closed

    def pending(self):
        if self.rt.closed:
            return None
        return self.rt.pending().model_dump(mode="json")

    def act(self, action):
        return self.rt.act(action)

    @property
    def history(self):
        return self.rt.history

    def chapter(self):
        try:
            return self.rt.pending().chapter if not self.rt.closed else _chapter_of(self.rt.history)
        except Exception:
            return _chapter_of(self.rt.history)

    def end(self):
        self.rt.end()

    def debrief(self):
        return self.rt.debrief()

    def identity_view(self) -> dict:
        st = self.rt.identity_state()
        return {"goals": st.get("goals", []), "insights": list(self.rt.insights()),
                "active_themes": st.get("active_themes", []),
                "traits": {t["name"]: round(t["value"], 3) for t in st.get("traits", [])}}


def _chapter_of(history) -> str:
    from ana.engine.arc import JourneyArc
    return JourneyArc.from_records(history).current_chapter() if history else "Prologue"


def make_journey(seed: dict, seed_value: int):
    if ENGINE_MODE in ("auto", "runtime"):
        try:
            return RuntimeJourney(seed, seed_value, MAX_CYCLES)
        except Exception as e:
            if ENGINE_MODE == "runtime":
                raise
            print(f"[mock] JourneyRuntime unavailable ({type(e).__name__}: {e}); using threaded driver",
                  file=sys.stderr)
    return ThreadedJourney(seed, seed_value, MAX_CYCLES)


def identity_from_seed(seed: dict) -> IdentityCore:
    ic = IdentityCore(name=seed.get("name") or "the wanderer", memory=ShardMemory(db_path=None))
    for k, v in (seed.get("traits") or {}).items():
        ic.set_trait(k, float(v))
    for b in seed.get("beliefs") or []:
        ic.add_belief(b)
    for g in seed.get("goals") or []:
        ic.add_goal(g["description"], horizon=g.get("horizon", "mid"),
                    is_future_memory=bool(g.get("is_future_memory")))
    for m in seed.get("memories") or []:
        ic.seed_memory(m["summary"], themes=m.get("themes") or [], symbols=m.get("symbols") or [])
    for t in seed.get("active_themes") or []:
        if t not in ic.active_themes:
            ic.active_themes.append(t)
    ic.emotion.valence = float(seed.get("valence", 0.0))
    ic.emotion.stress = float(seed.get("stress", 0.2))
    return ic


def _texts(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x)[:300] for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v[:300]]
    return []


def build_seed(answers: dict) -> dict:
    """A deterministic stand-in for ``ana.onboarding.build_seed``."""
    name = (str(answers.get("name") or "").strip()[:60]) or "the wanderer"
    themes: list[str] = []
    for t in _texts(answers.get("carrying")):
        for c in ([t] if t in THEME_LEXICON else canonical_themes(t)):
            if c not in themes:
                themes.append(c)
    become = str(answers.get("become") or "").strip()[:200]
    for c in canonical_themes(become):
        if c not in themes:
            themes.append(c)
    themes = themes[:4] or ["purpose"]
    goals = [{"description": become, "horizon": "life", "is_future_memory": True}] if become else []
    place = str(answers.get("place") or "").strip()[:200]
    memories = [{"summary": place, "themes": canonical_themes(place)[:2], "symbols": []}] if place else []
    feeling = answers.get("feeling")
    valence = (int(feeling) - 2) / 2.5 if isinstance(feeling, (int, float)) else 0.0
    strain = answers.get("strain")
    stress = 0.1 + 0.15 * int(strain) if isinstance(strain, (int, float)) else 0.2
    forbidden: list[str] = []
    for t in _texts(answers.get("never")):
        for part in re.split(r"[,;\n]", t):
            part = part.strip().lower()
            if part and part not in forbidden:
                forbidden.append(part)
    traits = {f"{themes[0]}_confidence": 0.3}
    return {"name": name, "traits": traits, "beliefs": [], "goals": goals, "memories": memories,
            "active_themes": themes, "forbidden_themes": forbidden,
            "valence": round(max(-1, min(1, valence)), 2), "stress": round(min(1, stress), 2)}


try:  # prefer the real onboarding module when the engine has it
    from ana import onboarding as _real_onboarding  # type: ignore
    from ana import safety as _real_safety  # type: ignore
    QUESTIONS = _real_onboarding.QUESTIONS
    DISCLAIMER = getattr(_real_safety, "DISCLAIMER", DISCLAIMER)
    _mock_build_seed = build_seed

    def build_seed(answers: dict) -> dict:  # noqa: F811
        res = _real_onboarding.build_seed(answers)
        if res.seed is None:
            return {"_safety": res.safety.model_dump(mode="json")}
        return res.seed.model_dump(mode="json")

    def screen_text(text: str) -> dict:  # noqa: F811
        return _real_safety.screen_text(text).model_dump(mode="json")
except Exception as _e:  # pragma: no cover
    print(f"[mock] using built-in onboarding/safety stand-ins ({_e})", file=sys.stderr)


def record_lite(rec, chapter: str) -> dict:
    s = rec.scene
    return {
        "tick": rec.tick,
        "stage": normalize_stage(s.stage),
        "chapter": chapter,
        "scene": {"title": s.title, "summary": s.summary,
                  "beats": [b.model_dump(mode="json") for b in s.beats],
                  "choices": list(s.choices), "symbols": list(s.symbols),
                  "is_vision": s.is_vision, "source": s.source},
        "action": rec.outcome.user_action.model_dump(mode="json") if rec.outcome.user_action else None,
        "npc_events": list(rec.outcome.npc_events),
        "lesson": rec.outcome.lesson,
        "convergence": round(rec.convergence, 4),
        "success": rec.outcome.success,
    }


# ─────────────────────────────────────────────────────────────
# In-memory "database"
# ─────────────────────────────────────────────────────────────

DB: dict[str, Any] = {"users": {}, "sessions": {}, "journeys": {}}
LOCK = threading.RLock()


def _hash(pw: str, salt: str) -> str:
    return hashlib.sha256((salt + pw).encode()).hexdigest()


def current_user(request: Request) -> Optional[dict]:
    tok = request.cookies.get(COOKIE)
    uid = DB["sessions"].get(tok or "")
    return DB["users"].get(uid) if uid else None


def usage_of(user: dict) -> dict:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if user.get("usage_day") != day:
        user["usage_day"], user["usage_today"] = day, 0
    return {"today": user["usage_today"], "limit": DAILY_LIMIT}


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "created_at": u["created_at"]}


def journey_meta(j: dict) -> dict:
    eng = j["engine"]
    return {"id": j["id"], "status": j["status"], "title": j["title"],
            "cycles": len(eng.history), "created_at": j["created_at"],
            "updated_at": j["updated_at"], "chapter": eng.chapter()}


def identity_payload(u: dict) -> dict:
    seed = u["seed"]
    live = None
    js = sorted((j for j in DB["journeys"].values() if j["user_id"] == u["id"] and j["engine"].history),
                key=lambda j: j["updated_at"])
    if js:
        live = js[-1]["engine"].identity_view()
    goals = [{"description": g["description"], "progress": 0.0, "realised": False,
              "horizon": g.get("horizon", "mid")} for g in seed["goals"]]
    if live:
        goals = [{"description": g["description"], "progress": round(g.get("progress", 0.0), 3),
                  "realised": bool(g.get("realised")), "horizon": g.get("horizon", "mid")}
                 for g in live["goals"]]
    return {"name": seed["name"], "active_themes": (live or {}).get("active_themes") or seed["active_themes"],
            "forbidden_themes": seed["forbidden_themes"], "goals": goals,
            "traits": (live or {}).get("traits") or seed["traits"],
            "insights": (live or {}).get("insights") or []}


# ─────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────

app = FastAPI(title="ANA mock API", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def headers_and_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
        ctype = request.headers.get("content-type", "")
        has_body = request.headers.get("content-length", "0") not in ("", "0")
        if has_body and "application/json" not in ctype:
            return err(415, "unsupported_media_type", "Requests must be JSON.")
        origin = request.headers.get("origin")
        if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
            return err(403, "bad_origin", "Cross-origin request refused.")
    if LATENCY and request.url.path.startswith("/api/"):
        await asyncio.sleep(LATENCY)
    resp = await call_next(request)
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


async def body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def need_user(request: Request):
    u = current_user(request)
    if not u:
        return None, err(401, "unauthenticated", "Please log in.")
    return u, None


@app.get("/healthz")
def healthz():
    return {"ok": True, "db": True, "llm": False}


@app.post("/api/auth/signup")
async def signup(request: Request):
    d = await body(request)
    email = str(d.get("email") or "").strip().lower()
    pw = str(d.get("password") or "")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return err(422, "invalid_email", "That doesn't look like an email address.")
    if len(pw) < 10:
        return err(422, "weak_password", "Your password needs at least 10 characters.")
    if d.get("age_confirmed") is not True:
        return err(422, "age_required", "ANA is for adults — please confirm you are 18 or older.")
    if INVITES:
        code = str(d.get("invite_code") or "").strip()
        if not code:
            return err(403, "invite_required", "ANA is invite-only right now. Please enter your invite code.")
        if code not in INVITES:
            return err(403, "invalid_invite", "That invite code isn't valid.")
    with LOCK:
        if any(u["email"] == email for u in DB["users"].values()):
            return err(409, "email_taken", "An account with that email already exists. Try logging in.")
        salt = secrets.token_hex(8)
        u = {"id": uuid.uuid4().hex[:12], "email": email, "salt": salt, "pw": _hash(pw, salt),
             "created_at": now_iso(), "seed": None, "usage_day": None, "usage_today": 0}
        DB["users"][u["id"]] = u
        tok = secrets.token_urlsafe(24)
        DB["sessions"][tok] = u["id"]
    resp = JSONResponse({"user": public_user(u)}, status_code=201)
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/auth/login")
async def login(request: Request):
    d = await body(request)
    email = str(d.get("email") or "").strip().lower()
    pw = str(d.get("password") or "")
    u = next((x for x in DB["users"].values() if x["email"] == email), None)
    if not u or _hash(pw, u["salt"]) != u["pw"]:
        return err(401, "invalid_credentials", "That email and password don't match.")
    tok = secrets.token_urlsafe(24)
    DB["sessions"][tok] = u["id"]
    resp = JSONResponse({"user": public_user(u)})
    resp.set_cookie(COOKIE, tok, httponly=True, samesite="lax", max_age=30 * 86400)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request):
    DB["sessions"].pop(request.cookies.get(COOKIE) or "", None)
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
def me(request: Request):
    u, e = need_user(request)
    if e:
        return e
    return {"user": public_user(u), "has_identity": u["seed"] is not None,
            "usage": usage_of(u), "llm_available": False}


@app.get("/api/onboarding")
def onboarding_q():
    # `invite_required` is a mock extension the SPA reads if present.
    return {"questions": QUESTIONS, "disclaimer": DISCLAIMER, "invite_required": bool(INVITES)}


@app.post("/api/onboarding")
async def onboarding_post(request: Request):
    u, e = need_user(request)
    if e:
        return e
    d = await body(request)
    answers = d.get("answers")
    if not isinstance(answers, dict):
        return err(422, "invalid_answers", "Answers are missing.")
    for v in answers.values():
        for t in _texts(v):
            s = screen_text(t)
            if s["level"] == "crisis":
                return JSONResponse({"safety": s}, status_code=200)
    seed = build_seed(answers)
    if "_safety" in seed:
        return JSONResponse({"safety": seed["_safety"]}, status_code=200)
    seed["_identity_id"] = "id_" + u["id"]
    u["seed"] = seed
    return JSONResponse({"identity": identity_payload(u)}, status_code=201)


@app.get("/api/identity")
def identity_get(request: Request):
    u, e = need_user(request)
    if e:
        return e
    if not u["seed"]:
        return err(404, "no_identity", "Finish onboarding first.")
    return {"identity": identity_payload(u)}


@app.patch("/api/identity")
async def identity_patch(request: Request):
    u, e = need_user(request)
    if e:
        return e
    if not u["seed"]:
        return err(404, "no_identity", "Finish onboarding first.")
    d = await body(request)
    if "forbidden_themes" in d:
        ft = d["forbidden_themes"]
        if not isinstance(ft, list) or not all(isinstance(x, str) for x in ft) or len(ft) > 50:
            return err(422, "invalid_forbidden_themes", "Send a list of up to 50 words or phrases.")
        u["seed"]["forbidden_themes"] = list(dict.fromkeys(x.strip().lower()[:60] for x in ft if x.strip()))
    if "name" in d:
        u["seed"]["name"] = str(d["name"]).strip()[:60] or "the wanderer"
    return {"identity": identity_payload(u)}


def _owned(u: dict, jid: str) -> Optional[dict]:
    j = DB["journeys"].get(jid)
    return j if j and j["user_id"] == u["id"] else None


@app.get("/api/journeys")
def journeys_list(request: Request):
    u, e = need_user(request)
    if e:
        return e
    js = [journey_meta(j) for j in DB["journeys"].values() if j["user_id"] == u["id"]]
    js.sort(key=lambda m: m["updated_at"], reverse=True)
    return {"journeys": js}


@app.post("/api/journeys")
def journeys_create(request: Request):
    u, e = need_user(request)
    if e:
        return e
    if not u["seed"]:
        return err(409, "no_identity", "Finish onboarding first.")
    active = [j for j in DB["journeys"].values() if j["user_id"] == u["id"] and j["status"] in ("active", "paused_safety")]
    if len(active) >= MAX_ACTIVE:
        return err(409, "too_many_active",
                   f"You can have up to {MAX_ACTIVE} journeys open at once. Finish or end one to begin another.")
    us = usage_of(u)
    if us["today"] >= us["limit"]:
        return err(429, "quota_exceeded", "You've walked far today. New turns open again tomorrow (UTC).")
    n = 1 + sum(1 for j in DB["journeys"].values() if j["user_id"] == u["id"])
    eng = make_journey(u["seed"], seed_value=secrets.randbelow(10_000))
    j = {"id": uuid.uuid4().hex[:10], "user_id": u["id"], "status": "active",
         "title": f"Journey {n}", "created_at": now_iso(), "updated_at": now_iso(),
         "engine": eng, "records": [], "debrief": None, "lock": threading.Lock()}
    DB["journeys"][j["id"]] = j
    pending = eng.pending()
    return JSONResponse({"journey": journey_meta(j), "pending": pending}, status_code=201)


@app.get("/api/journeys/{jid}")
def journey_get(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    out = {"journey": journey_meta(j), "records": j["records"]}
    if j["status"] in ("active", "paused_safety") and not j["engine"].closed:
        out["pending"] = j["engine"].pending()
    return out


@app.get("/api/journeys/{jid}/pending")
def journey_pending(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    if j["status"] in ("closed", "ended") or j["engine"].closed:
        return {"closed": True}
    return {"pending": j["engine"].pending()}


@app.post("/api/journeys/{jid}/act")
async def journey_act(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    if j["status"] in ("closed", "ended") or j["engine"].closed:
        return err(409, "journey_closed", "This journey has come home.")
    d = await body(request)
    ci, text = d.get("choice_index"), d.get("text")
    text = text.strip()[:400] if isinstance(text, str) else None
    if ci is None and not text:
        return err(422, "empty_action", "Choose something, or write what you do.")
    if j["status"] == "paused_safety":
        return err(409, "journey_paused", "This journey is paused. Resume it when you're ready.")
    if not j["lock"].acquire(blocking=False):
        return err(409, "busy", "Still dreaming the last turn — one moment.")
    try:
        us = usage_of(u)
        if us["today"] >= us["limit"]:
            return err(429, "quota_exceeded",
                       "You've walked far today. The story will be waiting tomorrow (turns reset at 00:00 UTC).")
        if text:
            s = screen_text(text)
            if s["level"] == "crisis":
                j["status"] = "paused_safety"
                j["updated_at"] = now_iso()
                return {"record": None, "closed": False, "debrief_ready": False, "safety": s}
        # The engine (and the runtime's shard memory, which drives its own
        # asyncio loop) must run off the server's event loop.
        return await run_in_threadpool(_resolve_turn, u, j, ci, text)
    finally:
        j["lock"].release()


def _resolve_turn(u: dict, j: dict, ci: Any, text: Optional[str]):
    eng = j["engine"]
    p = eng.pending()
    choices = p["scene"]["choices"] if p else []
    if ci is not None and not text:
        if not isinstance(ci, int) or isinstance(ci, bool) or not 0 <= ci < len(choices):
            return err(422, "bad_choice", "That choice isn't available.")
        action = UserAction(raw=choices[ci], choice_index=ci)
    else:
        action = UserAction(raw=text)
    rec = eng.act(action)
    u["usage_today"] += 1
    lite = record_lite(rec, _chapter_of(eng.history))
    j["records"].append(lite)
    j["status"] = "closed" if eng.closed else "active"
    j["updated_at"] = now_iso()
    out: dict[str, Any] = {"record": lite, "closed": eng.closed, "debrief_ready": eng.closed}
    if not eng.closed:
        out["pending"] = eng.pending()
    else:
        _make_debrief(j)
    return out


def _make_debrief(j: dict) -> None:
    if j["debrief"] is None:
        d = j["engine"].debrief()
        j["debrief"] = {"debrief": d.model_dump(mode="json"), "markdown": d.to_markdown()}
        j["title"] = d.title or j["title"]


@app.post("/api/journeys/{jid}/resume")
async def journey_resume(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    if (await body(request)).get("acknowledged") is not True:
        return err(422, "acknowledgement_required", "Please confirm you're ready to continue.")
    if j["status"] in ("closed", "ended"):
        return err(409, "journey_closed", "This journey has come home.")
    j["status"] = "active"
    out = {"journey": journey_meta(j)}
    if not j["engine"].closed:
        out["pending"] = await run_in_threadpool(j["engine"].pending)
    return out


@app.post("/api/journeys/{jid}/end")
def journey_end(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    if j["status"] not in ("closed", "ended"):
        j["engine"].end()
        j["status"] = "ended"
        j["updated_at"] = now_iso()
    _make_debrief(j)
    return {"journey": journey_meta(j), "debrief_ready": True}


@app.get("/api/journeys/{jid}/debrief")
def journey_debrief(jid: str, request: Request):
    u, e = need_user(request)
    if e:
        return e
    j = _owned(u, jid)
    if not j:
        return err(404, "not_found", "That journey doesn't exist.")
    if j["status"] not in ("closed", "ended"):
        return err(409, "not_ready", "The debrief is written when the journey comes home or ends.")
    _make_debrief(j)
    return j["debrief"]


@app.get("/api/me/export")
def export(request: Request):
    u, e = need_user(request)
    if e:
        return e
    data = {"exported_at": now_iso(), "user": public_user(u),
            "identity": identity_payload(u) if u["seed"] else None, "seed": u["seed"],
            "journeys": [{**journey_meta(j), "records": j["records"], "debrief": j["debrief"]}
                         for j in DB["journeys"].values() if j["user_id"] == u["id"]],
            "usage": usage_of(u), "shards": []}
    return Response(json.dumps(data, indent=2, ensure_ascii=False), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="ana-export.json"'})


@app.delete("/api/me")
async def delete_me(request: Request):
    u, e = need_user(request)
    if e:
        return e
    d = await body(request)
    if _hash(str(d.get("password") or ""), u["salt"]) != u["pw"]:
        return err(403, "invalid_password", "That password isn't right.")
    with LOCK:
        for jid in [k for k, j in DB["journeys"].items() if j["user_id"] == u["id"]]:
            DB["journeys"].pop(jid)
        for tok in [t for t, uid in DB["sessions"].items() if uid == u["id"]]:
            DB["sessions"].pop(tok)
        DB["users"].pop(u["id"], None)
    resp = Response(status_code=204)
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/{rest:path}")
def api_404(rest: str):
    return err(404, "not_found", "No such endpoint.")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
