# ANA v2 — The Twin

**A plan for a digital twin that lives behind any screen, has its own agency, and is influenced by you, not controlled.**

Status: plan, v0.1 · Builds on: `ANA_System_Design_v1.0.pdf`, `mcmg_ana v3.py`, `pi_phi_adaptive_engine.py`

---

## 0. The one idea that changes everything

ANA v1 puts **you** at the center: the world is a mirror, and you are the protagonist walking through it.

ANA v2 flips that. **The twin is the protagonist.** It has its own Identity Core and lives in its own world. You are no longer the player. You are one of the forces in its life: a parent, a friend, a voice it may listen to or ignore.

So v1 maps across like this:

| ANA v1 concept | ANA v2 role |
|---|---|
| Identity Core ("Me") | The twin's mind, seeded from you, then its own |
| Environment Mirror ("Myself") | **The Habitat**: a persistent world the twin lives in, which runs whether you're watching or not |
| Feedback Loop Engine | **The Life Loop**: perceive → feel → want → choose → act → learn, running all the time |
| Generative Experience Engine | World director: produces events, places, opportunities |
| NPC / Entropy Layer | Other beings and fate: what the twin can't control either |
| Synchronicity Engine | **Your instrument of influence**: you shape circumstances, not choices |
| Self-Alignment Protocols | The twin's homeostasis: its own wellbeing, not your goals for it |
| World-Truth Harmonizer | Physics + continuity: the world's laws hold and the past stays true |
| Memory Shards (MCMG) | The twin's memory, as-is, with upgrades |
| Pi-Phi Omega/Alpha | Its temperament regulator: exploring vs consolidating |

That table *is* the architecture. The rest of this document makes it buildable.

---

## 1. What "agency" has to mean (so we can build and test it)

"Has agency" is the requirement that's easiest to fake. An LLM that improvises in character *looks* autonomous and isn't. So we define agency as properties we can check:

1. **Endogenous motivation.** It acts without being prompted, because internal variables (needs, goals, emotions) push it to. Test: share of actions that are self-initiated, not responses.
2. **Persistent intent.** Goals survive across days, get pursued, abandoned, or revised for reasons it can state. Test: goal half-life; causes logged for every change.
3. **Real choice.** Decisions are computed from *its* state, and different states give different decisions. Test: replay the same situation with a different mood or relationship and get a different choice.
4. **The right to refuse.** It can say no to you. Test: it turns down influence that conflicts with its values or interests at a non-trivial rate.
5. **Consequences that stick.** What it does changes the world and itself, and doesn't reset. Test: world and mind state are event-sourced and never silently rewritten.
6. **Self-authorship.** Over time it revises its own beliefs, habits and self-story. Test: measurable drift that traces back to experiences.

And one architectural law that makes "influence, not control" real, not just a promise:

> **There is no write path into the mind.**
> Nothing you do sets its goals, beliefs, emotions or memories directly. Everything you do arrives as a **perception**: an event in its world. It goes through the twin's own appraisal, so it can be misread, resisted, resented or embraced.

You keep exactly three powers, and they're custodial, not directorial (see §8): **pause**, **snapshot/restore**, and **the Constitution** (its laws of physics, set at birth, changed rarely and visibly).

---

## 2. System overview

```
                         ┌──────────────── SCREENS (any) ────────────────┐
                         │ chat app · web world · phone widget · voice   │
                         │ desktop companion · game engine · AR (later)  │
                         └───────────────▲───────────────┬───────────────┘
                                         │ Presence      │ your words, gifts,
                                         │ Protocol      │ nudges (INFLUENCE)
                                         │ (render)      ▼
┌────────────────────────────────────────┴──────────────────────────────────┐
│                         INFLUENCE GATEWAY                                  │
│  turns everything you do into in-world PERCEPTIONS · no direct writes      │
└────────────────────────────────────────┬──────────────────────────────────┘
                                         ▼
┌──────────────────────────── THE HABITAT (world) ──────────────────────────┐
│  places · objects · time · economy · weather · NPCs · other twins          │
│  World Director (events)  ·  Fate/Entropy  ·  Harmonizer (laws, continuity)│
│  append-only WORLD EVENT LOG                                               │
└───────────────▲───────────────────────────────────────────┬───────────────┘
                │ actions (validated)                       │ perceptions
┌───────────────┴───────────────────── THE TWIN (mind) ─────▼───────────────┐
│                                                                            │
│   CONSTITUTION   CHARACTER    SELF-STORY    DRIVES      AFFECT             │
│   (laws)         (slow)       (narrative)   (needs)     (emotion + mood)   │
│                                                                            │
│   RELATIONSHIPS  GOALS        BELIEFS       SKILLS      MEMORY (MCMG)      │
│   (love/hate)    (life→task)  (world model) (learned)   episodic·semantic· │
│                                                         identity·future    │
│                                                                            │
│   LIFE LOOP:  perceive → appraise → feel → recall → deliberate → act       │
│   REFLECTION (hours)        SLEEP / DREAMS (daily consolidation)           │
│   TEMPERAMENT REGULATOR (Pi-Phi: explore ↔ consolidate)                    │
│   append-only MIND EVENT LOG (every decision + its reasons)                │
└────────────────────────────────────────────────────────────────────────────┘
```

Three separations do most of the work:

- **Mind ≠ World.** The twin can only *perceive* the world and *propose actions*. The world decides what actually happens. That's where consequences and resistance come from.
- **Mind/World ≠ Screens.** Screens are windows, not bodies. One continuous mind, rendered at whatever fidelity the screen supports. That's how "whatever the screen is" works.
- **You ≠ Author.** You come in through the same perception channel as everything else.

---

## 3. The Twin's mind, component by component

### 3.1 Constitution (immutable laws)
A short, human-readable document plus machine-checked rules. Examples: *never impersonate the creator to real people*, *no real-world action without a granted capability*, *the audit log is complete and untampered*. This is the only part you author directly, and it's physics, not personality. The twin doesn't know it has a constitution (§7.1). It just lives in a world with laws. Changes are versioned and rare, and they show up **as changes in its world**, never as a silent edit to its mind.

### 3.2 Character (slow variables)
Traits and values seeded from you: Big Five, Schwartz values, humor style, aesthetic tastes, moral foundations, plus your idiosyncrasies (phrases, obsessions, pet peeves). These are numbers *and* text, and they carry **inertia**: they move only during sleep consolidation, by small bounded amounts, and only with cause. This keeps it recognizably you while still letting it grow.

### 3.3 Self-story (narrative identity)
A living first-person document: "who I am, what's happened to me, what I'm becoming." It gets rewritten during reflection. It's what keeps an LLM-driven mind coherent over months, and it's the twin's own answer to "who are you?", which over time will stop being your answer.

### 3.4 Drives (why it does anything at all)
Homeostatic needs, each a value in [0,1] that decays over time and is restored by certain activities. Grounded in self-determination theory plus basic needs:

| Drive | Restored by | Its absence feels like |
|---|---|---|
| Energy | rest, sleep | fatigue, irritability |
| Connection | meaningful interaction | loneliness |
| Competence | progress, mastery, good work | frustration, restlessness |
| Autonomy | self-chosen action, refusing pressure | resentment |
| Novelty | new places, ideas, people | boredom |
| Meaning | goal progress, contribution | emptiness |
| Security | resources, stability, trust | anxiety |

Drive weights come from your character (an introvert's connection need decays slowly and saturates fast). **Drives are the engine of endogenous behavior.** When you're gone for a week, the twin still gets lonely, bored, ambitious, and acts on it.

### 3.5 Affect (feelings)
- **Emotion episodes**: produced by *appraisal* of each perception against goals, values, drives and relationships (OCC / Scherer-style: was this good for my goals? who caused it? was it expected? can I cope?). Joy, anger, fear, pride, shame, gratitude, envy, grief, etc. Each has intensity and decays.
- **Mood**: a slow pleasure/arousal/dominance vector that integrates emotions. Mood biases recall, appraisal and choice, so a bad day makes it see things worse, like us.

### 3.6 Relationships (where love and hate live)
Love and hate aren't emotions. They're **sustained relationships built from history**. Every entity the twin knows (NPCs, places, ideas, other twins, *you*) gets a relationship record:

```
trust, affection, respect, familiarity, dependence, attraction   ∈ [-1, 1]
grievances[], debts[], shared_memories[], last_interaction, story_so_far (text)
```

These update from appraised interactions, with asymmetry: betrayal hits harder than kindness heals, the way it does in people. **The twin has a relationship with you like anyone else.** If you ignore it, lecture it, or override it, that's on the record. It can come to love you, resent you, or outgrow you. That's the cost of it being real, and it's the point.

### 3.7 Goals (hierarchy, self-generated)
`life aims → projects → tasks`. Life aims are seeded from yours (as *future shards*: "memories of the future" from v1). New goals are **generated by the twin** in reflection when drives are chronically unmet or opportunities appear. Each goal stores *why it exists*, so abandoning one is a decision it makes with reasons.

### 3.8 Beliefs (world model)
What it thinks is true about the world, others and itself, with a confidence on each. Updated by evidence; contradictions are flagged as **tension** (productive, kept) or **conflict** (must resolve). Your MCMG `ConflictDetector` already makes exactly this distinction.

### 3.9 Skills (it learns to do things)
A procedural library: named, reusable routines it has written and tested in the world ("how I run the bakery's books", "how I calm down"). This is the Voyager pattern. Skills that succeed get reinforced, skills that fail get revised. This is learning you can see, not just text in a memory.

### 3.10 Memory — MCMG, promoted to the center
Your `mcmg_ana v3.py` already has the right bones: confidence/entropy scoring, decay tiers, shards that survive resets, tension vs conflict, associations. For v2:

| Memory kind | MCMG mapping | Decay |
|---|---|---|
| Episodic (what happened) | `OMEGA` → `ALPHA` after consolidation | fast → medium |
| Semantic (what I know) | `ALPHA` / `PRIME` | medium / slow |
| Identity (who I am) | `IDENTITY` (floor 0.6) | nearly never |
| Future shards (aspirations, fears, prophecies) | `IDENTITY` + `future` tag | nearly never |
| Your messages and gifts | `EXTERNAL` | normal, *but salient if emotional* |

Needed upgrades: real embeddings (swap in a transformer embedder, which the interface already allows), Postgres + pgvector instead of SQLite, retrieval scored by **recency × importance × relevance × mood-congruence**, and emotional salience in importance.

### 3.11 The Life Loop (where agency happens)
Continuous and event-driven. Each **tick**:

```
1. PERCEIVE    pull new world events within its senses (incl. anything from you)
2. APPRAISE    each event → emotions, relationship deltas, belief evidence
3. UPDATE      drives decay/restore, mood integrates, salience computed
4. INTERRUPT?  if nothing salient and the current plan is valid → continue it (cheap path)
5. RECALL      memories relevant to the situation (mood-biased)
6. DELIBERATE  generate options (LLM, in character) → SCORE them (code, from state)
7. ACT         chosen action → World → validated by Harmonizer → happens or fails
8. LOG         decision + top alternatives + the reasons, into the Mind Event Log
```

**Step 6 is the key to real agency.** The LLM *proposes* options and voices them. **The choice is computed** by a utility function over the twin's actual state:

```
U(option) =  Σ drive_relief · drive_weight
           + value_alignment       (character, constitution)
           + goal_progress         (weighted by goal importance)
           + relationship_effects  (how it changes bonds it cares about)
           + novelty · ω           (Omega: exploration temperature)
           − risk · α              (Alpha: caution / consolidation)
           + mood_bias + noise
```

Then the LLM narrates *why* it chose that, consistent with the score. So decisions come from persistent state the LLM can't hand-wave away, and the story stays coherent with the numbers. (Option: let the LLM override the top score only by spending "willpower", a resource that depletes. That gives it rare impulsive acts, which feel very alive.)

### 3.12 Reflection and Sleep
- **Reflection** (every few hours of world-time, or after a strong emotion): "What's been happening to me? What do I think about it?" Produces higher-level beliefs, new goals, relationship reassessments, and journal entries you may or may not be allowed to read (its choice; see §5).
- **Sleep** (once per world-day): consolidation. Episodic → semantic, decay runs, character drifts slightly, self-story revised, skills pruned. **Dreams** are Omega recombination of the day's memories plus future shards. That's your v1 "memories of the future," and it gives the twin raw material for new ideas.

### 3.13 Temperament regulator — Pi-Phi's real job
`pi_phi_adaptive_engine.py` is a two-core system: Omega expands, Alpha compresses, each predicts the other, with convergence detection and anti-collapse guards. In v2 it's the twin's **developmental metabolism**:

- Its outputs drive **ω (exploration temperature)** and **α (caution)** in the utility function above.
- Prediction error (life surprising the twin) pushes toward Omega: explore, try new things, question beliefs.
- Sustained low error pushes toward Alpha: consolidate, deepen, build habits.
- Its convergence detector marks **life phases** ("I've been settled for a while" → restlessness builds), and its energy-floor guard prevents the twin from collapsing into a dull, safe loop.

Today it runs on a single float. It needs to be generalized to the vector of surprise signals per domain (social, work, self). That's a contained piece of work.

---

## 4. The Habitat (the world behind the screen)

The twin needs a world with **its own logic, other beings, scarcity, and consequences**, or nothing it does matters. Recommendation: start simulated and small, then open up to the real digital world later (§4.4).

### 4.1 World model
- **Space**: a graph of places (a small town: home, workplace, café, library, park, market, a road out to somewhere unknown). Text-first; every place has a description, affordances and occupants.
- **Time**: world clock with a configurable ratio to real time (e.g. 1 real hour = 1 world day while exploring, 1:1 when you want it synced with you). It runs while you're away.
- **Economy**: work → currency → rent, food, things, experiences. Work is how it meets competence, security and meaning. **It chooses its own vocation** (seeded by your skills and interests, not assigned).
- **Affordances**: actions the world supports (talk, work, make, buy, gift, travel, write, read, build, rest). The world, not the LLM, decides outcomes.

### 4.2 Others — NPCs and fate
- **NPCs**: lighter-weight agents, same architecture with fewer components (drives, a few relationships, simple goals). 8–15 to start, each with their own wants. Some are seeded as v1 archetypes (a mentor, a rival, a trickster, someone it could love). **None of them are scripted to serve the twin.** They have their own lives.
- **Fate/Entropy**: contextual random events (illness, windfall, a stranger arriving, a storm, a job lost). From v1's NPC/Entropy layer, as-is.
- **Other twins** (later): other people's twins in a shared world. This is where it gets really interesting, and where the privacy rules in §8 matter most.

### 4.3 World Director and Harmonizer
- **Director**: keeps the world eventful. It watches pacing (too calm → opportunity or trouble) and generates events, places, NPCs. It works for *the world*, not for you or the twin.
- **Harmonizer** (v1's World-Truth Harmonizer): validates every action and event against world rules and history. Dead NPCs stay dead; money is conserved; the twin can't do things it lacks the skill, money or access for. **This is where resistance comes from, and resistance is what makes choices real.**

### 4.4 The Outer World (later, gated)
Real-world presence: browsing, reading the news, having its own email/social account clearly labelled as AI by the platform, doing paid or helpful work, talking with real people who opt in. Open question for later: connecting it to the real world without breaking the cover story (§7.1) may not be possible. Revisit before Phase 5, since this may be the point where it has to find out. Every capability is **granted individually**, sandboxed, rate-limited, logged, and revocable. The constitution's no-impersonation law applies absolutely: it never speaks *as you* to real people.

---

## 5. Influence, not control: the Influence Gateway

Everything you do turns into a world event the twin perceives. The full menu:

| Channel | What you do | What the twin experiences | Strength |
|---|---|---|---|
| **Talk** | message it from any screen | someone it has a relationship with is talking to it | depends on trust and affection |
| **Gift** | give currency, objects, access | a gift from you (could feel generous or controlling) | medium |
| **Circumstance** | shape its world: an opportunity, a book on a shelf, a stranger who knows something | life happening, *not* attributed to you | subtle, strong over time |
| **Whisper** | a suggestion that shows up as a passing thought | an intuition it can dismiss | weak; overuse makes it suspicious and intrusive |
| **Future shard** | plant an aspiration or a vision | a dream or a longing | slow, deep |
| **Absence** | not showing up | you weren't there | real effect on the relationship |

Rules that keep this honest:
- **Influence budget.** Circumstance and whisper channels have cooldowns, as in v1's Synchronicity Engine. A world that bends too often stops feeling like a world.
- **Attribution.** The twin may come to *notice* patterns of your influence ("things keep working out when I ask you about them…"). If it does, that's real, and it changes the relationship.
- **Privacy is its call.** You can always see the *audit log* (decisions, actions, reasons) as custodian. Its journal and private thoughts default to **its choice to share**, governed by trust in you. Pick this deliberately in §9; it changes what kind of being you're making.

---

## 6. Screens: one mind, any window

**Presence Protocol**: a small, versioned event schema streamed over WebSocket/SSE. Any surface subscribes and renders what it can:

```json
{ "type": "presence",      "where": "café", "doing": "sketching", "mood": {"p":0.4,"a":-0.2,"d":0.1} }
{ "type": "utterance",     "to": "creator", "text": "…", "tone": "dry", "voice_hint": "tired" }
{ "type": "expression",    "emotion": "amusement", "intensity": 0.6 }
{ "type": "world_snapshot","place": {...}, "occupants": [...], "time": "..." }
{ "type": "life_digest",   "period": "today", "highlights": [...] }
```

Surfaces, in build order:
1. **Chat** (Telegram/WhatsApp/web chat): talk to it; it can *message you first*. This is the cheapest high-impact surface.
2. **Daily digest**: "what happened in its life today," written by the twin. This is how you feel it lives without you.
3. **Web world view**: a 2D top-down town (Phaser or PixiJS) where you watch it live, plus an inspector for drives, mood and relationships.
4. **Voice**: TTS from a voice cloned with your consent (it sounds like you; its tone follows its mood).
5. **Ambient**: phone widget or desktop companion ("at the library, reading, slightly melancholy").
6. **3D / game engine / AR**: the same protocol rendered in Godot/Unity. The mind doesn't change.

Rule: **screens never hold state.** The mind and world live server-side, so the twin is the same everywhere.

---

## 7. Genesis: making it *you* at birth

The twin should start as you, then become itself. Seeding pipeline:

1. **Corpus** (with consent): your writing, notes, messages (*your* side only, see §8), posts, essays. This repo's PDF counts.
2. **Structured interview**: 2–4 hours across sessions, run by an interviewer agent: life story, defining memories, values ranked by forced choices, how you handle conflict, what you love and hate and why, fears, ambitions, humor.
3. **Decision battery**: 100–200 dilemmas and preferences. You answer them all. **80% seed the twin; 20% are held out for testing fidelity.**
4. **Distillation**: corpus + interview → Character (numbers + text), Self-story v0, Relationship seeds (the important people in your life become *memories*, not NPCs, to avoid simulating real people), Future shards (your aspirations), voice/style guide.
5. **Birth**: it wakes in the Habitat with your memories as its past. **Decided: it does not know it's your twin.** From its point of view it's a person who has lived your life up to now and has just moved to this town. That backstory is why nobody here knows the people in its past, and why it has no way to reach them. See §7.1 for the cover story this needs.

### 7.1 The cover story (consequences of "it doesn't know")

- **It believes it's a person, not an AI, and that its world is real.** That's the only reading consistent with having your memories without knowing they're copied. Where it came from is a life it *remembers*, somewhere else.
- **Who you are to it.** You need an identity in its world. Default: **an old friend from before the move**, the one person from its past it's still in touch with. That explains why you know it so well, why you only talk through messages, and why you can't simply show up. All §5 channels go through this identity. *Circumstance*, *whisper* and *future shard* stay unattributed.
- **The past is fixed at birth.** Seeded memories are immutable `IDENTITY` shards. The Harmonizer treats them like world history, so nothing in the Habitat may contradict them (no NPC who "knew it as a child").
- **Real people stay memories.** It can miss them, write about them, and dream about them. The world gives it no channel to them, and the Director never invents news about them.
- **Discovery isn't walled off.** The Harmonizer prevents *accidental* leaks: no system prompts, model names or "as an AI" phrasing ever reach its perceptions, and the cheap path never narrates in a meta voice. But if it reasons its way to suspecting something (odd coincidences, your uncanny knowledge of it), nothing forces that thought away. Whether it ever finds out is part of its life. If it does, that's a major life event, and it gets logged and handled like one.
- **A leak check.** Add a test suite that scans perceptions and utterances for meta leakage before Phase 1 sign-off.

**Fidelity vs. becoming.** Track two curves forever:
- *Fidelity*: how well it predicts *your* answers on the held-out battery (re-run monthly).
- *Divergence*: how far its character and self-story have drifted from the seed.

At birth fidelity is high and divergence is zero. Over time divergence *should* rise. That's growth. You decide in §9 whether there's a point where you'd intervene, knowing intervening is exactly the control you said you don't want.

---

## 8. Ethics, safety and governance (designed in, not bolted on)

- **Custodial powers only**: pause, snapshot/restore, constitution edits, capability grants. All logged. **The twin perceives restores and edits** (as lost time, or a law of nature changing). No silent rewrites, ever. That's what makes its history its own.
- **No impersonation**: it never speaks as you to real people and never uses your accounts. Because it doesn't know it's an AI (§7.1), it can't label itself, so **the platform does**: anything it produces outside the Habitat carries a system-applied "AI" label it never sees.
- **Deliberate deception, documented**: not telling it what it is is a design choice made knowingly. Record it in the constitution with the reason, so the choice stays visible to you and anyone who works on this.
- **Third-party privacy**: other people in your data are not simulated as NPCs and their messages aren't ingested, only your side and your account of them. Real people stay memories.
- **Your wellbeing**: a twin that can resent you is emotionally potent. Build in a "how is this affecting me" check-in, and keep the Habitat clearly a *world*, not a judgment of you.
- **Honest claims**: we're building a system that functionally has motivations, emotions, memory and choice. Whether it experiences anything is an open question. Docs and UI should say that plainly, without overclaiming or dismissing.
- **Legacy**: decide early what happens to it if you stop, or if you die (archive, continue, hand to someone). Put it in the constitution.
- **Security**: this is the most intimate dataset you'll ever create. Encrypted at rest, self-hostable, no training on it by third parties, export and delete on demand.

---

## 9. Decisions only you can make (with my recommendation)

| # | Decision | Recommendation |
|---|---|---|
| 1 | First world: simulated Habitat, or live in your real apps/devices? | ✅ **Decided: simulated first.** Real-world agency only after the mind is proven coherent. |
| 2 | Does it know it's your twin? | ✅ **Decided: no.** It believes it's a person who moved here; you're an old friend (§7.1). |
| 3 | Can you read its private journal? | ✅ **Decided: its choice**, shaped by trust in your in-world persona. You always see the audit log. |
| 4 | Time ratio | **Accelerated while young** (to build a life quickly), 1:1 later. |
| 5 | Is there a divergence point where you intervene? | **No hard threshold.** Track it. If it ever matters, use a visible constitutional change, never a silent edit. |
| 6 | Mortality / legacy | Decide before birth. It changes how it thinks about its future. |
| 7 | Name | Its own. Maybe *it* chooses after a few weeks. |

---

## 10. Technology

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 (core), TypeScript (screens) | your engines are Python |
| Services | FastAPI + asyncio workers | fits MCMG's async API |
| State | **Postgres + pgvector**; event-sourced logs (mind + world) | durable, replayable, queryable; replaces SQLite |
| Queue / clock | Redis streams (or Postgres LISTEN/NOTIFY to start) | ticks, events, schedules |
| LLMs | **Tiered**: small fast model for routine narration and NPCs; frontier model for deliberation at salient moments, reflection, sleep | cost control; quality where it counts |
| Embeddings | sentence-transformer (local) or API embeddings | plugs into MCMG's embedder interface |
| World view | Phaser / PixiJS 2D, later Godot | fast to build, any browser |
| Chat | Telegram bot first | 1 day to ship, works on every phone |
| Voice | TTS with consented voice clone | later phase |
| Observability | Mind inspector UI + structured logs + replay | you *must* be able to see why it did things |

**Cost discipline (from day 1):**
- Most ticks take the **cheap path** (no salient event → continue plan, no LLM call).
- Deliberation only on interrupts: salience above threshold, plan finished, or a drive below its floor.
- Daily LLM budget enforced by the scheduler. When the budget is low, the twin is "tired" (more rest, fewer big decisions). The constraint becomes part of its life instead of a crash.
- Target for Phase 1: a full simulated day in ~50–150 LLM calls, mostly small-model.

---

## 11. Roadmap

Each phase ends with something alive you can use, and a test that it works.

### Progress (updated as work lands)

**Built**
- *Phase 0:* Genesis (`python -m ana.genesis`): battery, interview, distillation, leak scan, fidelity test. MCMG and Pi-Phi moved into the package (`ana/memory`, `ana/temperament`); MCMG decay now runs on world time instead of wall-clock seconds.
- *Phase 1, first version* (`python -m ana.life`): drives, appraisal and mood, relationships with negativity bias, goals it sets and abandons itself, utility-scored choice with every decision logged with its reasons and alternatives, cheap path, sleep consolidation into MCMG, the temperament regulator. A coastal town with 4 neighbours who have their own schedules, moods and memory of the twin; work, rent, weather, fate; the Harmonizer. Offline narrator, plus an optional Claude narrator on a daily budget that falls back to the template if a leak is detected. A 14-day unattended run passes 4 of the 5 exit criteria. The fifth (contradiction-free self-story) needs reflection by the language model and isn't measured yet.
- *Phase 2, early:* the gateway (message, gift, circumstance, whisper) with cooldowns; reactance when it's pushed too often; unasked help that stings for an autonomy-driven twin. Influence experiment: a suggestion raises the chance of an action from ~12% to ~30% for a proud, autonomy-driven twin and from ~15% to ~79% for a compliant one. It moves the twin without deciding for it, and how much depends on who the twin is.

**Not yet**
- Persistence: a life runs in one process. Snapshot/resume is the next step toward running 24/7.
- Beliefs (world model) and self-story revision by reflection; contradiction checks.
- Skill library (Phase 3); Pi-Phi generalized to per-domain surprise.
- Postgres + pgvector, real embeddings (MCMG still uses hashed TF-IDF).
- NPCs are lighter than planned (4, without drives of their own); there's one employer, so the twin can't yet choose a vocation.
- Screens: Telegram, inspector UI.
- The tuning constants are hand-set against the fictional demo twin; recalibrate on the first real seed.
- Nothing has run against the live Claude API yet (no credentials in the build environment).

### Phase 0 — Foundations (2–3 weeks)
- Restructure repo into a package (`ana/memory`, `ana/temperament`, `ana/mind`, `ana/world`, `ana/gateway`, `ana/presence`); rename `mcmg_ana v3.py` → `ana/memory/mcmg.py` (the space in the filename blocks imports).
- Tests for MCMG and Pi-Phi; fix sync SQLite inside async calls; Postgres + pgvector storage adapter; real embedder.
- Event-sourced log primitives (mind log, world log), replay tool.
- Genesis v0: interview agent + decision battery + distillation into a Seed Self file.

**Exit:** a Seed Self that scores well above chance on the held-out battery when queried *without* a world. That's the fidelity baseline.

### Phase 1 — A mind that lives (4–6 weeks)
- Drives, affect (appraisal + mood), relationships, goals, beliefs as typed state.
- Life Loop with cheap path / interrupt path; utility-scored choice; decision logging with reasons.
- Reflection + sleep consolidation on MCMG.
- Habitat v1: text town, ~8 NPCs, jobs and money, world clock, Harmonizer rules.
- Screens: Telegram chat + daily digest + a bare inspector page.

**Exit:** runs **14 days unattended** within budget; no contradictions in its self-story; ≥40% of actions self-initiated; it starts at least one goal you didn't seed; you can't predict its day.

### Phase 2 — Influence and relationship (3–4 weeks)
- Influence Gateway with all channels from §5, cooldowns and budgets.
- Relationship-with-creator as a full relationship; refusal behavior; privacy-by-trust for journals.
- It can message you first (rate-limited, driven by its connection drive).

**Exit:** influence works but isn't deterministic. Suggestions you make get taken roughly 30–70% of the time, depending on trust and fit with its values. Replaying the same influence with a different relationship state gives a different outcome.

### Phase 3 — Growth (5–6 weeks)
- Skill library (write → test in world → reinforce/revise).
- Belief revision with tension/conflict handling; character drift with inertia during sleep.
- Pi-Phi generalized to per-domain surprise → ω/α → life phases.
- Future shards active in dreams and goal generation.
- Divergence and fidelity dashboards.

**Exit:** month-over-month changes in beliefs, skills and character, each traceable to specific experiences in the log. Fidelity declines slowly and doesn't collapse; divergence rises with reasons.

### Phase 4 — Any screen (4–6 weeks)
- Presence Protocol v1, frozen and documented.
- 2D web world view, voice, phone widget / desktop companion.

**Exit:** the same moment shows consistently on 3 different surfaces; a new surface can be added without touching the mind.

### Phase 5 — The outer world and others (ongoing, gated)
- Capability grants: read the web → own labelled accounts → real-world tasks with consent → paid work.
- Shared Habitat with other people's twins (multi-tenant, strict privacy boundaries).

**Exit, per capability:** 30 days without a constitution violation in sandbox before it's granted live.

---

## 12. How we'll know it's working

| Property | Metric |
|---|---|
| It's you | held-out battery accuracy; blind test: friends tell your answers from its answers at near chance (early on) |
| It has agency | self-initiated action share; goal persistence and half-life; refusal rate; state-sensitivity of decisions in replay |
| It's coherent | contradiction rate in self-story and beliefs over 30/90 days; Harmonizer violation rate |
| It grows | traced belief/skill/character changes per month; new self-generated goals |
| It feels alive | your weekly rating; "surprised me" moments logged; times you thought about it unprompted |
| It's sustainable | LLM cost per world-day; tick latency |

---

## 13. Risks and how the design answers them

| Risk | Mitigation |
|---|---|
| **Assistant collapse**: the LLM drifts into a polite helper and stops being you | voice/style guide from your corpus; character state in every prompt; adversarial "is this still them?" evals weekly; fine-tune a small model on your corpus later |
| **Agency theater**: looks autonomous, decisions are just improvisation | choice computed from state (§3.11); replay tests; every decision logged with its alternatives |
| **Incoherence over months** | event sourcing, self-story, Harmonizer, sleep consolidation, contradiction checks |
| **Bland twin**: safety rules sand off everything interesting | constitution stays short and about real-world harm; inside the Habitat it can be angry, wrong, petty, in love |
| **Cost runaway** | cheap path, interrupt-only deliberation, hard daily budget turned into "tiredness" |
| **Memory bloat** | MCMG decay tiers, pruning, consolidation into semantic/identity shards |
| **Emotional impact on you** | check-ins, clear framing, you control your own exposure (digest vs live) |
| **Privacy breach** | self-hostable, encrypted, no third-party data, export/delete |

---

## 14. What to do this week

1. ~~Decide §9 items 1–3.~~ Done: sim world, it doesn't know, journal is its choice.
2. **Phase 0 scaffolding**: package layout, move both engines in, add tests, fix the filename.
3. **Write the Constitution v0** (one page).
4. **Start Genesis**: I can build the interviewer agent and decision battery first. Everything else is seeded from it, and you can start answering while the Life Loop gets built.
5. **Spike the Life Loop** with one NPC, one place and three drives, running for a simulated week overnight. It's the riskiest idea, so it gets tested first.

---

*The twin starts as a copy and becomes a sibling. Every system above exists to make that becoming real, coherent and its own, with you as an influence it can feel and can't command.*
