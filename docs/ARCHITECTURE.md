# ANA architecture — blueprint → code

This maps every component of Mohamed Mousa's ANA system design to its
implementation here, and explains the key integration decisions.

## Core components

| Blueprint component | Module | Notes |
|---|---|---|
| **Identity Core ("Me")** | `ana/identity/core.py` | Traits, beliefs, goals, emotional register; emits an `IdentitySnapshot`; folds each `Outcome` back in (learning). Owns the memory. |
| **Environment Mirror ("Myself")** | `ana/environment/mirror.py` | World state + the **reflectiveness dial** + the archetypal mapping library (theme → motifs). The dial opens as convergence rises, so the world mirrors the self more directly over time. |
| **Feedback Loop Engine** | `ana/engine/feedback_loop.py` | The heartbeat. Implements the blueprint's cycle pseudocode, orchestrating every other module through the `interfaces.py` Protocols. |
| **Generative Experience Engine** | `ana/generative/engine.py` | The dream‑maker. Instantiates a Hero's‑Journey stage with world motifs, NPC dialogue, synchronicity, and shard callbacks. Deterministic, with an optional Claude path. |
| **NPC / Entropy Layer** | `ana/npc/layer.py` | Autonomous NPCs (some personify identity elements — Shadow, Mentor), plus context‑aware random events. The "not‑self" that pushes back. |

## Cross‑cutting subsystems

| Blueprint subsystem | Module | Notes |
|---|---|---|
| **Memory Shard System** (episodic + future memory) | `ana/identity/memory.py` over `ana/cores/mcmg.py` | A synchronous facade over the author's MCMG graph. Maps ANA concepts to MCMG origin tiers: Identity→IDENTITY, lesson→PRIME, experience→OMEGA. "Memories of the future" are stored with a `horizon:future` tag and recalled for backcasting. |
| **Synchronicity Engine** | `ana/subsystems/synchronicity.py` | Trigger detection → cooldown/probability gate → symbol matching. Produces a `SynchronicityHint` the generative engine weaves in "thematically but not too obviously." |
| **Self‑Alignment Protocols** | `ana/subsystems/alignment.py` | Reflection %, adaptation level, stress band, dynamic difficulty (zone of proximal development), safety gates (forbidden themes, hope‑after‑dark), and nudges that steer the next cycle. |
| **World‑Truth Harmonizer** | `ana/subsystems/harmonizer.py` | Continuity (dead NPCs can't silently reappear), symbolism‑vs‑literal calibration (flags visions, adds grounding notes), reality anchoring, moderation. |

## The π‑φ engine as the polarity dynamics

The design's central claim is that inner self and outer world **converge** over a
journey. The author's `pi_phi` engine already models a reciprocal system that
converges: Omega expands, Alpha compresses, each keeps a predictive model of the
other, and convergence is reached when those models stop being surprised.

`ana/engine/convergence.py` steps that engine once per ANA cycle and reads a
**journey-scaled, earned convergence** in `[0,1]`:

- **Omega (expansion)** = the environment / generative novelty pressure ("Myself").
- **Alpha (compression)** = the identity integrating experience ("Me").
- Each cycle the Self-Alignment Protocols compute an `AlignmentSignal` from what
  actually happened: reflection `r` (did the world carry the self's themes),
  engagement `e` (+1 met, -1 avoided), integration `i` (a lesson learned / a
  trait grew), external surprise `x` (vision, entropy, intensity).
- The signal is **coupled into the pi-phi state**: mismatch `(1-r)` and avoidance
  add noise to what each core observes of the other, so their mutual prediction
  `m` falls when self and world disagree.
- Earned evidence accumulates: recent merit `M ← M + λ(μ − M)` with
  `μ = 0.30r + 0.35e + 0.35i`, and a slow integrated-growth store
  `I ← I + κ·i·(1−I) − δ·max(0,−e)·I`.
- Target `T = m^γ · clip(0.45M + 0.55I)`; convergence eases toward it
  (faster down than up), avoidance pulls it back, shocks dip it.

So an engaged, learning self converges (≈0.5–0.7 in 16 cycles), an avoidant one
does not (≈0), a shock dips and recovers. Convergence drives the Environment
Mirror's reflectiveness dial; the **journey arc** (below) drives the story stage.
Phases: divergent → awakening → engaging → integrating → converged.

## Journey arc, cast, and debrief (`ana/engine/arc.py`, `ana/npc/layer.py`)

- `JourneyArc` tracks the Hero's-journey stage of every cycle and proposes the
  next one: it moves forward when the self is ready (or after dwelling three
  cycles), steps back at most once after a real slide, and never unwinds the
  return. It records milestones and decides when the journey has come home.
- The stage drives the world's place, the generated scene, and the cast. The
  `NPCDirector` keeps a persistent registry whose dispositions drift with how
  the protagonist treats each character, and stages callbacks: the mentor
  returns and the shadow must be faced at the ordeal, an early ally helps at the
  reward, a villager who fled asks to be trusted again, someone witnesses the
  return.
- `build_debrief(records, ...)` writes the reflection the blueprint calls for
  under *User Control and Transparency*: chapters, turning points, lessons, how
  the self changed, who walked with it, recurring symbols, open questions, a
  closing image. Deterministic, or Claude-written via a strict schema.

## Self-remembering (`ana/identity/core.py`, `ana/identity/journal.py`)

- Goals carry themes from a lexicon, so "become someone who can lead" advances
  when leadership is met (weighted by success, intensity, lessons, horizon) and
  never advances on avoidance.
- The `Journal` is the blueprint's *Meta-Analysis*: per-theme met/avoided
  records, trends ("consistently improving on courage but still struggling with
  purpose"), recurring symbols, symbols that have gone quiet (callbacks), and the
  most-avoided theme (offered as the grand test). Insights travel in the
  `IdentitySnapshot` and steer generation.
- Memories of the future are reinforced each time the self advances toward them;
  at 60% they are marked realised and a Prime shard "I became …" is linked to
  the original vision in MCMG.

## The Claude path (`ana/llm/`)

One replaceable gateway; nothing else imports the provider. Requests use Claude
Opus 5.5 with structured outputs (strict JSON schemas for scenes, outcomes,
actions, debriefs), explicit effort, thinking left at its always-on default,
server-side refusal fallbacks (`fallbacks: "default"`), and `stop_reason`
checked before content. Any refusal, error, or truncation degrades to the
deterministic path; every call is counted. `tests/test_llm.py` drives the real
SDK through a mocked transport; `ana-run llm-check` verifies it live.

## Evaluation (`ana/eval/`)

A rubric over many seeded journeys: earned convergence (engaged > avoidant;
correlation of success with convergence change), journey progression,
repetition, nudge variety, theme coverage, and safety. Baseline 2/7 → 7/7.

## Data flow (one cycle)

```
IdentityCore.snapshot() ─► GenerationRequest ─► ExperienceEngine.generate()
        ▲                        │                        │
        │                        ▼                        ▼
        │               SynchronicityHint        NPCDirector.inject_entropy()
        │                                                 │
        │                                                 ▼
        │                                   TruthHarmonizer.review()
        │                                                 │
        │                                                 ▼
        │                                   SelfAlignment.evaluate()
        │                                                 │
        │                          Driver.act() ◄─────────┘
        │                                │
        │                                ▼
        │                     Interpreter.interpret() ─► Outcome
        │                                │
        └──────── apply_outcome ◄────────┤
                                         ▼
                           ConvergenceEngine.step()  (pi-phi)
```

## Design decisions

- **Everything is swappable.** The Feedback Loop Engine depends only on the
  Protocols in `interfaces.py`. The deterministic generative engine and the Claude
  one are interchangeable; so is any subsystem.
- **Offline‑first.** No module hard‑depends on the network. The model gateway is
  the single, replaceable point of external dependency, and it fails soft.
- **Everything is inspectable.** Every cycle is a serialisable `CycleRecord`; the
  self‑model and memory can be dumped; a run can be logged to JSONL and replayed.
- **Safety is structural, not cosmetic.** Forbidden themes and the stress band are
  enforced by the alignment layer *before* a scene reaches the protagonist, and a
  blocked scene is regenerated gently or replaced with a safe interlude.
