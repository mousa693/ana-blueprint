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
**journey‑scaled convergence** in `[0,1]`:

- **Omega (expansion)** = the environment / generative novelty pressure ("Myself").
- **Alpha (compression)** = the identity integrating experience ("Me").
- Convergence is a slow integrator seeded at **0** (fully divergent). It eases
  toward the mutual‑prediction target as Me and Myself learn each other, and a
  **surprise shock** from a turbulent scene transiently re‑opens the gap — exactly
  the blueprint's "polarity convergence that can be perturbed and must be
  re‑earned."

Convergence then drives the Environment Mirror's reflectiveness dial and the
Hero's‑Journey stage selection, so the inner dynamic literally shapes the outer
world and the arc of the story.

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
