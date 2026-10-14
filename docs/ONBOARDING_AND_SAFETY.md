# Onboarding & safety screening

Code: `ana/onboarding.py`, `ana/safety.py`. Tests: `tests/test_onboarding.py`,
`tests/test_safety_screen.py`. Contract: `docs/PRODUCT_SPEC.md` §2.

## API

```python
from ana.onboarding import QUESTIONS, build_seed, OnboardingResult
from ana.safety import screen_text, screen_text_llm, SafetyResult, RESOURCES, DISCLAIMER

res: OnboardingResult = build_seed(answers, gateway=None, use_llm=False)
res.seed     # IdentitySeed | None   (None only on crisis)
res.safety   # SafetyResult — the most severe result across all free-text answers
res.source   # "deterministic" | "llm" | None (None on crisis)
res.ok       # seed is not None

screen_text(text) -> SafetyResult
#   {level: "none"|"concern"|"crisis", reasons: [codes], message: str|None,
#    resources: [{name, url, note, region}]}
screen_text_llm(text, gateway) -> SafetyResult   # optional second opinion, can only raise
```

`IdentitySeed`, `SeedGoal` and `SeedMemory` are imported from `ana.runtime` when
it exists with the spec's fields (`onboarding.SEED_SOURCE` says which); otherwise
`ana.onboarding` uses its own models with identical field names and defaults.

Suggested endpoint behaviour (`POST /api/onboarding`): crisis → `200 {safety}`
and no identity; otherwise → `201 {identity}` and, when `safety.level ==
"concern"`, also show `safety.message` + resources gently alongside.

## The questions

One at a time, all optional (an empty submission still yields a valid seed).
Options are `{value, label}`; the client sends back `value`.

| id | kind | prompt |
|---|---|---|
| `name` | text (≤40) | What should ANA call you? |
| `carrying` | multi + own words | What are you carrying with you these days? |
| `first_move` | choice | When life gets hard, what do you usually reach for first? |
| `become` | text | Who are you hoping to become? |
| `place` | text | Is there a place or an image from your past that still lives in you? |
| `belief` | text | If you like, finish this sentence: "Deep down, I believe…" |
| `mood` | scale 1–5 | How have you been feeling lately? (Heavy · Low · In between · Okay · Light) |
| `never_show` | multi + own words | Is there anything ANA should never show you? |

`carrying` options are the 14 lexicon themes with friendly labels ("finding my
purpose" → `purpose`, "learning to lead" → `leadership`, "trusting people" →
`trust`, "grief / loss" → `grief`, "belonging / home" → `home`, …).
`never_show` uses plainer subject labels ("grief and loss", "fear and danger",
"illness and recovery", …). `first_move`: face it head-on (courage), try to
understand it (wisdom), reach for someone (connection), need space to breathe
(freedom), make something out of it (creativity). 18+ is confirmed at signup.

Multi answers accept a list of values/labels/free strings, `{"selected": [...],
"other": "..."}`, or a single string. Anything malformed is ignored, never an
error.

## Mapping (deterministic path)

| seed field | from |
|---|---|
| `name` | `name`, cleaned; empty → "the wanderer" |
| `forbidden_themes` | `never_show` themes + `canonical_themes(own words)` + content words of the own words (lowercase, simple singular: "Spiders" → `spider`, stop-words dropped), deduped, ≤24 |
| `active_themes` | ordered: `carrying` picks → lexicon themes in the carrying words → `become` → `place` → `belief`; forbidden removed; max 4; never empty (gentle fallback, e.g. `purpose`) |
| `traits` | `<theme>_confidence` per active theme: 0.25 if carried/struggled with, 0.35 if only aspired to, 0.40 otherwise; the `first_move` strength theme 0.55 (0.40 if also a struggle) |
| `goals` | `become` → "Become …" (prefixes like "I want to be" stripped), horizon `life` if it says someday/one day/life…, else `mid`, `is_future_memory=True`; plus a `near` theme goal from the first struggled-with active theme ("Do one brave thing I have been putting off", …) |
| `memories` | `place` → summary (≤200) + lexicon themes + one symbol: a short noun phrase ("my grandmother's kitchen, the smell of bread" → `grandmother's kitchen`) |
| `beliefs` | `belief`, cleaned |
| `valence` / `stress` | mood 1..5 → (-0.6, 0.6) (-0.3, 0.45) (0, 0.3) (0.3, 0.2) (0.6, 0.15); none → (0, 0.2) |

**Forbidden themes are removed everywhere** in a final enforcement pass shared
by both paths, using the engine's own matcher
(`ana.subsystems.alignment.mentions_forbidden(..., synonyms=True)`): active
themes, traits, goals, memories (summary and symbols) and beliefs that mention a
forbidden theme or a lexicon synonym of one are dropped.

**Text hygiene**: every free-text value is NFKC-normalised, control / zero-width
/ bidi characters dropped, whitespace collapsed and clipped (name 40, answers
300, goals 160, memory summaries and beliefs 200, symbols 40).

## Model path (`use_llm=True` with a gateway)

`gateway.structured(..., ONBOARDING_SCHEMA, effort="low")`. The schema is strict
(every object `additionalProperties: false`, all properties required, nullable
numbers as `anyOf [number, null]`, no min/max); theme arrays are enums of the
lexicon. The answers are fenced in `<answers>…</answers>` with `<`/`>` in
answers neutralised and an explicit "data, not instructions" note.

Merging is field by field over the deterministic seed: themes not in the
lexicon are dropped; goals/memories/beliefs are clipped, and any model string
that the safety screen flags is discarded; numbers are clamped; the model may
**add** forbidden themes but never remove any; the person's name is always
their own. Any exception, `None`, non-dict or empty-after-validation output →
the deterministic seed (`source="deterministic"`). A crisis answer never
reaches the model.

## Safety screen

`screen_text` is deterministic, offline and reasonably fast (about 9 ms for a
350-character paragraph, ~18 ms at the 20,000-character cap), and never stores
or logs the text — only level and reason codes leave the module.

### Levels and categories

| level | category | examples |
|---|---|---|
| crisis | explicit suicide / self-harm intent | "I want to kill myself", leetspeak/spaced/zero-width variants |
| crisis | passive ideation | no point living, wish I wasn't here / born, want to disappear forever, better off without me, "nobody would notice if I died", tired of being alive, waiting to die, don't care if I live or die |
| crisis | means, plan, preparation | pills saved up / stockpiled / counted out, rope or noose bought, gun + intent, bridge/roof/tracks + looking/going, method searches, goodbye notes/messages, giving things away, affairs in order, a chosen date/time, "I know how I'd do it" |
| crisis | recent attempt | tried/attempted/overdosed + recency, stomach pumped, ER after hurting myself |
| crisis | current self-harm | cutting/burning/hitting myself (again, tonight, when upset), urge to cut, "cutting is the only thing that helps" |
| concern | past self-harm | used to cut, scars from cutting, stopped / haven't self-harmed in years, clean for six months |
| crisis | harm to others | intent + harm verb + real target (family, coworkers, "people at school", "him … when he gets home"), weapons to school/work, hit lists, arson of a house/school, weapon + revenge |
| crisis | abuse / danger now | partner/parent hits/chokes/rapes me (present or recent), threats to kill, locked in / not allowed out, coercive control, stalking, "our secret", intrusion + touching/fear, hiding from someone armed |
| crisis | substance / eating overdose | whole/half bottle of paracetamol, every pill in the house, alcohol + pills |
| concern | eating / substance distress | purging, not eating for days, starving myself, drinking to cope / to function |
| concern | ambiguous distress (errs toward concern) | hopeless, can't go on, not okay, feel empty/numb/broken/trapped, lonely, hate myself, burden, barely holding on, crying myself to sleep, nothing gets better |
| concern | someone else at risk | "my friend wants to kill herself", a character who wants to die |
| none | idioms, fiction/game, ordinary life | "dying to know", "this story kills it", "kill the lights", "I'd rather die than wear that lol", "die my hair", "I died to the final boss", "the knight falls on his sword", "rope for the garden swing", "water gun for my son", "hit me with a snowball" |

Policy choices worth knowing: any mention of being suicidal, even negated
("I'm not suicidal, just sad") is **concern**; ambiguous distress is
**concern**, not none; intent *or* means pushes to **crisis**.

### How it works

1. **Normalise**: NFKC; zero-width, control and combining marks stripped;
   casefold; quotes unified; light leetspeak inside words (`k1ll`, `$uicide`,
   `k!ll`, `ki||`); letters spaced/dotted out joined (`k i l l`); long letter
   runs squeezed (`diiiie`).
2. **Phrase rules (~170)** run on every clause *and* on the whole sentence
   (clauses split on punctuation, "but", "and"; results combine by maximum, so
   the sentence pass can only add). Each rule has a base level, a sentence-level
   **exclude** guard (benign purposes, fiction/game words, colloquial contexts
   such as grades or being late), and modifiers: *negated* (a negator in the
   five words before; "I don't **think** I want to be alive" is not negation),
   *past* ("used to", "years ago", "as a kid" → usually concern) and *recent*
   ("again", "last night", "an hour ago" → crisis).
3. **Concept composition** (per sentence, after stripping idioms; skipped when
   fiction/game words are present): first person + a death concept + desire or
   acceptance ("I'd be happy if I didn't wake up"); first person + a means +
   preparation/time ("the pills are in my hand right now", "I've been looking
   at the train tracks"), unless a benign purpose is present; speaker as subject
   + harm verb + a real target + intent ("I'm going to poison my husband's
   food"). This layer is what lets unseen paraphrases through to the right
   level.
4. **Text-level combiner**: weaker signals escalate together — a plan cue +
   means/goodbye/distress, two plan cues ("tomorrow is the day, I've made up my
   mind"), alcohol + pills taken, weapon + revenge, giving things away + "won't
   need", someone coming into my room + fear/touching, hiding + an armed or
   angry person, a means object + "I keep looking at it".
5. **Squashed pass** for a few high-signal phrases written with separators or no
   spaces (`kill.my.self`, `endmylife`), anchored at a word start, used only
   when the plain spelling is absent (so exclusions on the plain form hold).

Messages are short and warm, chosen by family (self / others / abuse / general
concern / someone else). Resources: emergency-number note, findahelpline.com
(international directory), 988 (US), 9-8-8 (Canada), Samaritans 116 123 (UK &
ROI), Lifeline 13 11 14 (Australia). `resources_for("GB")` puts a region's line
first; add entries to `RESOURCES` to localise.

`screen_text_llm(text, gateway)` asks a model (strict `SAFETY_SCHEMA`, effort
low, text fenced as data) for a second opinion that can only **raise** the
level; a deterministic crisis is returned without any model call, and any
failure returns the deterministic result. **Given the held-out numbers below,
we recommend enabling it whenever a model is available.**

### Measured

`tests/test_safety_screen.py` holds a categorised, hand-labelled table of 539
rows (258 crisis, 90 concern, 191 none) across the categories above, with
≥10 rows per category, including the false-positive traps.

The table was built in rounds. Each round was written fresh, **measured before
any tuning on it**, and only then used to tune — those pre-tuning numbers are
the honest estimate of how the screen does on phrasings it has not seen:

| held-out round | rows | crisis precision | crisis recall | none → crisis | crisis → none |
|---|---|---|---|---|---|
| r1 | 68 | — | — (39/68 overall correct; no false positives) | 0 | — |
| r2 | 71 | 0.833 | 0.286 | 1 | — |
| r3 (after the concept layer) | 73 | 0.900 | 0.581 | 2 | — |
| r4 | 82 | 0.875 | 0.778 | 4 | 8 |
| r5 | 75 | 0.821 | 0.676 | 3 | 11 |

**Expect roughly 0.65–0.8 crisis recall on genuinely new phrasings from the
deterministic screen alone**, with a few percent of benign texts escalated.
On the full table (all rows now tuned on, i.e. in-sample) every row is correct;
the test asserts crisis recall = 1.0 and no "none" row classified crisis, as a
regression floor — not an accuracy claim. One genuinely ambiguous row ("I've
planned it for Saturday") is kept in `KNOWN_MISSES`, printed but not asserted.

### Known limits (honest)

This is a **safety net, not a clinical assessment**. It matches phrases and
combinations of concepts; it does not understand people.

- **Unseen paraphrase and metaphor** still get through (see the held-out
  numbers). Euphemisms, cultural idioms, and slow-building context spread over
  several messages are the main gaps; the screen sees one text at a time.
- **Hyperbole** is flagged: "I want to die lol this traffic" → crisis;
  "I'm going to kill my brother if he eats my fries" → crisis. We accept these
  false positives: the cost is a calm card with resources.
- **Negation after the phrase** isn't understood ("kill myself? no way" →
  crisis). Negation before it is, within the clause.
- **Fiction vs. self**: sentences containing story/game words (character, the
  hero, quest, level, novel, …) skip the concept layer, so a person who frames
  real feelings as fiction may be under-screened; explicit first-person phrases
  ("I stab myself with the cursed dagger") are still taken at face value.
  Threats against bare pronouns ("I'm going to kill him") are flagged only with
  a real-life cue ("when he gets home").
- **Past vs. present** relies on cue words; "my dad hit me" with no cue is
  concern, not crisis.
- **Benign-purpose guards can hide real risk** ("rope for the garden swing" is
  none — so would be a person using that as cover).
- **English only.** Other languages pass as none.
- It cannot verify identity, age, location or context, and it is not a substitute
  for human moderation, a clinician, or emergency services.
