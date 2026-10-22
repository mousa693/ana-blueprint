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

## Status

Version: v1.0  
Scope: Architecture and system design  
Implementation: v2 Genesis (Phase 0) in progress

This document will evolve.
