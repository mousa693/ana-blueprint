# Web viewer

A local, offline web UI for exploring ANA journeys: a timeline card per cycle
(scene beats, action, NPC reactions, lesson, vision/synchronicity markers), a
convergence chart with Ω/α surprise, self-alignment small multiples, identity
evolution (traits, emotion, active themes), and a per-cycle alignment &
harmonizer panel. Click any chart point to jump to that cycle.

![viewer](viewer.png)

## Run it

```bash
# optional: produce a log to look at
python3 -m ana.cli demo --cycles 8 --log run.jsonl

python3 -m ana.web --log run.jsonl            # http://127.0.0.1:8765/
python3 -m ana.web --port 9000                # different port, no preloaded log
```

- `--log` can be repeated; each file appears in the run picker.
- **Upload JSONL** loads any `JSONLObserver` log (max 20 MB). Bad lines are
  skipped and counted rather than failing the upload; unknown extra fields are
  preserved and missing optional fields are tolerated.
- **Start live journey** runs a deterministic in-process session (the seeded
  lighthouse identity, `use_llm=False`) and streams each cycle into the page as
  it completes.

The server binds to `127.0.0.1` by default. Pass `--host` only if you
deliberately want it reachable from other machines; there is no auth.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/` | the single-page app (`ana/web/static/`, no build step, no CDN) |
| GET | `/api/runs` | loaded runs (metadata) |
| POST | `/api/runs/upload` | multipart `file` field, or a raw JSONL body (`?name=` sets the label) |
| GET | `/api/runs/{id}` | metadata + `records` (CycleRecord dicts) |
| POST | `/api/live` | body `{cycles, seed, use_llm=false, driver: "auto"\|"scripted"}` → `{id, stream}` |
| GET | `/api/live/{id}/stream` | Server-Sent Events: `cycle` per record, then `done` (or `error`) |

The stream replays from the start for late subscribers and honours
`Last-Event-ID` on reconnect. Tests: `python3 -m pytest tests/test_web.py -q`.
