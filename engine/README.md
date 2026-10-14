# ANA — Adaptive Neural-Simulation Architecture

A working implementation of **Mohamed Mousa's ANA blueprint**: an experiential
simulation built as a recursive feedback loop between an **Identity Core ("Me")**
and an **Environment Mirror ("Myself")**. The self projects a world; the world
reflects the self; the self meets its own reflection, is changed, and projects
again. Over a journey the inner/outer polarity *converges*.

This repository turns the 28‑page design document into a living loop. It is built
**on the blueprint's own two cores**, vendored unchanged:

- **`pi_phi_adaptive_engine.py`** → the mathematics of the feedback loop. Omega
  (expansion) is the world generating novelty; Alpha (compression) is the identity
  integrating it; their reciprocal prediction *convergence* is the blueprint's
  inner–outer polarity convergence.
- **`mcmg_ana v3.py`** → the Memory Shard System, verbatim: shards with
  Omega/Alpha/Prime/Identity decay tiers, tension‑vs‑conflict detection, and
  semantic recall, including "memories of the future."

Everything else — the generative dream‑maker, the NPC/entropy layer, and the
three cross‑cutting subsystems — is built around those cores against a single set
of typed contracts.

## The product

ANA ships as a public, multi-user web app: sign up, a gentle onboarding
conversation seeds your self, then you live journeys turn by turn in the
browser — choosing or writing what you do — until you come home to a debrief.
Your self persists across journeys. Every free-text input is safety-screened
(deterministic floor + Claude second opinion when configured); crisis pauses
the journey and shows support resources. People can export or delete
everything.

```bash
cp .env.example .env          # set ANA_SECRET_KEY (and optionally ANTHROPIC_API_KEY)
docker compose up -d          # http://127.0.0.1:8000
```

See `docs/DEPLOY.md` (Fly.io, Render, any VPS + Caddy/TLS, Postgres option,
budgets, backups) and `docs/PRODUCT_SPEC.md` (API and architecture).

## It runs with no API key

The whole simulation runs **fully offline and deterministically**. A Claude model
(Opus 5.5) is an *optional* enrichment behind a replaceable gateway: if
`ANTHROPIC_API_KEY` is set and `use_llm=True`, the generative engine, the
interpreter, and the autonomous driver can use it; otherwise each falls back to a
transparent deterministic path. Nothing else in the system imports the provider.

```bash
pip install -e .            # or: pip install -e ".[llm,dev]"
ana-run doctor              # environment check
ana-run journey --seed 4    # a whole journey: lives until it comes home, then a debrief
ana-run demo                # the deterministic "lighthouse" journey from the PDF
ana-run auto --cycles 12    # an autonomous journey: the self acts for itself
ana-run play                # interactive: you are the protagonist
python -m ana.web           # the journey viewer at http://127.0.0.1:8765
python -m ana.eval          # the evaluation rubric (docs/EVAL.md)
```

With credentials (`ANTHROPIC_API_KEY`, or `ant auth login`), add `--use-llm` to let
Claude Opus 5.5 dream the scenes, interpret what they meant, roleplay the
protagonist, and write the debrief. `ana-run llm-check` is a one-command live
check of that path. `--log run.jsonl` records every cycle for the viewer.

## The loop

Each heartbeat (`ana/engine/feedback_loop.py`) runs the blueprint's cycle:

```
read identity snapshot ("Me")
  → advance the journey's place; mirror the self into the world ("Myself"),
    scaled by the reflectiveness dial (which opens as convergence rises)
  → consider a synchronicity (meaningful coincidence, with cooldown)
  → recall past shards + "memories of the future" (backcasting)
  → GENERATE the scene (dream-maker) → inject entropy (NPC layer)
  → HARMONIZE (continuity, reality-anchoring, vision-flagging)
  → ALIGN (safety band, dynamic difficulty, nudges) — regenerate if a line is crossed
  → the protagonist ACTS (human / scripted / autonomous); NPCs react
  → INTERPRET the outcome → step the pi-phi convergence dynamics
  → fold the outcome back into the self; carry alignment's guidance forward
```

Every cycle is captured as an inspectable `CycleRecord` (identity before, request,
scene, alignment report, harmonizer verdict, outcome, convergence, surprises).

## What "alive" means here

The loop is a single persistent process carrying one continuous, evolving model
of a self. It acts on its own clock, changes itself from what happens, and
remembers itself: a journal notices trends ("growing in leadership, 4 of 4 met;
still turning from purpose"), goals advance by meaning, and "memories of the
future" are reinforced as the self grows toward them and marked fulfilled when it
gets there.

**Convergence is earned.** Each cycle the Self-Alignment Protocols turn what
actually happened — was the theme reflected, did the self meet or turn from the
challenge, was a lesson integrated, was there a shock — into a signal coupled
into the pi-phi cores. An engaged self converges; an avoidant one doesn't; a
shock dips it and it recovers. Across the evaluation suite the correlation
between meeting a challenge and convergence rising is 0.68 (it was 0.09 when
convergence was time-driven).

**The journey has a shape.** A journey arc walks the Hero's Journey in order —
Call, Threshold, Mentor, Ordeal, Reward, Return — the place follows the story,
the cast persists and calls back (the mentor returns at the ordeal, the shadow
must be faced, an early ally helps at the reward, someone witnesses the
homecoming), and when the self has come home the run ends with a **debrief**: a
warm, specific reflection on the chapters, turning points, lessons, how the self
changed, who walked with it, and questions to carry home. See
`examples/sample_debrief.md`.

**It is measured.** `python -m ana.eval` runs engaged, avoidant, mixed,
autonomous and scripted selves across seeds and scores the journeys against a
rubric (earned convergence, journey progression, variety, theme coverage,
safety). Current: 7/7 — see `docs/EVAL.md`, and `docs/EVAL_BASELINE.md` for where
it started (2/7).

## Layout

```
ana/
  cores/            vendored blueprint cores (pi_phi, mcmg) — unchanged
  contracts.py      the typed objects every module shares
  interfaces.py     Protocols: each module is swappable
  config.py         runtime configuration
  llm/gateway.py    replaceable, optional model bridge (Claude Opus 5.5)
  identity/         Identity Core ("Me"), journal / meta-analysis,
                    Memory Shard System (over MCMG)
  environment/      Environment Mirror ("Myself") + reflectiveness dial
  engine/           convergence (pi-phi, earned), arc + debrief, interpreter,
                    drivers, feedback loop
  generative/       Generative Experience Engine (the dream-maker)
  npc/              NPC / Entropy Layer
  subsystems/       Synchronicity · Self-Alignment · World-Truth Harmonizer
  observers.py      console + JSONL cycle observers
  session.py        assembles a full session from config
  cli.py            journey / demo / auto / play / doctor / llm-check
  web/              journey viewer (FastAPI + vanilla JS, live SSE runs)
  eval/             evaluation harness + rubric
tests/              hermetic, offline test suite (incl. the Claude path via a
                    mocked transport)
docs/ARCHITECTURE.md   blueprint section → module map
```

See `docs/ARCHITECTURE.md` for how every component of the design document maps to
code, and `docs/SAFETY.md` for the safety posture (this is a self‑contained inner
simulation — it does not touch anyone's real accounts, devices, or contacts).

## Credits

Architecture and the `pi_phi` / `mcmg` cores by **Mohamed Mousa** (MIT). This
engine assembles them, adds the generative and subsystem layers, and wires the
whole into the living loop the blueprint describes.
