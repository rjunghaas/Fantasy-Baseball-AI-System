# Trend Analyzer Eval

## Purpose

This eval verifies the Trend Analyzer's assignment of `trend_signal`  and `action_flag` for my roster and free agents as well as `trend_signal` for my opponent's roster.  We will look at the assignment of the Trend Analyzer in the Sunday and Wednesday runs and compare those ratings to the player's final production in the week to assess.

---

## Eval Types

The eval will run against the Sunday and the Wednesday Trend Analyzer runs and apply the same methodology to each.  

---

## Inputs

- Trend Analyzer Sunday outputs:  `trend_analyzer_roster_YYYYMMDD.csv`, `trend_analyzer_opponent_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`
- Trend Analyzer Wednesday outputs:  `trend_analyzer_roster_YYYYMMDD.csv`, `trend_analyzer_opponent_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`
- Actual statistics for players for matchup week:  `pybaseball_roster_YYYYMMDD.parquet`, `pybaseball_opponent_roster_YYYYMMDD.parquet`, and `pybaseball_fa_YYYYMMDD.parquet`

---

## Ground Truth

The ground truth will be the actual player statistics derived from the following week's Sunday data pull.  We will use this to get the actual statistics produced by each player on my roster, opponent's roster, and free agent pool, then compare these to the assignments for these players in the Trend Analyzer output for Sunday and Wednesday for the evaluation.  "This week's ERA" = the 7d window from the next Sunday's parquet; "Last week's ERA" = the 7d window from the current Sunday's parquet

---

## Scoring Method

**Trend_Signal**

For batters, count how many categories from this week equalled or exceeded the category total for the prior week.  Call this count `current_week_category_improvement`.  Do same for pitchers except for ERA and WHIP because a lower value is better, count it as an improvement if the current week's ERA or WHIP is LOWER than last week's respective value.  For pitchers with `era_inflation` or `era_deflation_risk`, apply only the ERA comparison rules. Skip the current_week_category_improvement count for those players.

If player has trend_signal of hot and `current_week_category_improvement` > 3, result is "yes".
If player has trend_signal of hot and `current_week_category_improvement` < 3, result is "no".
If player has trend_signal of hot and `current_week_category_improvement` = 3, result is "n/a".

If player has trend_signal of cold and `current_week_category_improvement` < 2, result is "yes".
If player has trend_signal of cold and `current_week_category_improvement` > 2, result is "no".
If player has trend_signal of cold and `current_week_category_improvement` = 2, result is "n/a".

If player has trend_signal of positive_regression and `current_week_category_improvement` > 2, result is "yes".
If player has trend_signal of positive_regression and `current_week_category_improvement` < 2, result is "no".
If player has trend_signal of positive_regression and `current_week_category_improvement` = 2, result is "n/a".

If player has trend_signal of negative_regression and `current_week_category_improvement` > 3, result is "no".
If player has trend_signal of negative_regression and `current_week_category_improvement` < 3, result is "yes".
If player has trend_signal of negative_regression and `current_week_category_improvement` = 3, result is "n/a".

If player has trend_signal of era_inflation and this week's era > last week's era, result is "yes".
If player has trend_signal of era_inflation and this week's era < last week's era, result is "no".
If player has trend_signal of era_inflation and this week's era = last week's era, result is "n/a".


If player has trend_signal of era_deflation_risk and this week's era > last week's era, result is "no".
If player has trend_signal of era_deflation_risk and this week's era < last week's era, result is "yes".
If player has trend_signal of era_deflation_risk and this week's era = last week's era, result is "n/a".

If player has trend_signal of velocity_drop, result is "n/a".


If player has trend_signal of insufficient_data, result is "n/a".


**Action_Flag**

This will only apply to my roster and free agents.  

If player has action_flag of drop_candidate and `current_week_category_improvement` > 2, result is "no".
If player has action_flag of drop_candidate and `current_week_category_improvement` < 2, result is "yes".
If player has action_flag of drop_candidate and `current_week_category_improvement` = 2, result is "n/a".

If player has action_flag of hold and `current_week_category_improvement` > 2, result is "yes".
If player has action_flag of hold and `current_week_category_improvement` < 2, result is "no".
If player has action_flag of hold and `current_week_category_improvement` = 2, result is "n/a".


If player has action_flag of neutral and `current_week_category_improvement` > 2, result is "yes".
If player has action_flag of neutral and `current_week_category_improvement` < 2, result is "no".
If player has action_flag of neutral and `current_week_category_improvement` = 2, result is "n/a".


---

## Pass/Fail Threshold

For Sunday eval, we should see a 60% "yes" rate for trend_signals and a 70% "yes" rate for the action_flags.

For Wednesday eval, we should see a 75% "yes" rate for trend_signals and an 85% "yes" rate for the action_flags.

---

## Known Limitations

- This eval will only look at the assignment of trend_signals and action_flags to players
- Later evals will look at the selection of free agents to target and the recommended transactions
- ench_today action flags are excluded from this eval — they require single-day performance data that isn't captured in the weekly parquet windows.

---

## How to Run

1. After the week ends, run eval_runner.py with the Sunday night data pull parquets as inputs.
2. The eval auto-loads the Trend Analyzer Sunday and Wednesday output CSVs (latest by date).
3. Ground truth is derived from the 7d window of pybaseball_roster_YYYYMMDD.parquet,
   pybaseball_opponent_roster_YYYYMMDD.parquet, and pybaseball_fa_YYYYMMDD.parquet
   from the next Sunday's data pull — this gives actual stats for the completed matchup week.
4. Record Sunday and Wednesday scores separately (trend_signal % correct, action_flag % correct).
5. Flag any players where the signal was "wrong" — these are the tuning targets.


---

## Example

The table below shows one row per player illustrating each scoring case. `ground_truth` is the `current_week_category_improvement` count for most players; for `era_inflation` / `era_deflation_risk` pitchers it shows the 7d ERA comparison instead (category count is skipped for those players per the scoring rules). `action_flag_correct` is n/a for opponent players since action flags only apply to my roster and free agents.

| player | stat_type | source | trend_signal | action_flag | ground_truth | trend_signal_correct | action_flag_correct |
|--------|-----------|--------|--------------|-------------|--------------|----------------------|---------------------|
| Randy Arozarena | batter | roster | hot | hold | improvement = 4 (R, HR, RBI, SB improved; OBP did not) | yes (hot + >3) | yes (hold + >2) |
| José Caballero | batter | roster | cold | drop_candidate | improvement = 1 (only SB improved) | yes (cold + <2) | yes (drop_candidate + <2) |
| Konnor Griffin | batter | roster | positive_regression | neutral | improvement = 3 (R, HR, OBP improved; RBI, SB did not) | yes (positive_regression + >2) | yes (neutral + >2) |
| Kyle Harrison | pitcher | roster | era_inflation | drop_candidate | 7d ERA this week: 6.17 vs prior 7d ERA: 3.01 → ERA rose | yes (era_inflation + ERA up) | n/a (category count skipped for era signal pitchers; action_flag not scored) |
| Opponent batter | batter | opponent | hot | — | improvement = 3 (exactly 3 of 5 categories) | n/a (hot + =3) | n/a (opponent — no action_flag) |
| Tarik Skubal | pitcher | roster | velocity_drop | hold | — | n/a (velocity_drop always n/a) | yes (hold + improvement = 4 > 2) |

