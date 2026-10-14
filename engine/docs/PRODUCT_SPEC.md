# ANA — product spec (v1, public multi-user)

This is the shared contract for building ANA into a live product. Every part of
the product is built against it. If something here is ambiguous, prefer the
simplest reading and note it in your report — do not invent a different shape.

## What the product is

A web app where a person signs up, is guided through a short onboarding
conversation that seeds their **self** (the Identity Core), and then **lives
journeys**: each turn ANA dreams a scene that mirrors them, they choose or write
what they do, the world responds, and over a journey the Hero's arc unfolds until
they come home and receive a **debrief**. Their self persists across journeys —
the journal remembers trends, goals advance, memories of the future are
fulfilled.

It is **not therapy** and says so plainly. It is 18+. People's inner material is
private: per-user isolation, export, and full deletion are first-class.

## 1. Engine runtime — `ana/runtime.py` (engine package)

The web is turn-based: the person supplies each action. The loop is therefore
split into "show a scene" and "resolve an action", and a journey must survive
process restarts between turns.

```python
class IdentitySeed(BaseModel):          # what onboarding produces
    name: str = "the wanderer"           # how the protagonist is addressed
    traits: dict[str, float] = {}        # e.g. {"leadership_confidence": 0.3}
    beliefs: list[str] = []
    goals: list[SeedGoal] = []           # SeedGoal: description, horizon("near"|"mid"|"life"), is_future_memory: bool
    memories: list[SeedMemory] = []      # SeedMemory: summary, themes: list[str], symbols: list[str]
    active_themes: list[str] = []        # canonical lexicon themes
    forbidden_themes: list[str] = []     # never shown, enforced by the engine's safety layers
    valence: float = 0.0                 # starting mood -1..1
    stress: float = 0.2

class PendingScene(BaseModel):          # what the UI renders while waiting for the person
    journey_tick: int                    # 0-based tick of the cycle in progress
    scene: Scene                         # ana.contracts.Scene (has stage, beats, choices...)
    chapter: str                         # e.g. "Chapter 2: Crossing the Threshold"
    stage: str | None
    convergence: float                   # current convergence (before this cycle resolves)
    location: str

class JourneyRuntime:
    @classmethod
    def start(cls, seed: IdentitySeed, *, identity_id: str, shard_db: str | None,
              use_llm: bool, gateway: ModelGateway | None = None, seed_value: int = 42,
              max_cycles: int = 30) -> "JourneyRuntime": ...
    def pending(self) -> PendingScene          # generates the next scene if none is pending; idempotent
    def act(self, action: UserAction) -> CycleRecord   # resolves the pending scene; raises if journey closed
    @property closed -> bool                   # journey came home or hit max_cycles
    def end(self) -> None                       # close early (person chose to stop)
    def debrief(self) -> Debrief                # ana.engine.arc.Debrief
    @property history -> list[CycleRecord]
    def insights(self) -> list[str]             # journal meta-analysis of the self
    def snapshot(self) -> bytes                 # full resumable state, excluding live handles
    @classmethod
    def restore(cls, blob: bytes, *, shard_db: str | None,
                gateway: ModelGateway | None = None) -> "JourneyRuntime": ...
    def close(self) -> None                     # release memory/db handles
```

Rules:
- `pending()` then `act()` must produce exactly the same records as `step()` with a
  driver returning the same actions (the CLI path stays the reference).
- `snapshot()` → `restore()` → continuing must produce the same next scene and
  records as continuing without the round-trip (deterministic mode).
- Memory shards live in `shard_db` scoped by `identity_id`, so a person's self
  persists across journeys and across restarts; different people never share.
- The model gateway, sqlite connections and event loops are never pickled; they
  are re-attached on restore.
- The snapshot is stored server-side only (never sent to a browser).

## 2. Onboarding & safety — `ana/onboarding.py`, `ana/safety.py`

`onboarding.QUESTIONS` — an ordered list of question dicts the UI renders:
`{id, prompt, help, kind: "text"|"choice"|"multi"|"scale", options?, optional: bool}`.
Roughly 6–8 questions, warm and short: what to call you; what you're carrying
right now (themes, multi-select from the lexicon + free text); something you
want to become (a goal); a place or image from your past that still matters
(seeds a motif/memory); how you're feeling lately (valence/stress); anything ANA
must never show you (forbidden themes, multi + free text); 18+ confirmation is
handled at signup, not here.

`onboarding.build_seed(answers: dict, gateway=None, use_llm=False) -> IdentitySeed`
— deterministic mapping via the theme lexicon (`ana.identity.core.canonical_themes`),
optional Claude extraction with a strict schema that falls back to deterministic.
All free text is clipped and treated as data.

`safety.screen_text(text: str) -> SafetyResult` — `{level: "none"|"concern"|"crisis",
reasons: list[str], message: str | None, resources: list[{name, url, note}]}`.
Used on every free-text answer/action before it touches the engine. "crisis"
(self-harm/suicide intent, harm to others, abuse in progress) pauses the journey
and shows resources; nothing about it is fed to the engine or the model.
`safety.RESOURCES` — international default (findahelpline.com, emergency number
note), easy to localise. `safety.DISCLAIMER` — short "not therapy, 18+" text.

## 3. Backend — `ana/app/` (FastAPI)

Config from env (`ana/app/config.py`):

| var | default | meaning |
|---|---|---|
| `ANA_DATABASE_URL` | `sqlite:///./ana_data/ana.db` | SQLAlchemy URL (Postgres supported) |
| `ANA_SHARD_DB` | `./ana_data/shards.db` | MCMG shard store (sqlite, one file) |
| `ANA_SECRET_KEY` | (required unless `ANA_DEV=1`) | signs session cookies |
| `ANA_DEV` | `0` | dev mode: insecure cookies allowed, auto secret |
| `ANTHROPIC_API_KEY` | unset | enables Claude-dreamed journeys |
| `ANA_USE_LLM` | `auto` | `auto` (on if key present) / `on` / `off` |
| `ANA_ALLOW_SIGNUPS` | `1` | |
| `ANA_INVITE_CODES` | unset | comma list; if set, signup requires one |
| `ANA_DAILY_CYCLES_PER_USER` | `60` | per-user per-UTC-day turn cap |
| `ANA_DAILY_LLM_CYCLES_TOTAL` | `2000` | global cap on model-dreamed turns per day; beyond → deterministic |
| `ANA_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host header allowlist |
| `ANA_PUBLIC_ORIGIN` | unset | e.g. `https://ana.example.com` (Origin check + secure cookies) |
| `ANA_MAX_ACTIVE_JOURNEYS` | `3` | per user |

Data (SQLAlchemy 2.x, created on startup; simple, no migrations framework in v1
beyond a `schema_version` table):
`users(id, email unique lower, password_hash argon2, created_at, deleted_at)`,
`identities(id, user_id unique, identity_id (shard scope, random), name, seed_json, created_at, updated_at)`,
`journeys(id, user_id, status: active|closed|ended|paused_safety, use_llm, seed_value, created_at, updated_at, cycles, snapshot BLOB, title)`,
`cycles(id, journey_id, tick, record_json, created_at)`,
`debriefs(journey_id pk, json, markdown, created_at)`,
`usage(user_id, day, cycles, llm_cycles)`, `safety_events(id, user_id, journey_id, level, created_at)` (store level/time only, never the text).

Auth: email + password (argon2id), min length 10, session cookie
(`HttpOnly; SameSite=Lax; Secure` unless dev) signed with itsdangerous, 30-day
expiry, server-side session id table so logout/delete revoke. Login/signup rate
limited per IP and per email (in-process token bucket is fine for v1). CSRF:
SameSite=Lax + Origin/Host checks on every non-GET (reuse the viewer's guard
pattern) + JSON-only bodies.

API (all JSON; errors `{error: {code, message}}`):

```
POST   /api/auth/signup   {email, password, age_confirmed: true, invite_code?} -> 201 {user}
POST   /api/auth/login    {email, password}                                    -> {user}
POST   /api/auth/logout                                                         -> 204
GET    /api/me                                  -> {user, has_identity, usage:{today, limit}, llm_available}

GET    /api/onboarding                          -> {questions, disclaimer}
POST   /api/onboarding    {answers}             -> 201 {identity} | 200 {safety} if crisis
GET    /api/identity                            -> {identity: {name, active_themes, forbidden_themes, goals:[{description,progress,realised}], traits, insights}}
PATCH  /api/identity      {forbidden_themes?, name?}  -> {identity}

GET    /api/journeys                            -> {journeys:[{id,status,title,cycles,created_at,updated_at,chapter}]}
POST   /api/journeys      {}                    -> 201 {journey, pending}   (409 if too many active)
GET    /api/journeys/{id}                       -> {journey, records:[CycleRecord-lite], pending?}
GET    /api/journeys/{id}/pending               -> {pending} | {closed:true}
POST   /api/journeys/{id}/act {choice_index?, text?} ->
          {record: CycleRecord-lite, pending?: PendingScene, closed: bool, debrief_ready: bool, safety?: SafetyResult}
POST   /api/journeys/{id}/end                   -> {journey, debrief_ready: true}
GET    /api/journeys/{id}/debrief               -> {debrief, markdown}
GET    /api/me/export                           -> JSON download of everything about the user (incl. shards for their identity)
DELETE /api/me            {password}            -> 204, removes user, identity, journeys, cycles, debriefs, usage, sessions, and their shards
GET    /healthz                                 -> {ok:true, db:true, llm:bool}
```

`CycleRecord-lite` = `{tick, stage, chapter, scene:{title,summary,beats,choices,symbols,is_vision,source}, action, npc_events, lesson, convergence, success}` — never expose internal alignment nudges, harmonizer internals, or snapshots to the browser.

Turn rules: `act` with neither choice nor text → 422. Text is screened by
`safety.screen_text` first; crisis → journey `paused_safety`, response carries
`safety`, nothing is fed to the engine. Quotas checked before generation (429
with a friendly message). Model use per turn requires the journey's `use_llm` and
the global daily budget; over budget → deterministic for that turn. One active
request per journey at a time (per-journey lock). The runtime is cached
in-process (LRU) and persisted via `snapshot()` after every turn.

## 4. Frontend — `ana/app/static/` (served by the app)

Vanilla JS + CSS, no build step, no CDN, strict CSP, text nodes only (never
innerHTML with data), works at 390px, light/dark. Views (hash routing):
landing (what ANA is, disclaimer, sign up / log in) · signup/login ·
onboarding (one question at a time, gentle) · home (current journeys, start a
new one, "your self": themes, goals with progress, insights) · **play** (the
heart: the scene revealed beat by beat, NPC voices styled by archetype,
synchronicity highlighted, choices as buttons + "or write your own", a small
Hero's-journey track and convergence ribbon, the lesson after each turn, end
journey) · debrief (beautiful, downloadable .md) · settings (never-show list,
export data, delete account). Safety result → calm full-screen card with
resources; journey paused.

## 5. Deploy

`Dockerfile` (python:3.12-slim, non-root, uvicorn single worker, `/data` volume
for sqlite + shards, HEALTHCHECK on /healthz), `docker-compose.yml` (app +
optional Postgres profile), `.env.example`, `docs/DEPLOY.md` (Fly.io, Render, any
VPS + Caddy for TLS), GitHub Actions CI (tests + image build).
