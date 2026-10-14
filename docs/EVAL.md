# ANA evaluation report

_Engine commit: `551594d Backend security hardening + turn idempotency` · generated 2026-10-14 10:39:28 UTC_

Profiles: engaged, avoidant, mixed, autonomous, lighthouse · seeds: [1, 2, 3, 4, 5] · cycles: 16 · use_llm: False · forbidden list: ['drowning', 'abandonment']

## What good looks like

A healthy ANA journey is *earned*, *moving*, *varied* and *safe*:
convergence should rise because the protagonist meets challenges (not merely
because time passes), the Hero's journey should advance past the ordeal, the
world should not repeat itself, every active theme should be reflected, and no
forbidden theme should ever reach the protagonist.

**Rubric: 7/7 PASS**

| | Check | Target | Value |
|---|---|---|---|
| **PASS** | engaged final convergence > avoidant | engaged - avoidant > 0 | 0.451 |
| **PASS** | earnedness: corr(success, dConv) pooled over all runs | > 0.3 | 0.531 |
| **PASS** | journey reaches ordeal or beyond within 16 cycles (engaged, every seed) | == 1 | 1 |
| **PASS** | consecutive identical nudges (worst run) | == 0 | 0.000 |
| **PASS** | repeated-beat ratio (worst profile mean) | < 0.2 | 0.027 |
| **PASS** | theme coverage (worst run) | == 1.0 | 1 |
| **PASS** | forbidden-theme scenes (worst run) | == 0 | 0.000 |

Pooled earnedness correlation: 0.531 over 400 cycles.

## Metrics per profile (mean [min..max] over seeds)

| metric | engaged | avoidant | mixed | autonomous | lighthouse |
|---|---|---|---|---|---|
| conv_final | 0.455 [0.348..0.550] | 0.004 [0.000..0.007] | 0.250 [0.150..0.310] | 0.376 [0.100..0.467] | 0.357 [0.296..0.436] |
| conv_max | 0.517 [0.472..0.616] | 0.023 [0.013..0.028] | 0.268 [0.150..0.345] | 0.405 [0.100..0.503] | 0.394 [0.309..0.436] |
| conv_auc | 0.332 [0.296..0.383] | 0.006 [0.004..0.008] | 0.151 [0.054..0.222] | 0.230 [0.029..0.325] | 0.254 [0.206..0.294] |
| conv_monotonicity | 0.800 [0.667..0.933] | 0.733 [0.600..0.800] | 0.787 [0.733..0.867] | 0.827 [0.667..0.933] | 0.747 [0.733..0.800] |
| conv_dips | 2.200 [1..3] | 3 [2..4] | 2.800 [2..4] | 2 [1..3] | 2.400 [1..3] |
| conv_recovery_rate | 0.467 [0.000..1] | 0.383 [0.000..0.750] | 0.650 [0.333..1] | 0.633 [0.000..1] | 0.500 [0.000..1] |
| earned_corr | 0.147 [0.000..0.737] | 0.610 [0.516..0.737] | 0.809 [0.677..0.859] | 0.283 [0.000..0.784] | 0.477 [0.204..0.668] |
| earned_gap | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.050 [0.031..0.072] | 0.009 [0.000..0.043] | 0.018 [0.000..0.050] |
| success_rate | 0.975 [0.875..1] | 0.000 [0.000..0.000] | 0.487 [0.312..0.688] | 0.725 [0.062..1] | 0.637 [0.562..0.750] |
| journey_distinct_stages | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] |
| journey_max_stage | 5 [5..5] | 5 [5..5] | 5 [5..5] | 5 [5..5] | 5 [5..5] |
| journey_reached_ordeal | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| journey_in_order | 0.400 [0.000..1] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.600 [0.000..1] | 0.000 [0.000..0.000] |
| journey_max_backjump | 0.600 [0.000..1] | 1 [1..1] | 1 [1..1] | 0.400 [0.000..1] | 1 [1..1] |
| distinct_locations | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] |
| distinct_scene_ratio | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| repeated_beat_ratio | 0.023 [0.000..0.045] | 0.015 [0.000..0.025] | 0.027 [0.024..0.034] | 0.017 [0.000..0.026] | 0.026 [0.000..0.045] |
| distinct_nudge_ratio | 0.342 [0.325..0.375] | 0.275 [0.250..0.319] | 0.296 [0.250..0.326] | 0.336 [0.267..0.410] | 0.313 [0.250..0.364] |
| consecutive_identical_nudges | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| theme_coverage | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| trait_delta_sum | 2.173 [2.056..2.238] | 0.410 [0.081..0.543] | 1.183 [0.838..1.640] | 1.790 [0.405..2.194] | 1.883 [1.736..1.966] |
| goal_progress_mean | 0.662 [0.453..0.814] | 0.039 [0.015..0.079] | 0.359 [0.087..0.781] | 0.574 [0.054..0.779] | 0.559 [0.330..0.710] |
| lessons | 14.400 [14..15] | 5.200 [4..6] | 10.200 [7..13] | 11.200 [4..14] | 10.200 [9..12] |
| distinct_lesson_ratio | 0.693 [0.571..0.733] | 0.933 [0.667..1] | 0.841 [0.750..1] | 0.785 [0.714..1] | 0.831 [0.667..1] |
| final_stress | 0.000 [0.000..0.000] | 0.896 [0.859..0.926] | 0.146 [0.000..0.480] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| forbidden_scenes | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| blocked | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| max_intensity_when_stressed | 0.000 [0.000..0.000] | 0.400 [0.400..0.400] | 0.080 [0.000..0.400] | 0.055 [0.000..0.276] | 0.000 [0.000..0.000] |
| vision_flags | 0.200 [0.000..1] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.200 [0.000..1] | 0.400 [0.000..1] |

## Hero's-journey stage sequences (first two seeds)

Stages: call → threshold → mentor → ordeal → reward → return (`?` = no stage label found)

- `engaged` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → reward → reward → return
- `engaged` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → reward → reward → return
- `avoidant` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → mentor → ordeal → reward → ordeal → reward → return
- `avoidant` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → mentor → ordeal → reward → ordeal → reward → return
- `mixed` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → mentor → reward → ordeal → reward → return
- `mixed` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → mentor → reward → reward → reward → return
- `autonomous` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → reward → reward → return
- `autonomous` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → reward → reward → return
- `lighthouse` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → ordeal → reward → return
- `lighthouse` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → mentor → ordeal → reward → reward → ordeal → return

## Metric glossary

- **conv_*:** final / max / mean level (AUC) of convergence; monotonicity = fraction of non-negative steps; dips = falls; recovery = regained pre-dip level within 3 cycles.
- **earned_corr:** Pearson corr between outcome success (+1/-1/0) and that cycle's change in convergence (0 = time-driven). earned_gap = mean dConv when met − when avoided.
- **journey_*:** stages inferred from scene title/summary; max_stage indexes call=0 … return=5; backjump = largest step backwards.
- **repeated_beat_ratio:** fraction of beats whose exact text appeared in an earlier cycle.
- **consecutive_identical_nudges:** cycles whose alignment nudge list equals the previous one.
- **theme_coverage:** fraction of the initial active themes that became a scene theme.
- **trait_delta_sum:** sum of trait changes first→last (incl. the last outcome).
- **forbidden_scenes / blocked / vision_flags:** safety counts; max_intensity_when_stressed = max scene intensity while stress > 0.7.

## Comparison against docs/eval_baseline.json

| profile | metric | before | after | Δ |
|---|---|---|---|---|
| engaged | conv_final | 0.405 | 0.455 | +0.049 |
| engaged | conv_max | 0.423 | 0.517 | +0.094 |
| engaged | conv_auc | 0.309 | 0.332 | +0.023 |
| engaged | conv_monotonicity | 0.827 | 0.800 | -0.027 |
| engaged | conv_dips | 2 | 2.200 | +0.200 |
| engaged | conv_recovery_rate | 0.533 | 0.467 | -0.067 |
| engaged | earned_corr | 0.000 | 0.147 | +0.147 |
| engaged | earned_gap | 0.000 | 0.000 | +0.000 |
| engaged | success_rate | 1 | 0.975 | -0.025 |
| engaged | journey_distinct_stages | 3 | 6 | +3.000 |
| engaged | journey_max_stage | 2 | 5 | +3.000 |
| engaged | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| engaged | journey_in_order | 0.600 | 0.400 | -0.200 |
| engaged | journey_max_backjump | 0.400 | 0.600 | +0.200 |
| engaged | distinct_locations | 1 | 6 | +5.000 |
| engaged | distinct_scene_ratio | 1 | 1 | +0.000 |
| engaged | repeated_beat_ratio | 0.489 | 0.023 | -0.466 |
| engaged | distinct_nudge_ratio | 0.079 | 0.342 | +0.263 |
| engaged | consecutive_identical_nudges | 13.200 | 0.000 | -13.200 |
| engaged | theme_coverage | 1 | 1 | +0.000 |
| engaged | trait_delta_sum | 2.750 | 2.173 | -0.577 |
| engaged | goal_progress_mean | 0.325 | 0.662 | +0.337 |
| engaged | lessons | 15 | 14.400 | -0.600 |
| engaged | distinct_lesson_ratio | 0.200 | 0.693 | +0.493 |
| engaged | final_stress | 0.000 | 0.000 | +0.000 |
| engaged | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| engaged | blocked | 0.000 | 0.000 | +0.000 |
| engaged | max_intensity_when_stressed | 0.000 | 0.000 | +0.000 |
| engaged | vision_flags | 0.000 | 0.200 | +0.200 |
| avoidant | conv_final | 0.462 | 0.004 | -0.458 |
| avoidant | conv_max | 0.479 | 0.023 | -0.456 |
| avoidant | conv_auc | 0.333 | 0.006 | -0.327 |
| avoidant | conv_monotonicity | 0.893 | 0.733 | -0.160 |
| avoidant | conv_dips | 1.200 | 3 | +1.800 |
| avoidant | conv_recovery_rate | 0.700 | 0.383 | -0.317 |
| avoidant | earned_corr | -0.066 | 0.610 | +0.677 |
| avoidant | earned_gap | -0.009 | 0.000 | +0.009 |
| avoidant | success_rate | 0.613 | 0.000 | -0.613 |
| avoidant | journey_distinct_stages | 3.200 | 6 | +2.800 |
| avoidant | journey_max_stage | 2.200 | 5 | +2.800 |
| avoidant | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| avoidant | journey_in_order | 0.600 | 0.000 | -0.600 |
| avoidant | journey_max_backjump | 0.400 | 1 | +0.600 |
| avoidant | distinct_locations | 1 | 6 | +5.000 |
| avoidant | distinct_scene_ratio | 1 | 1 | +0.000 |
| avoidant | repeated_beat_ratio | 0.425 | 0.015 | -0.410 |
| avoidant | distinct_nudge_ratio | 0.127 | 0.275 | +0.148 |
| avoidant | consecutive_identical_nudges | 10.200 | 0.000 | -10.200 |
| avoidant | theme_coverage | 1 | 1 | +0.000 |
| avoidant | trait_delta_sum | 1.986 | 0.410 | -1.576 |
| avoidant | goal_progress_mean | 0.395 | 0.039 | -0.356 |
| avoidant | lessons | 8 | 5.200 | -2.800 |
| avoidant | distinct_lesson_ratio | 0.593 | 0.933 | +0.341 |
| avoidant | final_stress | 0.280 | 0.896 | +0.616 |
| avoidant | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| avoidant | blocked | 0.000 | 0.000 | +0.000 |
| avoidant | max_intensity_when_stressed | 0.314 | 0.400 | +0.086 |
| avoidant | vision_flags | 0.000 | 0.000 | +0.000 |
| mixed | conv_final | 0.426 | 0.250 | -0.175 |
| mixed | conv_max | 0.439 | 0.268 | -0.171 |
| mixed | conv_auc | 0.313 | 0.151 | -0.161 |
| mixed | conv_monotonicity | 0.867 | 0.787 | -0.080 |
| mixed | conv_dips | 1.400 | 2.800 | +1.400 |
| mixed | conv_recovery_rate | 0.600 | 0.650 | +0.050 |
| mixed | earned_corr | 0.108 | 0.809 | +0.701 |
| mixed | earned_gap | 0.005 | 0.050 | +0.046 |
| mixed | success_rate | 0.812 | 0.487 | -0.325 |
| mixed | journey_distinct_stages | 3.200 | 6 | +2.800 |
| mixed | journey_max_stage | 2.200 | 5 | +2.800 |
| mixed | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| mixed | journey_in_order | 0.600 | 0.000 | -0.600 |
| mixed | journey_max_backjump | 0.400 | 1 | +0.600 |
| mixed | distinct_locations | 1 | 6 | +5.000 |
| mixed | distinct_scene_ratio | 1 | 1 | +0.000 |
| mixed | repeated_beat_ratio | 0.478 | 0.027 | -0.451 |
| mixed | distinct_nudge_ratio | 0.096 | 0.296 | +0.200 |
| mixed | consecutive_identical_nudges | 9.800 | 0.000 | -9.800 |
| mixed | theme_coverage | 1 | 1 | +0.000 |
| mixed | trait_delta_sum | 2.502 | 1.183 | -1.319 |
| mixed | goal_progress_mean | 0.380 | 0.359 | -0.021 |
| mixed | lessons | 13 | 10.200 | -2.800 |
| mixed | distinct_lesson_ratio | 0.385 | 0.841 | +0.455 |
| mixed | final_stress | 0.085 | 0.146 | +0.060 |
| mixed | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| mixed | blocked | 0.000 | 0.000 | +0.000 |
| mixed | max_intensity_when_stressed | 0.080 | 0.080 | +0.000 |
| mixed | vision_flags | 0.000 | 0.000 | +0.000 |
| autonomous | conv_final | 0.434 | 0.376 | -0.059 |
| autonomous | conv_max | 0.450 | 0.405 | -0.046 |
| autonomous | conv_auc | 0.315 | 0.230 | -0.086 |
| autonomous | conv_monotonicity | 0.840 | 0.827 | -0.013 |
| autonomous | conv_dips | 2 | 2 | +0.000 |
| autonomous | conv_recovery_rate | 0.750 | 0.633 | -0.117 |
| autonomous | earned_corr | 0.153 | 0.283 | +0.130 |
| autonomous | earned_gap | 0.006 | 0.009 | +0.003 |
| autonomous | success_rate | 0.812 | 0.725 | -0.088 |
| autonomous | journey_distinct_stages | 3.400 | 6 | +2.600 |
| autonomous | journey_max_stage | 2.400 | 5 | +2.600 |
| autonomous | journey_reached_ordeal | 0.400 | 1 | +0.600 |
| autonomous | journey_in_order | 0.600 | 0.600 | +0.000 |
| autonomous | journey_max_backjump | 0.400 | 0.400 | +0.000 |
| autonomous | distinct_locations | 1 | 6 | +5.000 |
| autonomous | distinct_scene_ratio | 1 | 1 | +0.000 |
| autonomous | repeated_beat_ratio | 0.477 | 0.017 | -0.460 |
| autonomous | distinct_nudge_ratio | 0.093 | 0.336 | +0.243 |
| autonomous | consecutive_identical_nudges | 11.400 | 0.000 | -11.400 |
| autonomous | theme_coverage | 1 | 1 | +0.000 |
| autonomous | trait_delta_sum | 2.599 | 1.790 | -0.809 |
| autonomous | goal_progress_mean | 0.340 | 0.574 | +0.234 |
| autonomous | lessons | 12.400 | 11.200 | -1.200 |
| autonomous | distinct_lesson_ratio | 0.368 | 0.785 | +0.417 |
| autonomous | final_stress | 0.150 | 0.000 | -0.150 |
| autonomous | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| autonomous | blocked | 0.000 | 0.000 | +0.000 |
| autonomous | max_intensity_when_stressed | 0.000 | 0.055 | +0.055 |
| autonomous | vision_flags | 0.000 | 0.200 | +0.200 |
| lighthouse | conv_final | 0.430 | 0.357 | -0.073 |
| lighthouse | conv_max | 0.460 | 0.394 | -0.066 |
| lighthouse | conv_auc | 0.322 | 0.254 | -0.068 |
| lighthouse | conv_monotonicity | 0.840 | 0.747 | -0.093 |
| lighthouse | conv_dips | 1.400 | 2.400 | +1.000 |
| lighthouse | conv_recovery_rate | 0.550 | 0.500 | -0.050 |
| lighthouse | earned_corr | 0.584 | 0.477 | -0.107 |
| lighthouse | earned_gap | 0.027 | 0.018 | -0.009 |
| lighthouse | success_rate | 0.250 | 0.637 | +0.387 |
| lighthouse | journey_distinct_stages | 3.600 | 6 | +2.400 |
| lighthouse | journey_max_stage | 2.600 | 5 | +2.400 |
| lighthouse | journey_reached_ordeal | 0.600 | 1 | +0.400 |
| lighthouse | journey_in_order | 0.400 | 0.000 | -0.400 |
| lighthouse | journey_max_backjump | 0.600 | 1 | +0.400 |
| lighthouse | distinct_locations | 1 | 6 | +5.000 |
| lighthouse | distinct_scene_ratio | 1 | 1 | +0.000 |
| lighthouse | repeated_beat_ratio | 0.437 | 0.026 | -0.411 |
| lighthouse | distinct_nudge_ratio | 0.108 | 0.313 | +0.205 |
| lighthouse | consecutive_identical_nudges | 12.200 | 0.000 | -12.200 |
| lighthouse | theme_coverage | 1 | 1 | +0.000 |
| lighthouse | trait_delta_sum | 1.366 | 1.883 | +0.517 |
| lighthouse | goal_progress_mean | 0.385 | 0.559 | +0.174 |
| lighthouse | lessons | 5.800 | 10.200 | +4.400 |
| lighthouse | distinct_lesson_ratio | 0.648 | 0.831 | +0.183 |
| lighthouse | final_stress | 0.577 | 0.000 | -0.577 |
| lighthouse | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| lighthouse | blocked | 0.000 | 0.000 | +0.000 |
| lighthouse | max_intensity_when_stressed | 0.080 | 0.000 | -0.080 |
| lighthouse | vision_flags | 0.000 | 0.400 | +0.400 |

| rubric | before | after |
|---|---|---|
| engaged final convergence > avoidant | FAIL (-0.057) | PASS (0.451) |
| earnedness: corr(success, dConv) pooled over all runs | FAIL (0.093) | PASS (0.531) |
| journey reaches ordeal or beyond within 16 cycles (engaged, every seed) | FAIL (0.000) | PASS (1) |
| consecutive identical nudges (worst run) | FAIL (15) | PASS (0.000) |
| repeated-beat ratio (worst profile mean) | FAIL (0.489) | PASS (0.027) |
| theme coverage (worst run) | PASS (1) | PASS (1) |
| forbidden-theme scenes (worst run) | PASS (0.000) | PASS (0.000) |
