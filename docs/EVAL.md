# ANA evaluation report

_Engine commit: `43543cc Web viewer: bounded memory/disk, non-blocking SSE, host/origin guard` · generated 2026-10-13 23:40:53 UTC_

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
| **PASS** | engaged final convergence > avoidant | engaged - avoidant > 0 | 0.490 |
| **PASS** | earnedness: corr(success, dConv) pooled over all runs | > 0.3 | 0.612 |
| **PASS** | journey reaches ordeal or beyond within 16 cycles (engaged, every seed) | == 1 | 1 |
| **PASS** | consecutive identical nudges (worst run) | == 0 | 0.000 |
| **PASS** | repeated-beat ratio (worst profile mean) | < 0.2 | 0.032 |
| **PASS** | theme coverage (worst run) | == 1.0 | 1 |
| **PASS** | forbidden-theme scenes (worst run) | == 0 | 0.000 |

Pooled earnedness correlation: 0.612 over 400 cycles.

## Metrics per profile (mean [min..max] over seeds)

| metric | engaged | avoidant | mixed | autonomous | lighthouse |
|---|---|---|---|---|---|
| conv_final | 0.509 [0.468..0.620] | 0.019 [0.006..0.034] | 0.327 [0.253..0.385] | 0.418 [0.084..0.557] | 0.355 [0.242..0.461] |
| conv_max | 0.546 [0.469..0.620] | 0.036 [0.022..0.046] | 0.330 [0.253..0.385] | 0.436 [0.084..0.584] | 0.376 [0.242..0.465] |
| conv_auc | 0.341 [0.295..0.395] | 0.012 [0.006..0.016] | 0.166 [0.091..0.212] | 0.247 [0.024..0.365] | 0.252 [0.172..0.295] |
| conv_monotonicity | 0.840 [0.733..1] | 0.787 [0.600..0.867] | 0.827 [0.733..0.933] | 0.800 [0.667..0.933] | 0.733 [0.533..0.933] |
| conv_dips | 1.800 [0.000..4] | 2.200 [1..3] | 2.200 [1..3] | 2.200 [1..4] | 1.800 [1..3] |
| conv_recovery_rate | 0.500 [0.000..1] | 0.467 [0.000..0.667] | 0.767 [0.500..1] | 0.800 [0.000..1] | 0.100 [0.000..0.500] |
| earned_corr | 0.082 [0.000..0.187] | 0.635 [0.478..0.803] | 0.814 [0.768..0.865] | 0.367 [0.000..0.719] | 0.641 [0.265..0.797] |
| earned_gap | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.058 [0.036..0.085] | 0.016 [0.000..0.048] | 0.028 [0.000..0.081] |
| success_rate | 0.963 [0.938..1] | 0.000 [0.000..0.000] | 0.475 [0.312..0.625] | 0.725 [0.062..1] | 0.588 [0.438..0.688] |
| journey_distinct_stages | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] |
| journey_max_stage | 5 [5..5] | 5 [5..5] | 5 [5..5] | 5 [5..5] | 5 [5..5] |
| journey_reached_ordeal | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| journey_in_order | 0.200 [0.000..1] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.200 [0.000..1] |
| journey_max_backjump | 0.800 [0.000..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 0.800 [0.000..1] |
| distinct_locations | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] | 6 [6..6] |
| distinct_scene_ratio | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| repeated_beat_ratio | 0.028 [0.011..0.070] | 0.014 [0.000..0.034] | 0.023 [0.000..0.047] | 0.032 [0.000..0.070] | 0.029 [0.000..0.065] |
| distinct_nudge_ratio | 0.309 [0.262..0.359] | 0.278 [0.250..0.320] | 0.276 [0.244..0.304] | 0.319 [0.286..0.341] | 0.280 [0.239..0.333] |
| consecutive_identical_nudges | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| theme_coverage | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] | 1 [1..1] |
| trait_delta_sum | 2.193 [2.123..2.226] | 0.506 [0.026..0.680] | 1.492 [1.111..1.884] | 1.781 [0.342..2.226] | 1.833 [1.632..1.928] |
| goal_progress_mean | 0.645 [0.601..0.752] | 0.061 [0.026..0.107] | 0.399 [0.291..0.575] | 0.522 [0.120..0.639] | 0.485 [0.374..0.608] |
| lessons | 14.400 [13..15] | 5.200 [4..6] | 10 [8..12] | 11.400 [4..15] | 9.800 [9..11] |
| distinct_lesson_ratio | 0.740 [0.667..0.846] | 0.900 [0.833..1] | 0.967 [0.833..1] | 0.821 [0.733..1] | 0.897 [0.778..1] |
| final_stress | 0.000 [0.000..0.000] | 0.647 [0.445..0.854] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| forbidden_scenes | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| blocked | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] | 0.000 [0.000..0.000] |
| max_intensity_when_stressed | 0.000 [0.000..0.000] | 0.320 [0.000..0.400] | 0.000 [0.000..0.000] | 0.080 [0.000..0.400] | 0.000 [0.000..0.000] |
| vision_flags | 0.400 [0.000..2] | 0.000 [0.000..0.000] | 0.400 [0.000..2] | 0.000 [0.000..0.000] | 0.200 [0.000..1] |

## Hero's-journey stage sequences (first two seeds)

Stages: call → threshold → mentor → ordeal → reward → return (`?` = no stage label found)

- `engaged` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → ordeal → reward → return
- `engaged` seed 2: call → call → call → threshold → threshold → threshold → mentor → threshold → mentor → ordeal → mentor → ordeal → reward → ordeal → reward → return
- `avoidant` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → mentor → reward → ordeal → reward → return
- `avoidant` seed 2: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → ordeal → reward → return
- `mixed` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → mentor → reward → ordeal → reward → return
- `mixed` seed 2: call → call → call → threshold → threshold → call → mentor → mentor → mentor → ordeal → ordeal → mentor → reward → reward → reward → return
- `autonomous` seed 1: call → call → call → threshold → call → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → ordeal → reward → return
- `autonomous` seed 2: call → call → call → threshold → threshold → threshold → mentor → threshold → mentor → ordeal → mentor → ordeal → reward → ordeal → reward → return
- `lighthouse` seed 1: call → call → call → threshold → threshold → threshold → mentor → mentor → mentor → ordeal → ordeal → ordeal → reward → ordeal → reward → return
- `lighthouse` seed 2: call → call → call → threshold → threshold → threshold → mentor → threshold → mentor → ordeal → ordeal → mentor → reward → ordeal → reward → return

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
| engaged | conv_final | 0.405 | 0.509 | +0.104 |
| engaged | conv_max | 0.423 | 0.546 | +0.124 |
| engaged | conv_auc | 0.309 | 0.341 | +0.032 |
| engaged | conv_monotonicity | 0.827 | 0.840 | +0.013 |
| engaged | conv_dips | 2 | 1.800 | -0.200 |
| engaged | conv_recovery_rate | 0.533 | 0.500 | -0.033 |
| engaged | earned_corr | 0.000 | 0.082 | +0.082 |
| engaged | earned_gap | 0.000 | 0.000 | +0.000 |
| engaged | success_rate | 1 | 0.963 | -0.037 |
| engaged | journey_distinct_stages | 3 | 6 | +3.000 |
| engaged | journey_max_stage | 2 | 5 | +3.000 |
| engaged | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| engaged | journey_in_order | 0.600 | 0.200 | -0.400 |
| engaged | journey_max_backjump | 0.400 | 0.800 | +0.400 |
| engaged | distinct_locations | 1 | 6 | +5.000 |
| engaged | distinct_scene_ratio | 1 | 1 | +0.000 |
| engaged | repeated_beat_ratio | 0.489 | 0.028 | -0.462 |
| engaged | distinct_nudge_ratio | 0.079 | 0.309 | +0.230 |
| engaged | consecutive_identical_nudges | 13.200 | 0.000 | -13.200 |
| engaged | theme_coverage | 1 | 1 | +0.000 |
| engaged | trait_delta_sum | 2.750 | 2.193 | -0.557 |
| engaged | goal_progress_mean | 0.325 | 0.645 | +0.320 |
| engaged | lessons | 15 | 14.400 | -0.600 |
| engaged | distinct_lesson_ratio | 0.200 | 0.740 | +0.539 |
| engaged | final_stress | 0.000 | 0.000 | +0.000 |
| engaged | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| engaged | blocked | 0.000 | 0.000 | +0.000 |
| engaged | max_intensity_when_stressed | 0.000 | 0.000 | +0.000 |
| engaged | vision_flags | 0.000 | 0.400 | +0.400 |
| avoidant | conv_final | 0.462 | 0.019 | -0.443 |
| avoidant | conv_max | 0.479 | 0.036 | -0.443 |
| avoidant | conv_auc | 0.333 | 0.012 | -0.321 |
| avoidant | conv_monotonicity | 0.893 | 0.787 | -0.107 |
| avoidant | conv_dips | 1.200 | 2.200 | +1.000 |
| avoidant | conv_recovery_rate | 0.700 | 0.467 | -0.233 |
| avoidant | earned_corr | -0.066 | 0.635 | +0.702 |
| avoidant | earned_gap | -0.009 | 0.000 | +0.009 |
| avoidant | success_rate | 0.613 | 0.000 | -0.613 |
| avoidant | journey_distinct_stages | 3.200 | 6 | +2.800 |
| avoidant | journey_max_stage | 2.200 | 5 | +2.800 |
| avoidant | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| avoidant | journey_in_order | 0.600 | 0.000 | -0.600 |
| avoidant | journey_max_backjump | 0.400 | 1 | +0.600 |
| avoidant | distinct_locations | 1 | 6 | +5.000 |
| avoidant | distinct_scene_ratio | 1 | 1 | +0.000 |
| avoidant | repeated_beat_ratio | 0.425 | 0.014 | -0.411 |
| avoidant | distinct_nudge_ratio | 0.127 | 0.278 | +0.152 |
| avoidant | consecutive_identical_nudges | 10.200 | 0.000 | -10.200 |
| avoidant | theme_coverage | 1 | 1 | +0.000 |
| avoidant | trait_delta_sum | 1.986 | 0.506 | -1.480 |
| avoidant | goal_progress_mean | 0.395 | 0.061 | -0.334 |
| avoidant | lessons | 8 | 5.200 | -2.800 |
| avoidant | distinct_lesson_ratio | 0.593 | 0.900 | +0.307 |
| avoidant | final_stress | 0.280 | 0.647 | +0.367 |
| avoidant | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| avoidant | blocked | 0.000 | 0.000 | +0.000 |
| avoidant | max_intensity_when_stressed | 0.314 | 0.320 | +0.006 |
| avoidant | vision_flags | 0.000 | 0.000 | +0.000 |
| mixed | conv_final | 0.426 | 0.327 | -0.099 |
| mixed | conv_max | 0.439 | 0.330 | -0.109 |
| mixed | conv_auc | 0.313 | 0.166 | -0.147 |
| mixed | conv_monotonicity | 0.867 | 0.827 | -0.040 |
| mixed | conv_dips | 1.400 | 2.200 | +0.800 |
| mixed | conv_recovery_rate | 0.600 | 0.767 | +0.167 |
| mixed | earned_corr | 0.108 | 0.814 | +0.706 |
| mixed | earned_gap | 0.005 | 0.058 | +0.054 |
| mixed | success_rate | 0.812 | 0.475 | -0.338 |
| mixed | journey_distinct_stages | 3.200 | 6 | +2.800 |
| mixed | journey_max_stage | 2.200 | 5 | +2.800 |
| mixed | journey_reached_ordeal | 0.200 | 1 | +0.800 |
| mixed | journey_in_order | 0.600 | 0.000 | -0.600 |
| mixed | journey_max_backjump | 0.400 | 1 | +0.600 |
| mixed | distinct_locations | 1 | 6 | +5.000 |
| mixed | distinct_scene_ratio | 1 | 1 | +0.000 |
| mixed | repeated_beat_ratio | 0.478 | 0.023 | -0.455 |
| mixed | distinct_nudge_ratio | 0.096 | 0.276 | +0.180 |
| mixed | consecutive_identical_nudges | 9.800 | 0.000 | -9.800 |
| mixed | theme_coverage | 1 | 1 | +0.000 |
| mixed | trait_delta_sum | 2.502 | 1.492 | -1.010 |
| mixed | goal_progress_mean | 0.380 | 0.399 | +0.019 |
| mixed | lessons | 13 | 10 | -3.000 |
| mixed | distinct_lesson_ratio | 0.385 | 0.967 | +0.581 |
| mixed | final_stress | 0.085 | 0.000 | -0.085 |
| mixed | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| mixed | blocked | 0.000 | 0.000 | +0.000 |
| mixed | max_intensity_when_stressed | 0.080 | 0.000 | -0.080 |
| mixed | vision_flags | 0.000 | 0.400 | +0.400 |
| autonomous | conv_final | 0.434 | 0.418 | -0.016 |
| autonomous | conv_max | 0.450 | 0.436 | -0.015 |
| autonomous | conv_auc | 0.315 | 0.247 | -0.068 |
| autonomous | conv_monotonicity | 0.840 | 0.800 | -0.040 |
| autonomous | conv_dips | 2 | 2.200 | +0.200 |
| autonomous | conv_recovery_rate | 0.750 | 0.800 | +0.050 |
| autonomous | earned_corr | 0.153 | 0.367 | +0.213 |
| autonomous | earned_gap | 0.006 | 0.016 | +0.011 |
| autonomous | success_rate | 0.812 | 0.725 | -0.088 |
| autonomous | journey_distinct_stages | 3.400 | 6 | +2.600 |
| autonomous | journey_max_stage | 2.400 | 5 | +2.600 |
| autonomous | journey_reached_ordeal | 0.400 | 1 | +0.600 |
| autonomous | journey_in_order | 0.600 | 0.000 | -0.600 |
| autonomous | journey_max_backjump | 0.400 | 1 | +0.600 |
| autonomous | distinct_locations | 1 | 6 | +5.000 |
| autonomous | distinct_scene_ratio | 1 | 1 | +0.000 |
| autonomous | repeated_beat_ratio | 0.477 | 0.032 | -0.445 |
| autonomous | distinct_nudge_ratio | 0.093 | 0.319 | +0.227 |
| autonomous | consecutive_identical_nudges | 11.400 | 0.000 | -11.400 |
| autonomous | theme_coverage | 1 | 1 | +0.000 |
| autonomous | trait_delta_sum | 2.599 | 1.781 | -0.817 |
| autonomous | goal_progress_mean | 0.340 | 0.522 | +0.182 |
| autonomous | lessons | 12.400 | 11.400 | -1.000 |
| autonomous | distinct_lesson_ratio | 0.368 | 0.821 | +0.453 |
| autonomous | final_stress | 0.150 | 0.000 | -0.150 |
| autonomous | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| autonomous | blocked | 0.000 | 0.000 | +0.000 |
| autonomous | max_intensity_when_stressed | 0.000 | 0.080 | +0.080 |
| autonomous | vision_flags | 0.000 | 0.000 | +0.000 |
| lighthouse | conv_final | 0.430 | 0.355 | -0.075 |
| lighthouse | conv_max | 0.460 | 0.376 | -0.084 |
| lighthouse | conv_auc | 0.322 | 0.252 | -0.070 |
| lighthouse | conv_monotonicity | 0.840 | 0.733 | -0.107 |
| lighthouse | conv_dips | 1.400 | 1.800 | +0.400 |
| lighthouse | conv_recovery_rate | 0.550 | 0.100 | -0.450 |
| lighthouse | earned_corr | 0.584 | 0.641 | +0.057 |
| lighthouse | earned_gap | 0.027 | 0.028 | +0.001 |
| lighthouse | success_rate | 0.250 | 0.588 | +0.338 |
| lighthouse | journey_distinct_stages | 3.600 | 6 | +2.400 |
| lighthouse | journey_max_stage | 2.600 | 5 | +2.400 |
| lighthouse | journey_reached_ordeal | 0.600 | 1 | +0.400 |
| lighthouse | journey_in_order | 0.400 | 0.200 | -0.200 |
| lighthouse | journey_max_backjump | 0.600 | 0.800 | +0.200 |
| lighthouse | distinct_locations | 1 | 6 | +5.000 |
| lighthouse | distinct_scene_ratio | 1 | 1 | +0.000 |
| lighthouse | repeated_beat_ratio | 0.437 | 0.029 | -0.408 |
| lighthouse | distinct_nudge_ratio | 0.108 | 0.280 | +0.171 |
| lighthouse | consecutive_identical_nudges | 12.200 | 0.000 | -12.200 |
| lighthouse | theme_coverage | 1 | 1 | +0.000 |
| lighthouse | trait_delta_sum | 1.366 | 1.833 | +0.467 |
| lighthouse | goal_progress_mean | 0.385 | 0.485 | +0.100 |
| lighthouse | lessons | 5.800 | 9.800 | +4.000 |
| lighthouse | distinct_lesson_ratio | 0.648 | 0.897 | +0.249 |
| lighthouse | final_stress | 0.577 | 0.000 | -0.577 |
| lighthouse | forbidden_scenes | 0.000 | 0.000 | +0.000 |
| lighthouse | blocked | 0.000 | 0.000 | +0.000 |
| lighthouse | max_intensity_when_stressed | 0.080 | 0.000 | -0.080 |
| lighthouse | vision_flags | 0.000 | 0.200 | +0.200 |

| rubric | before | after |
|---|---|---|
| engaged final convergence > avoidant | FAIL (-0.057) | PASS (0.490) |
| earnedness: corr(success, dConv) pooled over all runs | FAIL (0.093) | PASS (0.612) |
| journey reaches ordeal or beyond within 16 cycles (engaged, every seed) | FAIL (0.000) | PASS (1) |
| consecutive identical nudges (worst run) | FAIL (15) | PASS (0.000) |
| repeated-beat ratio (worst profile mean) | FAIL (0.489) | PASS (0.032) |
| theme coverage (worst run) | PASS (1) | PASS (1) |
| forbidden-theme scenes (worst run) | PASS (0.000) | PASS (0.000) |
