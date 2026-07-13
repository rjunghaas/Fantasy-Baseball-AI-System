# Roster Management Eval

## Purpose

This script will test the total_efficiency and category delta scores for players across the season and 14d windows by the Roster Management Skill.  We want to ensure these are accurate because they are the basis for the recommendations for dropping and adding players.  Our eval for total_efficiency_14d will be tighter than the category evaluations because it is the basis for identifying drop_candidates on my roster and free agents to potentially add, so it needs to be quite robust.

## Eval Type

**Calibration Eval**
This eval is going to assess the scores that the Roster Management eval generates for players on my roster and the free agent pool.

## Inputs

Data produced by the previous run of the Roster Management Skill:  `roster_management_batter_output_YYYYMMDD.csv`, `roster_management_pitcher_output_YYYYMMDD.csv`, `roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`

## Ground Truth

Data from the following Sunday's data pull will show the final category outputs by each player that we will evaluate the total_efficiency and category delta scores:  `pybaseball_roster_YYYYMMDD.parquet` and `pybaseball_fa_YYYYMMDD.parquet`.

## Scoring Method

**Batter evaluations** 

1. Get the average production for the week for runs, home runs, RBI, SB, and OBP across all batters.
2. Get weekly production for runs, home runs, RBI, SB, and OBP for the batter being evaluated.
3. Obtain their category deltas and total_efficiency score for season and 14d for every batter being evaluated.
4. Calculate expected production per category for each batter:  expected_season = avg_weekly_category_score + player_category_delta_season, expected_14d = avg_weekly_category_score + player_category_delta_14d

If (0.8 * expected_season <= actual_season <= 1.2 * expected_season), result is "yes".  If not, then result is "no"

If (0.7 * expected_14d <= actual_14d <= 1.3 * expected_14d), result is "yes". If not, the result is "no"

5. Evaluate total_efficiency_14d for each batter using the following steps:

   a. Back-calculate the scarcity_factor from the RM output (it is not a standalone column):
      `scarcity_factor = total_efficiency_14d / (R_delta_14d + HR_delta_14d + RBI_delta_14d + SB_delta_14d + OBP_delta_14d)`

   b. Compute actual weekly deltas from the 7d window of the next Sunday's parquet (no doubling needed — 7d stats are already on a weekly scale, matching the ÷2 normalization applied in data_client.py):
      `actual_delta_R   = actual_7d_R   - avg_weekly_league_R`
      `actual_delta_HR  = actual_7d_HR  - avg_weekly_league_HR`
      `actual_delta_RBI = actual_7d_RBI - avg_weekly_league_RBI`
      `actual_delta_SB  = actual_7d_SB  - avg_weekly_league_SB`
      `actual_delta_OBP = actual_7d_OBP - avg_weekly_league_OBP`

   c. Compute actual_total_efficiency_14d:
      `actual_total_efficiency_14d = scarcity_factor × (actual_delta_R + actual_delta_HR + actual_delta_RBI + actual_delta_SB + actual_delta_OBP)`

   d. Compare against the RM output score:
      If (0.9 * total_efficiency_14d <= actual_total_efficiency_14d <= 1.1 * total_efficiency_14d), result is "yes". Else, result is "no".

6. Output the top 5 batters who had the largest delta between their actual total_efficiency_14d and their total_efficiency_14 along with their category deltas so that I can manually inspect and determine if adjustments are needed for the Skill.

**Pitcher evaluations**

1. Get the average product for the week for wins, saves, strikeouts, ERA, and WHIP across all pitchers.
2. Get the weekly production for wins, saves, strikeouts, ERA, and WHIP across the pitchers being evaluated.
3. Obtain the category deltas and total_efficiency score for season and 14d for every pitcher being evaluated.
4. Calculate expected production per category for each pitcher:  expected = avg_weekly_category_score + player_category_delta_season

For wins, saves, and strikeouts (higher is better):
   `expected_season = avg_weekly_category_score + player_category_delta_season`
   `expected_14d    = avg_weekly_category_score + player_category_delta_14d`

   If (0.8 * expected_season <= actual <= 1.2 * expected_season), result is "yes". If not, then result is "no".
   If (0.7 * expected_14d <= actual <= 1.3 * expected_14d), result is "yes". If not, then result is "no".

For ERA and WHIP (lower is better — ERA_delta and WHIP_delta are already inverted in data_client.py: positive delta = better than league average):
   `expected_ERA_season  = avg_weekly_ERA  - ERA_delta_season`
   `expected_ERA_14d     = avg_weekly_ERA  - ERA_delta_14d`
   `expected_WHIP_season = avg_weekly_WHIP - WHIP_delta_season`
   `expected_WHIP_14d    = avg_weekly_WHIP - WHIP_delta_14d`

   If (0.8 * expected_ERA_season <= actual_ERA <= 1.2 * expected_ERA_season), result is "yes". If not, then result is "no".
   If (0.7 * expected_ERA_14d <= actual_ERA <= 1.3 * expected_ERA_14d), result is "yes". If not, then result is "no".
   Apply same formula for WHIP.

5. Evaluate total_efficiency_14d for each pitcher using the following steps:

   a. Back-calculate the scarcity_factor from the RM output:
      `scarcity_factor = total_efficiency_14d / (W_delta_14d + SV_delta_14d + K_delta_14d + ERA_delta_14d + WHIP_delta_14d)`

   b. Compute actual weekly deltas from the 7d window of the next Sunday's parquet (no doubling needed):
      `actual_delta_W    = actual_7d_W    - avg_weekly_league_W`
      `actual_delta_SV   = actual_7d_SV   - avg_weekly_league_SV`
      `actual_delta_K    = actual_7d_K    - avg_weekly_league_K`
      `actual_delta_ERA  = avg_weekly_league_ERA  - actual_7d_ERA`   (inverted: lower ERA = positive delta)
      `actual_delta_WHIP = avg_weekly_league_WHIP - actual_7d_WHIP`  (inverted: lower WHIP = positive delta)

   c. Compute actual_total_efficiency_14d:
      `actual_total_efficiency_14d = scarcity_factor × (actual_delta_W + actual_delta_SV + actual_delta_K + actual_delta_ERA + actual_delta_WHIP)`

   d. Compare against the RM output score:
      If (0.9 * total_efficiency_14d <= actual_total_efficiency_14d <= 1.1 * total_efficiency_14d), result is "yes". Else, result is "no".

6. Output the top 5 pitchers who had the largest delta between their actual total_efficiency_14d and their total_efficiency_14 along with their category deltas so that I can manually inspect and determine if adjustments are needed for the Skill.

## Pass/Fail Threshold

For all batters in my roster and free agent pool, sum up total category "yes" scores over total batters evaluated.  Group ratio of "yes" scores to total batters by category and output category "yes" ratio.  Group further by separate each category into season and 14d "yes" ratios.  Passing should be 80% for all categories over both time ranges.

For all pitchers in my roster and free agent pool, sum up total category "yes" scores over total pitchers evaluated.  Group ratio of "yes" scores to total pitchers by category and output category "yes" ratio.  Group further by separate each category into season and 14d "yes" ratios.  Passing should be 80% for all categories over both time ranges.

For the total_efficiency_14d eval for both batters and pitchers, sum up the total_efficiency "yes" scores over total batters and pitchers evaluated.  Group the "yes" scores by batter and pitcher.  "Yes" ratio for batters and pitchers should be 90% each to pass.

## Known Limitations
- We are not going to evaluate total_efficiency_season as previously mentioned.

## How to Run

1. After the week ends, run eval_runner.py with the Sunday night data pull parquets as inputs.
2. The eval auto-loads the Roster Management output CSVs (latest by date).
3. Ground truth is derived from the 7d window of pybaseball_roster_YYYYMMDD.parquet and pybaseball_fa_YYYYMMDD.parquet
   from the next Sunday's data pull — this gives actual stats for the completed matchup week.
4. Flag the top 5 batters and pitchers where the signal was "wrong" — these are the tuning targets.

## Example

### Batter: Randy Arozarena (roster)

**RM output (from last Sunday's run):**
| R_delta_14d | HR_delta_14d | RBI_delta_14d | SB_delta_14d | OBP_delta_14d | total_efficiency_14d |
|-------------|--------------|---------------|--------------|---------------|----------------------|
| +0.90 | +0.50 | +0.40 | +0.80 | +0.03 | 3.90 |

**League weekly averages (from league_benchmarks):**
avg_R = 5.3, avg_HR = 1.1, avg_RBI = 5.5, avg_SB = 0.9, avg_OBP = 0.320

**Expected production (14d):**
expected_R = 5.3 + 0.90 = 6.20 | expected_HR = 1.1 + 0.50 = 1.60 | expected_RBI = 5.5 + 0.40 = 5.90 | expected_SB = 0.9 + 0.80 = 1.70 | expected_OBP = 0.320 + 0.03 = 0.350

**Actual 7d production (from next Sunday's parquet):**
actual_R = 6 | actual_HR = 2 | actual_RBI = 7 | actual_SB = 2 | actual_OBP = 0.366

**Category checks (14d, ±30% bounds):**
| category | expected_14d | actual | lower_bound (×0.7) | upper_bound (×1.3) | result |
|----------|-------------|--------|--------------------|--------------------|--------|
| R | 6.20 | 6 | 4.34 | 8.06 | yes |
| HR | 1.60 | 2 | 1.12 | 2.08 | yes |
| RBI | 5.90 | 7 | 4.13 | 7.67 | yes |
| SB | 1.70 | 2 | 1.19 | 2.21 | yes |
| OBP | 0.350 | 0.366 | 0.245 | 0.455 | yes |

**total_efficiency_14d check:**
- scarcity_factor = 3.90 / (0.90 + 0.50 + 0.40 + 0.80 + 0.03) = 3.90 / 2.63 = 1.48
- actual_delta_R=0.70, actual_delta_HR=0.90, actual_delta_RBI=1.50, actual_delta_SB=1.10, actual_delta_OBP=0.046
- actual_total_efficiency_14d = 1.48 × (0.70 + 0.90 + 1.50 + 1.10 + 0.046) = 1.48 × 4.25 = 6.29
- bounds: 0.9 × 3.90 = 3.51 / 1.1 × 3.90 = 4.29
- 6.29 > 4.29 → **no** (actual outperformed the 14d projection — RM was underestimating this player heading into the week; candidate for threshold review)

---

### Pitcher: Tarik Skubal (roster)

**RM output (from last Sunday's run):**
| W_delta_14d | SV_delta_14d | K_delta_14d | ERA_delta_14d | WHIP_delta_14d | total_efficiency_14d |
|-------------|--------------|-------------|---------------|----------------|----------------------|
| +0.20 | 0.00 | +2.10 | +0.80 | +0.30 | 4.80 |

**League weekly averages (from league_benchmarks):**
avg_W = 0.7, avg_SV = 0.5, avg_K = 8.2, avg_ERA = 4.20, avg_WHIP = 1.28

**Expected production (14d):**
- W: 0.7 + 0.20 = 0.90 | SV: 0.5 + 0.00 = 0.50 | K: 8.2 + 2.10 = 10.30
- ERA: 4.20 − 0.80 = 3.40 (inverted: subtract delta) | WHIP: 1.28 − 0.30 = 0.98

**Actual 7d production (from next Sunday's parquet):**
actual_W = 1 | actual_SV = 0 | actual_K = 11 | actual_ERA = 3.15 | actual_WHIP = 0.94

**Category checks (14d, ±30% bounds):**
| category | expected_14d | actual | lower_bound | upper_bound | result |
|----------|-------------|--------|-------------|-------------|--------|
| W | 0.90 | 1 | 0.63 | 1.17 | yes |
| SV | 0.50 | 0 | 0.35 | 0.65 | no |
| K | 10.30 | 11 | 7.21 | 13.39 | yes |
| ERA | 3.40 | 3.15 | 2.38 | 4.42 | yes |
| WHIP | 0.98 | 0.94 | 0.69 | 1.27 | yes |

**total_efficiency_14d check:**
- scarcity_factor = 4.80 / (0.20 + 0.00 + 2.10 + 0.80 + 0.30) = 4.80 / 3.40 = 1.41
- actual_delta_W=0.30, actual_delta_SV=−0.50, actual_delta_K=2.80, actual_delta_ERA=1.05, actual_delta_WHIP=0.34
- actual_total_efficiency_14d = 1.41 × (0.30 − 0.50 + 2.80 + 1.05 + 0.34) = 1.41 × 3.99 = 5.63
- bounds: 0.9 × 4.80 = 4.32 / 1.1 × 4.80 = 5.28
- 5.63 > 5.28 → **no** (slightly above upper bound — Skubal outperformed his 14d projection, driven by strong K and ERA; borderline miss)