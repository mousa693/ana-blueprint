# Web viewer

A local, offline web UI for exploring ANA journeys: a timeline card per cycle
(scene beats, action, NPC reactions, lesson, vision/synchronicity markers), a
convergence chart with Ω/α surprise, self-alignment small multiples, identity
evolution (traits, emotion, active themes), and a per-cycle alignment &
harmonizer panel. Click any chart point to jump to that cycle.

The journey's shape is shown alongside:

- **Hero's journey track** — six stations (call → threshold → mentor → ordeal →
  reward → return) with the current position and the cycles spent at each.
  Stages come from `scene.stage` when the log has it (exact); older logs fall
  back to the server's arc (which reads stage labels and stage symbols), then
  the title, then convergence (marked `≈ derived`).
- **Convergence chart** — dashed rules mark chapter boundaries, numbered.
- **Chapters & milestones** — consecutive same-stage cycles form a chapter;
  milestones ("crossed the threshold at cycle 4") come from `JourneyArc`.
  Both are clickable. Cycles are numbered from 1 everywhere, matching the
  engine's chapters and milestones (the raw `tick` is shown on each card).
- **Debrief** — at the end of the timeline (and linked from "Read the
  debrief ↓"): title, arc summary, chapters, turning points, what you
  learned, how you changed, who walked with you, recurring symbols, questions
  to carry home, and the closing image as a pull-quote. **Download debrief
  (.md)** saves the markdown version.
- **Cast** (live runs) — everyone met: role, times met, and disposition
  first → now with a warmer ↗ / cooler ↘ / steady → arrow.
- **Cycle cards** show `identity_before.insights` as chips (new ones
  highlighted) and mark a realised goal / fulfilled vision on the cycle where
  it happened (the goal's `realised_at` tick, else the cycle before it first
  appears).

![viewer](viewer.png)

![debrief panel](viewer_debrief.png)

Phone width (390 px): [viewer_mobile.png](viewer_mobile.png).

## Run it

```bash
# optional: produce a log to look at
python3 -m ana.cli demo --cycles 8 --log run.jsonl

python3 -m ana.web --log run.jsonl            # http://127.0.0.1:8765/
python3 -m ana.web --port 9000                # different port, no preloaded log
```

- `--log` can be repeated; each file appears in the run picker.
- **Upload JSONL** loads any `JSONLObserver` log (max 20 MB). Bad lines are
  skipped and counted rather than failing the upload; missing optional fields
  are tolerated. Unknown extra top-level fields are preserved up to 4 KB per
  record (larger ones are dropped and counted as `trimmed`); partial records
  (not a full `CycleRecord`, but with a `tick` and a `scene`) keep only known
  fields. Lines nested deeper than 64 levels are skipped.
- **Start live journey** runs a deterministic in-process session (the seeded
  lighthouse identity, `use_llm=False`) and streams each cycle into the page as
  it completes. With **until home** checked (the default) it runs until the
  journey comes home — like `ana-run auto --until-close` — and `max cycles` is
  only a cap; the debrief and cast appear when it finishes. Unchecked, it runs
  exactly `cycles` cycles.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--host` | `127.0.0.1` | bind address. A specific address is also added to the accepted Host names |
| `--port` | `8765` | |
| `--log FILE` | — | JSONL log to preload (repeatable); never evicted |
| `--allow-host NAME` | — | extra Host-header name to accept (repeatable), e.g. `--host 0.0.0.0 --allow-host viewer.lan`. Binding to a wildcard address accepts only localhost names unless you add these |
| `--allow-llm` | off | let `POST /api/live` request `use_llm=true` (otherwise `403`). Off by default because the endpoint is unauthenticated and LLM calls cost money |

## Security model

The viewer is a local tool with **no authentication**; the server binds to
`127.0.0.1` by default. Pass `--host` only if you deliberately want it
reachable from other machines. What it does defend against:

- **DNS rebinding** — every request must carry a Host header naming
  `localhost`, `127.0.0.1`, `[::1]` (any port), the `--host` address, or an
  `--allow-host` name; anything else gets `400`.
- **CSRF from other sites** — a request with an `Origin` header that is not
  this server's own origin gets `403`. `POST /api/live` additionally requires
  `Content-Type: application/json`, and `POST /api/runs/upload` accepts only
  `multipart/form-data` or `application/x-ndjson` / `application/jsonl`
  (other types → `415`), so a cross-site "simple" request cannot start a run.
- **Memory** — parsed records are accounted with a conservative size
  estimate (~5x the JSONL size for real logs). One upload may not exceed
  160 MB of it (`413`); the whole store is kept under 480 MB and 32 runs by
  evicting the oldest uploads first, then the oldest finished live runs.
  Running live runs and `--log` preloads are never evicted; if nothing can be
  evicted the upload gets `507`. At most 2 uploads are parsed at once.
- **Disk / request size** — upload bodies are counted as they arrive
  (chunked or not) and cut off with `413` at 20 MB (+64 KB multipart
  overhead) *before* any multipart parsing, so nothing is spooled to disk
  beyond that. `/api/live` bodies are capped at 16 KB.
- **Streams** — SSE streams are async (an idle subscriber holds no worker
  thread) and capped at 8 per run and 64 in total (`429` beyond); a closed
  connection frees its slot within about a second. At most 4 live runs run at
  once (`429`; the check is atomic).

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | the single-page app (`ana/web/static/`, no build step, no CDN) |
| GET | `/api/runs` | loaded runs (metadata) |
| POST | `/api/runs/upload` | multipart `file` field, or a raw JSONL body with `Content-Type: application/x-ndjson` or `application/jsonl` (`?name=` sets the label). `413` too large, `415` other content types, `422` nothing usable, `507` store full; metadata includes `skipped`, `partial`, `trimmed` |
| GET | `/api/runs/{id}` | metadata + `records` (CycleRecord dicts); live runs also report `closed` |
| GET | `/api/runs/{id}/arc` | `{chapter, stage, furthest_stage, chapters:[{stage, name, start, end, length, start_tick, end_tick}], milestones, npcs_met, entries:[{cycle, tick, stage}], stage_counts, cycles}` |
| GET | `/api/runs/{id}/debrief` | `{debrief, markdown, cast, closed, cycles, from}`; `?format=md` downloads the markdown. `409` while a live run is still going |
| POST | `/api/live` | `Content-Type: application/json`, body `{cycles=40, until_close=true, seed, use_llm=false, driver: "auto"\|"scripted"}` (empty body = defaults) → `{id, stream}`; with `until_close`, `cycles` is the max. `403` for `use_llm=true` without `--allow-llm`; `429` when 4 live runs are going |
| GET | `/api/live/{id}/stream` | Server-Sent Events: `cycle` per record, each followed by `arc` `{chapter, stage, furthest_stage, cycle, tick}`, then `done` `{status, cycles, closed}` (or `error` first) |

The stream replays from the start for late subscribers and honours
`Last-Event-ID` on reconnect (only `cycle` events carry an id; values that
are not 1–9 ASCII digits are ignored and the stream replays from the start).

Where arc and debrief come from:

- **Uploaded / preloaded logs** — records are parsed back to `CycleRecord`
  (partial records are skipped) and folded into a `JourneyArc` using each
  record's `scene.stage` when present, otherwise inferred; the debrief is
  `build_debrief(records, stages=...)` with no cast (`cast: null`). Old logs
  without `stage`, `insights`, etc. work unchanged. Results are cached per run.
- **Live runs** — when the run finishes, before the session is closed, the
  server captures the session's own `debrief()` (which includes the cast),
  `engine.npc.cast_summary()` and the session arc. `from` is `"session"`
  (or `"records"` if that capture failed and it was rebuilt). Tests: `python3 -m pytest tests/test_web.py -q`.
