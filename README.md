# ANA — Adaptive Neural-Simulation Architecture

This repository contains the technical system design for ANA.

ANA is an experiential AI architecture that models how identity and environment continuously shape one another through a recursive feedback loop. Instead of treating the world as neutral and the user as a passive observer, ANA treats identity as the primary generative input and the environment as its reflective output.

The document in this repository describes the full system architecture, including:

- Identity Core and Environment Mirror
- Recursive feedback and alignment protocols
- Memory shards and future-state backcasting
- NPC autonomy and entropy management
- World-truth harmonization and realism constraints

This is a design blueprint, not an implementation.

## Document

- **ANA_System_Design_v1.pdf**

Readers coming from the conceptual essay can start here.

## ANA v2: The Twin

`docs/TWIN_PLAN.md` is the plan for v2: a digital twin with its own agency, living in a world behind any screen, influenced by its creator rather than controlled.

### Genesis (seeding the twin)

The first working piece. It gets your life on record and distills it into the twin's starting self.

```bash
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=...

python -m ana.genesis battery     # 120 quick choices; no API calls
python -m ana.genesis interview   # biographer interview, ~2-4 hours across sessions
python -m ana.genesis distill     # → genesis_data/seed_self.json (add --corpus DIR for your own writing)
python -m ana.genesis fidelity    # does the seed answer 24 held-out items like you?
python -m ana.genesis status
```

Everything is resumable. Your answers stay in `genesis_data/`, which is git-ignored and owner-only.

### The Life Loop (the twin, living)

The twin's mind (drives, feelings, relationships, goals it sets itself, memory, temperament) living in a small coastal town with four neighbours, weather, money and bad luck. Runs offline; no API key needed.

```bash
python -m ana.life run --days 14                                  # a fictional demo twin, Rui
python -m ana.life run --seed genesis_data/seed_self.json         # yours, once Genesis is done
python -m ana.life run --influence my_nudges.json                 # message, gift, shape its world
python -m ana.life run --narrator claude                          # journal and letters in its own voice
python -m ana.life run --resume life_data/run --days 7            # keep living the same life
python -m ana.life experiment                                     # does influence work without controlling?
```

Each run writes `life_data/run/`: `summary.md` (agency metrics and the Phase 1 exit criteria), one page per day with its journal and every choice it made and why, its letters to you, and the full decision and world event logs.

Layout: `ana/mind` (the twin), `ana/world` (the Habitat and its rules), `ana/gateway.py` (your only way in), `ana/life` (the loop, narration, reports), `ana/memory` (MCMG), `ana/temperament` (Pi-Phi and the explore/consolidate regulator).

## Status

Version: v1.0  
Scope: Architecture and system design  
Implementation: v2 Phase 0 (Genesis) built; Phase 1 Life Loop first version built

This document will evolve.
