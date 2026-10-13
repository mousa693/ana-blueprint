# Safety posture

ANA, as built here, is a **self‑contained inner simulation**. It is important to
be precise about what it is and is not, because the name has been used for a
different idea elsewhere.

## What this system does

- Maintains a model of a *self* (traits, beliefs, goals, memories) and runs a
  recursive narrative loop that reflects that self back as a symbolic world, for
  reflection, learning, and growth — the blueprint's stated purpose.
- Runs entirely within its own process and its own local state (an optional
  SQLite shard store under `./ana_run/`).

## What this system does NOT do

- It does **not** read, send, or act on anyone's real messages, email, calendar,
  accounts, devices, or contacts.
- It does **not** impersonate a real person to third parties.
- It has **no actuators** that affect the outside world. The only optional
  external call is to a language model, through one replaceable gateway, to help
  *write the fiction* — and the whole system runs without it.

If a future version is ever asked to act on a real person's own digital life, that
is a different system with a different threat model (consent of the person,
disclosure to the third parties it would contact, platform rules, and a hard
separation between "advise" and "act"). None of that is in scope here, and nothing
in this codebase reaches outside the simulation.

## Safety that *is* in the design

Because ANA deliberately explores personal, symbolic, sometimes dark material, the
blueprint's **Self‑Alignment Protocols** and **World‑Truth Harmonizer** are
implemented as real guards, not flavor text:

- **Forbidden themes.** `ANAConfig.forbidden_themes` are kept out of everything
  the protagonist sees, in three layers: generation works from a "safe view" of
  the self with forbidden themes (and their lexicon synonyms) removed from
  themes, goals, memories, motifs and the cast; the generator, cast and
  harmonizer never voice them; and a final gate scans every text field of the
  finished scene (title, summary, beats, speakers, symbols, choices). A scene
  that still trips the gate is regenerated, or replaced with a safe interlude,
  and the record says so. Verified: 0 leaks over 200 cycles with each of the
  self's active themes forbidden in turn.
- **Stress band / dynamic difficulty.** Intensity is pulled down when modelled
  stress approaches the ceiling and raised out of boredom — keeping the experience
  in a constructive zone.
- **Hope after dark.** After an intense sequence the alignment layer caps the next
  intensity and nudges toward hope.
- **Reality testing.** The harmonizer flags surreal content as a *vision* and
  attaches grounding notes, so symbolic events are not mistaken for literal truth.
- **Continuity.** The harmonizer prevents incoherent contradictions (e.g. a dead
  character silently reappearing).

These are configurable and inspectable, and every decision is recorded in the
per‑cycle `CycleRecord`.

## Model output and the viewer

- Text produced by the model is bounded before it can enter the self: trait
  updates only on known traits, themes only from the theme lexicon, clipped
  strings. Earlier story text is fenced as data in later prompts.
- The web viewer binds to localhost, rejects foreign Host/Origin headers,
  requires JSON/ndjson content types, budgets memory and upload size, and keeps
  model use off unless started with `--allow-llm`. All page text is rendered as
  text nodes under a strict Content-Security-Policy.
