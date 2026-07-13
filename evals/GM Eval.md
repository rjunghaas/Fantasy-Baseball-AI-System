# GM Eval

## Purpose

This eval is for the GM Agent which synthesizes recommendations from the other 4 agents (Matchup, Roster Management, Future Predictor, and Trend Analyzer).  We are going to evaluate 3 things:  a) the category targets on Sunday and Wednesday runs for accuracy against actual results, b) the recommendation to drop roster players, and c) the recommendation to add players from the free agent pool.

## Eval Type

All 3 of these evals are calibration evals to ensure the recommendations are sufficiently tuned.

## Inputs

**Sunday categories**
1. decisions_YYYYMMDD.json (from previous Sunday)

**Wednesday categories**
1. decisions_YYYYMMDD.json (from previous Wednesday)

**Drop Candidates**
1. decisions_YYYYMMDD.json
2. get_batter_contribution_score_14d(), get_pitcher_contribution_score_14(), and get_roster_slot_efficiency() from data_client.py

**Add Candidates**
1. decisions_YYYYMMDD.json
2. get_batter_contribution_score_14d(), get_pitcher_contribution_score_14(), and get_roster_slot_efficiency() from data_client.py

## Ground Truth

**Sunday categories**
1. scoreboard_history.parquet (from end of current week)

**Wednesday categories**
1. scoreboard_history.parquet (from end of current week)

**Drop Candidates**
1. pybaseball_roster_YYYYMMDD.parquet, pybaseball_fa_YYYYMMDD.parquet (all from end of current week)

**Add Candidates**
1. pybaseball_roster_YYYYMMDD.parquet, pybaseball_fa_YYYYMMDD.parquet (all from end of current week)

## Scoring Method

**Sunday categories**
1. Get category_targets and categories_punted from previous Sunday's run
2. Get final results of matchup from scoreboard_history.parquet

If category is in `category_targets` and I won the category in the final matchup scoreboard, result = "yes"
If category is in `category_targets` and I lost by less than 10% of my opponent's score, result = "yes"
If category is in `category_targets` and I tied with my opponent, result = "yes"
If category is in `category_targets` and I lost by more than 10% of my opponent's score, result = "no"

If category is in `categories_punted` and I won or tied the category in the final matchup scoreboard, result = "no"
If category is in `categories_punted` and I lost the category by more than 20% of my opponent's score, result = "yes"
If category is in `categories_punted` and I lost the category by less than 20% of my opponent's score, result is "no"

**Wednesday categories**
1. Get category_targets and categories_punted from previous Wednesday's run
2. Get final results of matchup from scoreboard_history.parquet

If category is in `category_targets` and I won the category in the final matchup scoreboard, result = "yes"
If category is in `category_targets` and I lost by less than 5% of my opponent's score, result = "yes"
If category is in `category_targets` and I tied with my opponent, result = "yes"
If category is in `category_targets` and I lost by more than 5% of my opponent's score, result = "no"

If category is in `categories_punted` and I won or tied the category in the final matchup scoreboard, result = "no"
If category is in `categories_punted` and I lost the category by more than 30% of my opponent's score, result = "yes"
If category is in `categories_punted` and I lost the category by less than 30% of my opponent's score, result is "no"

**Drop Candidates**
1. Get all of the recommended_drops from the Sunday and Wednesday runs along with the total_efficiency_14d at the time of the recommendation.
2. Get their end of week stats from pybaseball_roster_YYYYMMDD.parquet and pybaseball_fa_YYYYMMDD.parquet (if they were dropped)
3. Re-calculate their total_effiiciency_14d by calling get_batter_contribution_score_14d(), or get_pitcher_contribution_score_14() as appropriate and then and get_roster_slot_efficiency() from data_client.py.  This will give recalc_total_efficiency_14d.

If historical total_efficiency_14d >= recalc_total_efficiency_14d, result is "yes"
If historical total_efficiency_14d < 0.8 * recalc_total_efficiency_14d, result is "no"
Else result is "n/a"

**Add Candidates**
1. Get all of the recommended_adds from the Sunday and Wednesday runs along with the total_efficiency_14d at the time of the recommendation.
2. Select free agents from roster_management_batter_fa_output_YYYYMMDD.csv and roster_management_pitcher_fa_output_YYYYMMDD.csv filtered to only those with a positive contribution_delta in at least one target category in either Sunday or Wednesday GM outputs along with the total_efficiency_14d at the time of the recommendation.
3. Get their end of week stats from pybaseball_roster_YYYYMMDD.parquet and pybaseball_fa_YYYYMMDD.parquet
4. Re-calculate their total_effiiciency_14d by calling get_batter_contribution_score_14d(), or get_pitcher_contribution_score_14() as appropriate and then and get_roster_slot_efficiency() from data_client.py.  This will give recalc_total_efficiency_14d.
5. For the recommended add, check target category execution: using the recalculated contribution deltas from step 4, check if the added player's recalc contribution delta is > 0 in at least one of the GM's target categories for that week.

If the added player has a positive contribution delta in at least one target category, `target_category_execution` = "yes". Else `target_category_execution` = "no".

6. Count number of filtered free agents' recalc_total_efficiency_14d > recommended_adds players' recalc_total_efficiency_14d and call this count `fa_better_total_efficiency`.

If `fa_better_total_efficiency` > 2, `counterfactual_result` is "no"
If `fa_better_total_efficiency` <= 2, `counterfactual_result` is "yes"

7. Overall add result: "yes" if both `target_category_execution` = "yes" and `counterfactual_result` = "yes". "no" if either is "no".

## Pass/Fail Threshold

Sunday recommendations should be correct 80% of the time
Wednesday recommendations should be correct 90% of the time
Drop candidates should be correct 90% of the time
Add candidates should be correct 90% of the time

## Known Limitations

- The GM agent is not directly computing total_efficiency_14d, but is getting this from the Roster Management Skill and then applying the weekly_adjustment_factor from Future Predictor.  We are not doing an eval on Future Predictor.
- Dropped players who get picked up by another team mid-week will not appear in either parquet by the following Sunday — these should be marked n/a.
- Percentage margins behave differently for rate stats.  May refine this to handle rate and counting stats differently in future iterations.
- Small sample sizes for Drop/Add thresholds


## How to Run

1. After the week ends, run eval_runner.py with the Sunday night data pull parquets as inputs.
2. The eval auto-loads decisions_YYYYMMDD.json for both Sunday and Wednesday runs (latest by date prefix for each day).
3. Category evals derive ground truth from scoreboard_history.parquet — the just-completed week's final category scores for the current matchup.
4. Drop eval: load historical total_efficiency_14d from decisions_YYYYMMDD.json, then recalculate using 7d window stats from the next Sunday's pybaseball parquets.
5. Add eval: load the recommended add from decisions_YYYYMMDD.json, build the filtered FA comparison pool from the Roster Management FA output CSVs, recalculate total_efficiency_14d for all players using next Sunday's parquets, check target category execution and counterfactual ranking.
6. Record pass/fail for each sub-eval vs thresholds (Sunday 80%, Wednesday 90%, Drop 90%, Add 90%).
7. For manual inspection: review category misses to identify which target/punt classifications need tuning; review any "no" on Drop or Add with the player's actual stats to determine if the threshold or scoring logic needs adjustment.

## Example

### Sunday Category Eval

**decisions_YYYYMMDD.json (previous Sunday):**
- category_targets: ["OBP", "R", "RBI", "SB", "HR"]
- categories_punted: ["ERA", "WHIP", "K", "W", "SV"]

**Final scoreboard (from scoreboard_history.parquet):**
| category | my_score | opp_score | winner | margin | result |
|----------|----------|-----------|--------|--------|--------|
| OBP (target) | 0.342 | 0.308 | me | +11% | yes |
| R (target) | 38 | 32 | me | +19% | yes |
| RBI (target) | 41 | 38 | me | +8% | yes |
| SB (target) | 5 | 3 | me | +67% | yes |
| HR (target) | 9 | 12 | opp | −25% | no (lost by >10%) |
| ERA (punted) | 3.85 | 3.20 | opp | −20% | yes (lost by ≥20%) |
| WHIP (punted) | 1.22 | 1.08 | opp | −13% | no (lost by <20%) |
| K (punted) | 48 | 55 | opp | −13% | no (lost by <20%) |
| W (punted) | 5 | 7 | opp | −29% | yes (lost by >20%) |
| SV (punted) | 3 | 5 | opp | −40% | yes (lost by >20%) |

**Sunday category score: 7/10 = 70% → below 80% threshold → fail**
Tuning note: HR target missed — review whether HR should have been punted given the opponent's power profile.

---

### Drop Candidate Eval

**Recommended drop: José Caballero**
- historical_total_efficiency_14d at time of recommendation: −2.3
- Recalculated using next Sunday's 7d stats: recalc_total_efficiency_14d = −3.1

Rule check:
- historical (−2.3) >= recalc (−3.1)? Yes → **result: "yes"** (player got worse after drop — drop was correct)

---

### Add Candidate Eval

**Recommended add: Evan Carter**
- category_targets at time of recommendation: ["OBP", "R", "RBI", "SB", "HR"]
- Recalculated contribution deltas from next Sunday's 7d stats:
  - R_delta = +0.8, HR_delta = +0.3, RBI_delta = +0.5, SB_delta = +1.1, OBP_delta = +0.02
- recalc_total_efficiency_14d = 3.6

**Target category execution:**
- Positive delta in at least one target category? R(+0.8), HR(+0.3), RBI(+0.5), SB(+1.1), OBP(+0.02) → all positive → `target_category_execution` = "yes"

**Counterfactual check (filtered FA pool — FAs with positive delta in at least one target category):**
| fa_player | recalc_total_efficiency_14d |
|-----------|----------------------------|
| Player A | 4.8 |
| Player B | 4.1 |
| Player C | 3.9 |
| Player D | 3.4 |
| Player E | 2.7 |

FAs with recalc > Evan Carter's 3.6: Player A (4.8), Player B (4.1), Player C (3.9) → `fa_better_total_efficiency` = 3

- fa_better_total_efficiency (3) > 2 → `counterfactual_result` = "no"

**Overall add result: "no"** (target category execution passed but counterfactual failed — there were better FA options available in the target categories)
Tuning note: review why RM/GM ranked Evan Carter above Players A-C; likely a recency bias in the 14d window.
