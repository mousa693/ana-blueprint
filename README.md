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

## It runs with no API key

The whole simulation runs **fully offline and deterministically**. A Claude model
(Opus 5.5) is an *optional* enrichment behind a replaceable gateway: if
`ANTHROPIC_API_KEY` is set and `use_llm=True`, the generative engine, the
interpreter, and the autonomous driver can use it; otherwise each falls back to a
transparent deterministic path. Nothing else in the system imports the provider.

```bash
pip install -e .          # or: pip install -e ".[llm,dev]"
ana-run doctor            # environment check
ana-run demo              # the deterministic "lighthouse" journey from the PDF
ana-run auto --cycles 12  # an autonomous journey: the self acts for itself
ana-run play              # interactive: you are the protagonist
```

Add `--use-llm` to `auto` (with credentials) to let Claude dream the scenes and
roleplay the protagonist. Add `--log run.jsonl` to record every cycle for replay.

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

The loop is a single persistent process that carries one continuous, evolving
model of a self forward across cycles, acts on its own clock via the autonomous
driver, and changes itself from what happens. Run `ana-run auto` and watch the
Hero's‑Journey stage advance (Call → Threshold → Mentor → Ordeal → Reward →
Return), the world move, traits shift, lessons form, and convergence climb from
near‑zero toward one.

## Layout

```
ana/
  cores/            vendored blueprint cores (pi_phi, mcmg) — unchanged
  contracts.py      the typed objects every module shares
  interfaces.py     Protocols: each module is swappable
  config.py         runtime configuration
  llm/gateway.py    replaceable, optional model bridge (Claude Opus 5.5)
  identity/         Identity Core ("Me") + Memory Shard System (over MCMG)
  environment/      Environment Mirror ("Myself") + reflectiveness dial
  engine/           convergence (pi-phi), interpreter, drivers, feedback loop
  generative/       Generative Experience Engine (the dream-maker)
  npc/              NPC / Entropy Layer
  subsystems/       Synchronicity · Self-Alignment · World-Truth Harmonizer
  observers.py      console + JSONL cycle observers
  session.py        assembles a full session from config
  cli.py            demo / auto / play / doctor
tests/              hermetic, offline test suite
docs/ARCHITECTURE.md   blueprint section → module map
```

See `docs/ARCHITECTURE.md` for how every component of the design document maps to
code, and `docs/SAFETY.md` for the safety posture (this is a self‑contained inner
simulation — it does not touch anyone's real accounts, devices, or contacts).

## Credits

Architecture and the `pi_phi` / `mcmg` cores by **Mohamed Mousa** (MIT). This
engine assembles them, adds the generative and subsystem layers, and wires the
whole into the living loop the blueprint describes.
