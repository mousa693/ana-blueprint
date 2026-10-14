# Deploying ANA

ANA's web product (`ana/app`, FastAPI) ships as one container image: a
single uvicorn worker that serves the JSON API and the web app, and keeps its
data on a `/data` volume (SQLite by default, Postgres optional).

| file | what it is |
|---|---|
| `Dockerfile` | multi-stage build: wheel → venv → `python:3.12-slim-bookworm`, uid 10001, `/data` volume, HEALTHCHECK |
| `docker-compose.yml` | `app` (+ `postgres` profile, + `caddy` profile for automatic HTTPS) |
| `.env.example` | every setting, with safe placeholders |
| `deploy/Caddyfile` | TLS reverse proxy for any VPS |
| `deploy/fly.toml` | Fly.io: one Machine + volume at `/data` |
| `deploy/render.yaml` | Render Blueprint: Docker web service + disk at `/data` |
| `scripts/smoke.sh` | end-to-end API check against any running server |
| `.github/workflows/ci.yml` | tests, image build, container smoke test |

---

## Quickstart (local, Docker Compose)

```bash
cp .env.example .env
# put a real secret in .env:
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # → ANA_SECRET_KEY=...
docker compose up -d --build
docker compose ps                          # wait for "healthy"
scripts/smoke.sh http://127.0.0.1:8000     # optional: full API round trip
```

Open <http://localhost:8000>. The port is bound to `127.0.0.1` only; put a TLS
proxy in front before letting anyone else in (see [Any VPS + Caddy](#any-vps--caddy)).

Plain `docker run` works too:

```bash
docker build -t ana:local .
docker run -d --name ana -p 127.0.0.1:8000:8000 -v ana-data:/data \
  -e ANA_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')" \
  ana:local
```

Building behind a TLS-intercepting corporate proxy: pass its CA bundle as a
build secret (used only during `pip install`, never stored in the image):
`docker build --secret id=pip_ca,src=/path/to/ca.crt -t ana:local .`

### What is in the image

Only the installed `ana-engine` wheel (with the `llm` and `postgres` extras) in
`/opt/venv`, on top of `python:3.12-slim-bookworm`. No source tree, tests,
docs, screenshots, `.git`, `.env`, pip or build tools (`.dockerignore` is an
allow-list). It runs as `ana` (uid/gid 10001), workdir `/app`, and writes only
to `/data` (and `/tmp`) — compose runs it with a read-only root filesystem,
no capabilities and `no-new-privileges`.

Defaults baked into the image:

| var | default in the image |
|---|---|
| `ANA_DATABASE_URL` | `sqlite:////data/ana.db` |
| `ANA_SHARD_DB` | `/data/shards.db` |
| `PORT` | `8000` (the port uvicorn listens on) |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` — leave it; the app derives the client IP itself (see [Client IP](#client-ip)) |

The HEALTHCHECK calls `GET /healthz` with Python's `urllib` (no curl in the
image), sending `Host: localhost` — or the first entry of `ANA_ALLOWED_HOSTS`
if you removed `localhost` from it. `/healthz` returns
`{"ok": true, "db": true, "llm": false}` (503 when the database is unreachable).

---

## Configuration

All settings are environment variables; `.env.example` lists every one with
comments. The ones that matter in production:

| var | production value |
|---|---|
| `ANA_SECRET_KEY` | **required.** ≥16 chars; use `secrets.token_urlsafe(48)`. Rotating it logs everyone out. |
| `ANA_DEV` | `0` (never `1` in production: random secret, non-Secure cookies) |
| `ANA_PUBLIC_ORIGIN` | the exact URL people type, e.g. `https://ana.example.com` (no trailing slash) |
| `ANA_ALLOWED_HOSTS` | `localhost` (plus any other names you serve; the public origin's host is added automatically) |
| `ANA_CLIENT_IP_HEADER` / `ANA_TRUSTED_PROXY_HOPS` | how the app finds the client IP behind your proxy — see [Client IP](#client-ip) (Fly: `fly-client-ip`; Render / Caddy: hops `1`) |
| `ANTHROPIC_API_KEY` | optional; without it every journey is deterministic (offline) |
| `ANA_DAILY_LLM_CYCLES_TOTAL` | global daily cap on **model calls** (every request to the API: scenes, interpretation, NPC lines, onboarding, safety screens, debriefs) — your spend ceiling. A model-dreamed turn is typically 2–3 calls, onboarding ~4 |
| `ANA_DAILY_CYCLES_PER_USER` | per-person daily turn cap |
| `ANA_INVITE_CODES` / `ANA_ALLOW_SIGNUPS` | who may sign up |

### How the request guard uses Host and Origin

`ana/app/security.py` checks every request:

1. **Host allowlist.** The `Host` header (port ignored) must be in
   `ANA_ALLOWED_HOSTS` ∪ {host of `ANA_PUBLIC_ORIGIN`}; otherwise
   `400 bad_host`. So with `ANA_PUBLIC_ORIGIN=https://ana.example.com` you do
   **not** need to repeat `ana.example.com` in `ANA_ALLOWED_HOSTS` — but keep
   `localhost` there for the container health check. Setting
   `ANA_ALLOWED_HOSTS` *replaces* the default `localhost,127.0.0.1`.
2. **Origin (CSRF).** On POST/PATCH/DELETE, an `Origin` (or else `Referer`)
   header must equal `http://<Host>`, `https://<Host>` (Host *with* its port, as
   sent) or `ANA_PUBLIC_ORIGIN`. `Origin: null` and `Sec-Fetch-Site: cross-site`
   are refused (`403 bad_origin`).
3. **JSON only**, at most 32 KiB per `/api` body (`415` / `413`).

Consequences for a reverse proxy: it must pass the original `Host` through
unchanged (Caddy and Fly/Render do by default; for nginx use
`proxy_set_header Host $host;`). Nothing else needs rewriting.

`ANA_PUBLIC_ORIGIN` starting with `https://` also turns on
`Strict-Transport-Security` and keeps cookies `Secure`. Cookies are `Secure`
whenever `ANA_DEV=0`; browsers accept Secure cookies on plain-http
`localhost`/`127.0.0.1`, so the local quickstart works without TLS, but any
other plain-http hostname will not keep you logged in — use TLS.

### Client IP

The per-IP limits (log-in/sign-up 20/min per IP; failed log-ins 8/min per
email *and* IP) key on the client IP, which the app derives itself — it does
**not** rely on uvicorn's `--proxy-headers`:

1. `ANA_CLIENT_IP_HEADER` — a header your edge proxy **sets** (overwriting
   whatever the client sent), e.g. `fly-client-ip`;
2. else, with `ANA_TRUSTED_PROXY_HOPS=N` (N ≥ 1), the N-th entry from the
   **right** of `X-Forwarded-For` — the address your own proxy appended. The
   entries to its left are written by the client and are never used;
3. else the TCP peer (direct exposure, or a proxy whose address is fine to
   share a bucket).

| where | setting |
|---|---|
| Fly.io (`deploy/fly.toml`) | `ANA_CLIENT_IP_HEADER=fly-client-ip` |
| Render (`deploy/render.yaml`) | `ANA_TRUSTED_PROXY_HOPS=1` (rightmost `X-Forwarded-For`). Only switch to a header such as `true-client-ip` after checking in your own logs that Render sets it on every request — a header the edge does not overwrite is forgeable. |
| Caddy profile / your own Caddy or nginx | `ANA_TRUSTED_PROXY_HOPS=1` (Caddy replaces an untrusted client's `X-Forwarded-For` with the real peer; nginx: `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`) |
| a CDN in front of that proxy | one more hop per proxy that appends, e.g. `2`, or the CDN's client-IP header if the proxy behind it cannot be bypassed |
| nothing in front | leave both unset |

Keep `FORWARDED_ALLOW_IPS` at the image default `127.0.0.1`. **Never** set it
to `*`: uvicorn then takes the *leftmost* `X-Forwarded-For` entry as the
client, which anyone can forge (`X-Forwarded-For: 1.2.3.4, …`) to get a fresh
rate-limit bucket per request. The app logs a warning at startup when it sees
`FORWARDED_ALLOW_IPS=*` without either setting above. The app port must still
be reachable only through the proxy (otherwise a client can skip the proxy
and write the header itself).

### Abuse limits and budgets

* **Per person, per minute**: 30 turns, 10 new journeys, 10 identity edits,
  5 onboarding submissions (429 + `Retry-After`).
* **Per person, per UTC day** (`ANA_DAILY_CYCLES_PER_USER`): every turn, every
  journey's first scene and every onboarding submission takes one unit,
  reserved atomically (concurrent requests cannot overshoot it).
* **Active journeys** (`ANA_MAX_ACTIVE_JOURNEYS`): checked and inserted under
  the person's lock, so parallel creates cannot exceed it.
* **Global model budget** (`ANA_DAILY_LLM_CYCLES_TOTAL`): counts every model
  call the shared gateway makes. A request reserves a unit atomically before
  the model may be used and is then settled to its actual call count, so the
  counter equals real model requests; past the cap everything runs
  deterministically. Requests already in flight when it runs out may finish
  their few calls, so the counter can end a handful above the cap.
* **Safety screen**: always runs (deterministic floor). The model second
  opinion is limited per person (`ANA_SAFETY_MODEL_PER_HOUR`, default 60) and
  by the global budget; beyond that the deterministic floor alone decides.

---

## Production checklist

- [ ] **Secret**: `ANA_SECRET_KEY` from `secrets.token_urlsafe(48)`, stored as a
      secret (not in git, not baked in the image). `ANA_DEV=0`.
- [ ] **Public origin**: `ANA_PUBLIC_ORIGIN=https://<your domain>`.
- [ ] **Allowed hosts**: `ANA_ALLOWED_HOSTS=localhost` (plus extra names you serve).
- [ ] **TLS** in front (Caddy profile, Fly, Render, or your own proxy) and the
      [client IP](#client-ip) setting for it (`ANA_CLIENT_IP_HEADER` or
      `ANA_TRUSTED_PROXY_HOPS`); `FORWARDED_ALLOW_IPS` stays `127.0.0.1`.
      The app port is never public.
- [ ] **Backups** of `/data` (both SQLite files) or of Postgres **and**
      `/data/shards.db` — scheduled, copied off the host, restore tested.
- [ ] **Model**: `ANTHROPIC_API_KEY` set as a secret if you want model-dreamed
      journeys; `ANA_DAILY_LLM_CYCLES_TOTAL` sized to your budget (turns beyond
      it run deterministically, they do not fail); `ANA_DAILY_CYCLES_PER_USER`
      sized to fair use. `/healthz` reports `"llm": true` when the model is on.
- [ ] **Invites**: for a closed beta set `ANA_INVITE_CODES=code1,code2,...`
      (generate with `secrets.token_urlsafe(9)`), or `ANA_ALLOW_SIGNUPS=0` to
      freeze sign-ups.
- [ ] **One instance, one worker** (see [Limits of v1](#limits-of-v1)).
- [ ] Run `scripts/smoke.sh https://<your domain>` after each deploy (it
      creates and then deletes a throwaway account; with invites on, pass
      `SMOKE_INVITE_CODE=...`).

### Backups

`/data` holds `ana.db` (accounts, journeys, snapshots; SQLite in WAL mode, so
also `ana.db-wal`/`ana.db-shm`) and `shards.db` (the memory shards). **Do not
copy the `.db` files of a running server** — a plain copy can miss the WAL or
catch a half-written page. Use SQLite's online backup API, which is consistent
while the app keeps serving (the image has Python, no `sqlite3` CLI needed):

```bash
docker compose exec -T app python - <<'PY'
import os, sqlite3, time
os.makedirs("/data/backups", mode=0o700, exist_ok=True)
ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
for name in ("ana", "shards"):
    src = f"/data/{name}.db"
    if os.path.exists(src):
        with sqlite3.connect(src) as s, sqlite3.connect(f"/data/backups/{name}-{ts}.db") as d:
            s.backup(d)
        print(f"/data/backups/{name}-{ts}.db")
PY
docker compose cp app:/data/backups ./backups-$(date +%F)   # get them off the host
docker compose exec -T app sh -c 'rm -rf /data/backups'
```

(`docker exec -i ana python - <<'PY' ...` for a plain `docker run` container;
on Fly use `fly ssh console -C "python -c '...'"` and `fly ssh sftp get`.)

Restore: stop the app, put `ana-<ts>.db` → `/data/ana.db` and
`shards-<ts>.db` → `/data/shards.db`, **delete any `ana.db-wal`/`ana.db-shm`**,
make sure the files are owned by uid 10001, start the app.

With Postgres: `docker compose exec -T postgres pg_dump -U ana -Fc ana > ana-$(date +%F).dump`
**plus** the `shards.db` backup above — the shards are always SQLite on `/data`.

### Postgres

```bash
# .env
POSTGRES_PASSWORD=<random, URL-safe>          # e.g. secrets.token_hex(24)
ANA_DATABASE_URL=postgresql://ana:<same password>@postgres:5432/ana

docker compose --profile postgres up -d --build
```

`postgres://` and `postgresql://` URLs are switched to the psycopg 3 driver
automatically (the image includes `psycopg[binary]`); a managed Postgres works
the same way — just set `ANA_DATABASE_URL` (add `?sslmode=require` if your
provider needs it; URL-encode special characters in the password). Tables are
created on startup. The app logs its effective config with the password
redacted. Switching an existing deployment from SQLite to Postgres does not
migrate data (v1 has no migration tool); start fresh or move rows yourself.

---

## Fly.io

`deploy/fly.toml`: one Machine, a volume `ana_data` mounted at `/data`, HTTPS
by Fly's proxy, `/healthz` check with `Host: localhost`, client IP from
`Fly-Client-IP` (`ANA_CLIENT_IP_HEADER=fly-client-ip`). A 512 MB VM is enough:
password hashing is capped at 2 concurrent argon2 operations of 19 MiB each
(see [Security notes](#security-notes)).

```bash
# edit deploy/fly.toml: app name, primary_region, ANA_PUBLIC_ORIGIN
fly launch --no-deploy --copy-config --config deploy/fly.toml --name <your-app>
fly volumes create ana_data --size 3 --region <same region> --config deploy/fly.toml
fly secrets set --config deploy/fly.toml \
  ANA_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
fly secrets set --config deploy/fly.toml ANTHROPIC_API_KEY=sk-ant-...      # optional
fly secrets set --config deploy/fly.toml ANA_INVITE_CODES=code1,code2       # optional
fly deploy . --config deploy/fly.toml --dockerfile Dockerfile
scripts/smoke.sh https://<your-app>.fly.dev
```

Keep the app at one Machine (`fly scale count 1`); a volume belongs to one
Machine anyway. For a custom domain: `fly certs add ana.example.com`, then set
`ANA_PUBLIC_ORIGIN=https://ana.example.com` in `[env]` and redeploy. Volume
snapshots are taken daily by Fly; still run the backup above for off-site copies.

## Render

`deploy/render.yaml` is a Blueprint for one Docker web service with a 5 GB disk
at `/data`. Render reads `render.yaml` at the repository root — copy it there
(or select `deploy/render.yaml` as the Blueprint file), set
`ANA_PUBLIC_ORIGIN` to your `https://<name>.onrender.com` URL (or custom
domain), then **New → Blueprint** in the dashboard. `ANA_SECRET_KEY` is
generated by Render and stays stable; set `ANTHROPIC_API_KEY` /
`ANA_INVITE_CODES` in the dashboard. The client IP is the rightmost
`X-Forwarded-For` entry (`ANA_TRUSTED_PROXY_HOPS=1`, see [Client IP](#client-ip)).

A disk requires a paid instance type and limits the service to one instance
with no zero-downtime deploys (each deploy stops the old container first) — the
right shape for v1. Render's health check uses `/healthz`; if it fails with
`400 bad_host` in the logs, add the Host the checker sends to
`ANA_ALLOWED_HOSTS`.

## Any VPS + Caddy

On a fresh host with Docker and a DNS `A`/`AAAA` record for your domain:

```bash
git clone <repo> ana && cd ana
cp .env.example .env
# in .env:
#   ANA_SECRET_KEY=<secrets.token_urlsafe(48)>
#   ANA_PUBLIC_ORIGIN=https://ana.example.com
#   ANA_ALLOWED_HOSTS=localhost
#   ANA_DOMAIN=ana.example.com
#   ANA_TRUSTED_PROXY_HOPS=1
docker compose --profile caddy up -d --build              # + --profile postgres if wanted
scripts/smoke.sh https://ana.example.com
```

Caddy (`deploy/Caddyfile`) obtains and renews certificates automatically
(ports 80 and 443 must be open), forwards the original `Host`, adds
`X-Forwarded-For`/`-Proto`, and proxies to `app:8000` on the compose network.
The app's own port stays on `127.0.0.1:8000` for local checks; set
`ANA_BIND=127.0.0.1` (default) and never publish it on `0.0.0.0` — a client
reaching the app directly could write its own `X-Forwarded-For`.
Try it locally with `ANA_DOMAIN=localhost` and `ANA_PUBLIC_ORIGIN=https://localhost`
(Caddy then uses its own local CA).

Using a Caddy you already run on the host instead: replace `app:8000` with
`127.0.0.1:8000` in the Caddyfile and keep `ANA_TRUSTED_PROXY_HOPS=1`
(see [Client IP](#client-ip)).

---

## Upgrading

```bash
# 1. back up (see Backups)          2. update the code
git pull
# 3. rebuild and restart — in-flight journeys are flushed on SIGTERM
docker compose up -d --build        # add the same --profile flags you use
docker compose ps                   # healthy?
scripts/smoke.sh http://127.0.0.1:8000
```

Tables are created on startup and a `schema_version` row is checked: a server
refuses to start on a database written by a *newer* schema (roll back the image
or restore the backup). v1 has no migration framework, so read the release
notes before upgrading across a schema change. Journey snapshots are pickled
engine state: a release that changes engine classes may not restore old
in-progress journeys — finish or end them first when the notes say so.
Rolling back: `docker compose up -d` with the previous image tag
(`ANA_IMAGE=ana:<tag>`) and, if the schema changed, the backup.

On Fly: `fly deploy` (the volume is kept). On Render: push to the tracked
branch (or Manual Deploy).

---

## Limits of v1

**One process, one worker, one instance.** The image runs uvicorn with
`--workers 1` on purpose, and every deploy target above runs exactly one
instance:

* **Runtime cache.** A journey's live engine (`JourneyRuntime`) is kept in an
  in-process LRU (`ana/app/runtimes.py`, `ANA_RUNTIME_CACHE_SIZE`). It is
  restored from the DB snapshot on a cache miss and re-snapshotted after each
  turn, but a second process with its own cached copy would not see the
  first's turns: it would act on a stale state and its snapshot would
  overwrite the other's — a forked or rewound journey.
* **Per-journey locks** ("one active request per journey") are
  `threading.Lock`s in that same process; they do not exclude another worker
  or machine.
* **Per-person locks** (`ana/app/userlocks.py`: create/onboarding/identity
  edits/deletion exclusive, turns shared) are in-process too. The daily-turn
  and global model-budget counters are atomic in the database and would stay
  correct across processes; the active-journey cap relies on the lock.
* **Rate limiters** (sign-up/log-in, turns per minute) are in-process token
  buckets; N processes would allow N× the limits.
* **Shards** live in one SQLite file on the local `/data` volume, which cannot
  be shared between machines.

To scale horizontally you would need either **sticky routing by journey/user**
(every request for a journey lands on the same process — e.g. hash on the
session cookie at the load balancer — still with care on rebalancing) or a
**shared coordination layer**: a cross-process lock per journey (Postgres
advisory locks or Redis), cache invalidation by snapshot version (reload when
the DB copy is newer), shared rate-limit counters, and a shared shard store.
One worker comfortably serves a small community: the engine is CPU-light
when deterministic, and model-dreamed turns spend their time waiting on the
API in FastAPI's thread pool.

Other v1 limits: no email (no password reset or verification — an operator
can delete an account; the user can export and delete their own), no admin UI,
no schema migration tool, daily budgets reset at 00:00 UTC.

---

## Security notes

* **Passwords**: argon2id with m=19 MiB, t=2, p=1 — the OWASP Password
  Storage Cheat Sheet minimum — rather than the library default (64 MiB,
  t=3, p=4), because memory is the scarce resource on a small VM. At most
  `ANA_PASSWORD_HASH_CONCURRENCY` (default 2) hashes/verifies run at once
  process-wide; a request that waits more than ~2 s for a slot gets
  `503 busy` with `Retry-After: 2` instead of queueing (a burst of logins
  cannot exhaust memory: peak ≈ 2 × 19 MiB above baseline). Hashes made with
  other parameters keep working and are re-hashed on the next successful
  log-in.
* **Log-in throttling** counts only *failed* attempts: strictly per
  (email, client IP) (8/min) and loosely per email across all IPs (burst 50,
  then 5/min). Someone guessing at your email from their own address cannot
  lock you out; only a distributed attack above the global ceiling can.
* **Account existence**: sign-up answers `409 email_taken` for an existing
  address (kept for usability; there is no email verification in v1), so
  sign-up does reveal whether an address has an account. The timing of the
  409 is equalised with a real sign-up (a dummy argon2 hash), and log-in
  answers the same `401` in the same time for unknown and known addresses.
  Sign-up is rate-limited per IP.
* **Account deletion** holds the person's lock: in-flight requests finish
  first, new ones wait and then see the account gone; shards are purged and
  cached journey runtimes closed (without flushing) once more after the
  commit.
* **Never-show words** are redacted at read time from journey titles and
  debriefs (`ana/app/redact.py`), so adding a word in Settings applies to past
  journeys immediately. The export (`GET /api/me/export`) is the raw data.

## API notes for clients

* `POST /api/journeys/{id}/act` takes `expected_tick` — the
  `pending.journey_tick` of the scene being answered. If the story has moved
  on (another tab), the answer is `409` with
  `{"error": {"code": "scene_changed", …}, "pending": {…}, "journey": {…}}` and
  nothing is played. If the request repeats the last turn exactly (same tick,
  same `choice_index` / `text` — a lost response being retried) the stored
  result is returned with `"replayed": true` (header `X-ANA-Replayed: 1`),
  without playing or charging another turn. `expected_tick` is optional for
  backward compatibility; requests without it get neither protection, so all
  clients should send it.
