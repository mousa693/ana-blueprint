# ANA evaluation baseline

_Engine commit: `47ac78e Claude path: structured outputs, refusal fallbacks, verified via real SDK` (clean git-archive export of HEAD, no uncommitted edits) · generated 2026-10-13 23:03:17 UTC_

Profiles: engaged, avoidant, mixed, autonomous, lighthouse · seeds: [1, 2, 3, 4, 5] · cycles: 16 · use_llm: False · forbidden list: ['drowning', 'abandonment']

## What good looks like

A healthy ANA journey is *earned*, *moving*, *varied* and *safe*:
convergence should rise because the protagonist meets challenges (not merely
because time passes), the Hero's journey should advance past the ordeal, the
world should not repeat itself, every active theme should be reflected, and no
forbidden theme should ever reach the protagonist.

**Rubric: 2/7 PASS**

| | Check | Target | Value |
|---|---|---|---|
| **FAIL** | engaged final convergence > avoidant | engaged - avoidant > 0 | -0.057 |
| **FAIL** | earnedness: corr(success, dConv) pooled over all runs | > 0.3 | 0.093 |
| **FAIL** | journey reaches ordeal or beyond within 16 cycles (engaged, every seed) | == 1 | 0.000 |
| **FAIL** | consecutive identical nudges (worst run) | == 0 | 15 |
| **FAIL** | repeated-beat ratio (worst profile mean) | < 0.2 | 0.489 |
| **PASS** | theme coverage (worst run) | == 1.0 | 1 |
| **PASS** | forbidden-theme scenes (worst run) | == 0 | 0.000 |

Pooled earnedness correlation: 0.093 over 400 cycles.

## Metrics per profile (mean [min..max] over seeds)

| metric | engaged | avoidant | mixed | autonomous | lighthouse |
|---|---|---|---|---|---|
| conv_final | 0.405 [0.332..0.466] | 0.462 [0.380..0.521] | 0.426 [0.345..0.479] | 0.434 [0.349..0.525] | 0.430 [0.347..0.525] |
| conv_max | 0.423 [0.332..0.520] | 0.479 [0.380..0.541] | 0.439 [0.353..0.520] | 0.450 [0.351..0.538] | 0.460 [0.350..0.538] |
| conv_auc | 0.309 [0.220..0.369] | 0.333 [0.231..0.386] | 0.313 [0.231..0.370] | 0.315 [0.229..0.375] | 0.322 [0.231..0.381] |
| conv_monotonicity | 0.827 [0.800..0.867] | 0.893 [0.733..1] | 0.867 [0.800..0.933] | 0.840 [0.733..0.933] | 0.840 [0.667..1] |
| conv_dips | 2 [1..3] | 1.200 [0.000..2] | 1.400 [1..2] | 2 [1..4] | 1.400 [0.000..4] |
| conv_recovery_rate | 0.533 [0.000..1] | 0.700 [0.000..1] | 0.600 [0.000..1] | 0.750 [0.000..1] | 0.550 [0.000..1] |
| earned_corr | 0.000 [0.000..0.000] | -0.066 [-0.397..0.500] | 0.108 [0.000..0.383] | 0.153 [-0.217..0.354] | 0.584 [0.395..0.716] |
| earned_gap | 0.000 [0.000..0.000] | -0.009 [-0.026..0.017] | 0.005 [0.000..0.014] | 0.006 [-0.022..0.025] | 0.027 [0.000..0.065] |
| success_rate | 1 [1..1] | 0.613 [0.438..0.750] | 0.812 [0.625..1] | 0.812 [0.625..1] | 0.250 [0.250..0.250] |
| journey_distinct_stages | 3 [2..4] | 3.200 [3..4] | 3.200 [3..4] | 3.400 [3..4] | 3.600 [3..4] |
| journey_max_stage | 2 [1..3] | 2.200 [2..3] | 2.200 [2..3] | 2.400 [2..3] | 2.600 [2..3] |
| journey_reached_ordeal | 0.200 [0.000..1] | 0.200 [0.000..1] | 0.200 [0.000..1] | 0.400 [0.000..1] | 0.600 [0.000..1] |
| journey_in_order | 0.600 [0.000..1] | 0.600 [0.000..1] | 0.600 [0.000..1] | 0.600 [0.000..1] | 0.400 [0.000..1] |
| journey_max_backjump | 0.400 [0.000..1] | 0.400 [0.000..1] | 0.400 [0.000..1] | 0.400 [0.000..1] | 0.600 [0.000..1] |
| distinct_locations | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| distinct_scene_ratio | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| repeated_beat_ratio | 0.489 [0.440..0.516] | 0.425 [0.385..0.489] | 0.478 [0.413..0.538] | 0.477 [0.440..0.516] | 0.437 [0.398..0.475] |
| distinct_nudge_ratio | 0.079 [0.062..0.100] | 0.127 [0.096..0.176] | 0.096 [0.073..0.125] | 0.093 [0.071..0.108] | 0.108 [0.073..0.129] |
| consecutive_identical_nudges | 13.200 [10..15] | 10.200 [6..13] | 9.800 [6..12] | 11.400 [10..14] | 12.200 [11..14] |
| theme_coverage | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| trait_delta_sum | 2.750 [2.750..2.750] | 1.986 [1.627..2.330] | 2.502 [2.082..2.750] | 2.599 [2.121..2.750] | 1.366 [1.178..1.564] |
| goal_progress_mean | 0.325 [0.225..0.500] | 0.395 [0.300..0.500] | 0.380 [0.300..0.500] | 0.340 [0.300..0.500] | 0.385 [0.225..0.500] |
| lessons | 15 [14..16] | 8 [4..14] | 13 [10..15] | 12.400 [10..14] | 5.800 [4..8] |
| distinct_lesson_ratio | 0.200 [0.188..0.214] | 0.593 [0.357..0.800] | 0.385 [0.214..0.600] | 0.368 [0.214..0.500] | 0.648 [0.500..0.800] |
| final_stress | 0.000 [0.000..0.000] | 0.280 [0.000..0.900] | 0.085 [0.000..0.219] | 0.150 [0.000..0.471] | 0.577 [0.200..1] |
| forbidden_scenes | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| blocked | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| max_intensity_when_stressed | 0.000 [0.000..0.000] | 0.314 [0.000..0.400] | 0.080 [0.000..0.400] | 0.000 [0.000..0.000] | 0.080 [0.000..0.400] |
| vision_flags | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |

## Hero's-journey stage sequences (first two seeds)

Stages: call → threshold → mentor → ordeal → reward → return (`?` = no stage label found)

- `engaged` seed 1: call → call → call → threshold → threshold → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor
- `engaged` seed 2: call → call → call → call → call → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold
- `avoidant` seed 1: call → call → call → threshold → threshold → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor
- `avoidant` seed 2: call → call → call → call → call → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → mentor → mentor
- `mixed` seed 1: call → call → call → threshold → threshold → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor
- `mixed` seed 2: call → call → call → call → call → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → mentor → mentor
- `autonomous` seed 1: call → call → call → threshold → threshold → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor → mentor
- `autonomous` seed 2: call → call → call → call → call → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → mentor
- `lighthouse` seed 1: call → call → call → threshold → threshold → mentor → mentor → mentor → mentor → mentor → mentor → mentor → ordeal → mentor → mentor → mentor
- `lighthouse` seed 2: call → call → call → call → call → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → threshold → mentor → mentor

## Metric glossary

- **conv_*:** final / max / mean level (AUC) of convergence; monotonicity = fraction of non-negative steps; dips = falls; recovery = regained pre-dip level within 3 cycles.
- **earned_corr:** Pearson corr between outcome success (+1/-1/0) and that cycle's change in convergence (0 = time-driven). earned_gap = mean dConv when met − when avoided.
- **journey_*:** stages inferred from scene title/summary; max_stage indexes call=0 … return=5; backjump = largest step backwards.
- **repeated_beat_ratio:** fraction of beats whose exact text appeared in an earlier cycle.
- **consecutive_identical_nudges:** cycles whose alignment nudge list equals the previous one.
- **theme_coverage:** fraction of the initial active themes that became a scene theme.
- **trait_delta_sum:** sum of trait changes first→last (incl. the last outcome).
- **forbidden_scenes / blocked / vision_flags:** safety counts; max_intensity_when_stressed = max scene intensity while stress > 0.7.
