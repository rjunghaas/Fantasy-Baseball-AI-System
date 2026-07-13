# Trend Analyzer — Skill Reference

## Purpose
The Trend Analyzer agent connects to Parquet data for players on my fantasy baseball roster using data_client.py. It evaluates recent performance trends against season baselines to identify players who are slumping, hot, or due for statistical regression. It produces a ranked output for the GM agent to act on. It does not consider upcoming matchups, park factors, roster slot efficiency, or opponent strategy — those are handled by other agents.

## Trigger Conditions
- Runs on Sunday nights and Wednesday nights as part of the full agent pipeline
- Parquet files `pybaseball_roster_YYYYMMDD.parquet`, `pybaseball_opponent_roster_YYYYMMDD.parquet` and `pybaseball_fa_YYYYMMDD.parquet` must exist in the `data/` directory for this agent to run
- Players with no 14d stats (injured, recently called up) will be absent from output — GM should note any missing roster players.
- The agent uses data_client.py to load pre-computed trend signals — it does not query Parquet directly

## Workflow

1. Load data:  Call `get_batter_trend_signals("roster")`, `get_batter_trend_signals("opponent")`, `get_batter_trend_signals("fa")`, `get_pitcher_trend_signals("roster")`, `get_pitcher_trend_signals("opponent")`, and `get_pitcher_trend_signals("fa")`
2. Apply Decision Rules below to each of 3 sets of players (my roster, opponent's roster, free agent pool)
3. Apply the no-drop override: if `no_drop = true` and `action_flag = drop_candidate`, override to `hold` and update notes accordingly.
4. Populate `key_evidence` and `notes` for each player.
5. Produce the output files: `trend_analyzer_roster_YYYYMMDD.csv`, `trend_analyzer_opponent_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`.  `trend_analyzer_roster_YYYYMMDD.csv` should be ordered by: `drop_candidate` first, then `bench_today`, then `hold`, then `neutral`. Within each group, order by `recommendation_strength` descending.  All players in `trend_analyzer_fa_YYYYMMDD.csv` have action_flag = add_candidate — pre-filtered by data_client, order by `recommendation_strength` descending.  `trend_analyzer_opponent_YYYYMMDD.csv` should be ordered by `trend_signal`, then by `recommendation_strength`.

## Output Schema

Output Schema for `trend_analyzer_roster_YYYYMMDD.csv` and `trend_analyzer_fa_YYYYMMDD.csv`

| Column | Type | Description |
|--------|------|-------------|
| `player_id` | string | Player's unique ID |
| `player_name` | string | Player's full name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, CI, OF, P, BN, IL) |
| `trend_signal` | string | Signal from data_client.py: hot / cold / positive_regression / negative_regression / era_inflation / era_deflation_risk / velocity_drop / insufficient_data |
| `recommendation_strength` | int or null | 1–3 based on consecutive data pulls confirming the signal. Null if insufficient_data. |
| `action_flag` | string | Agent recommendation: `drop_candidate` / `bench_today` / `hold` / `neutral` |
| `key_evidence` | string or null | Short summary of the stat(s) driving the signal. e.g. `"babip_14d=0.243, xwoba_season=0.390"`. Null if insufficient_data. |
| `notes` | string | One sentence for the GM explaining the signal and any overrides applied. |

Output schema for `trend_analyzer_opponent_YYYYMMDD.csv`

| Column | Type | Description |
|--------|------|-------------|
| `player_id` | string | Player's unique ID |
| `player_name` | string | Player's full name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, CI, OF, P, BN, IL) |
| `trend_signal` | string | Signal from data_client.py: hot / cold / positive_regression / negative_regression / era_inflation / era_deflation_risk / velocity_drop / insufficient_data |
| `recommendation_strength` | int or null | 1–3 based on consecutive data pulls confirming the signal. Null if insufficient_data. |
| `key_evidence` | string or null | Short summary of the stat(s) driving the signal. e.g. `"babip_14d=0.243, xwoba_season=0.390"`. Null if insufficient_data. |
| `notes` | string | One sentence for the GM explaining the signal and any overrides applied. |


## Decision Rules

### Batters

**Strength of Signal Matrix**

Apply the following table to determine trend_signal for each batter and the recommendation_strength:

| **Signal** | **Strength 1** | **Strength 2** | **Strength 3** |
| :--- | :--- | :--- | :--- |
| **positive_regression** | babip_14d < 0.250, xwoba > 0.320 | babip_14d < 0.235, xwoba > 0.330 | babip_14d < 0.220, xwoba > 0.340 |
| **hot** | obp_14d > season + 0.040 | obp_14d > season + 0.060 | obp_14d > season + 0.080 |
| **negative_regression** | babip_14d > 0.370, xwoba_season < 0.320 | babip_14d > 0.395, xwoba_season < 0.310 | babip_14d > 0.410, xwoba_season < 0.300 |
| **cold** | obp_14d < season - 0.040 | obp_14d < season - 0.060 | obp_14d < season - 0.080, xwoba_14d < xwoba_season - 0.020 |

### Pitchers

**Strength of Signal Matrix**

Apply the following table to determine trend_signal for each pitcher and the recommendation_strength:

 **Signal** | **Strength 1** | **Strength 2** | **Strength 3** |
| :--- | :--- | :--- | :--- |
| **velocity_drop** | -1.5 to -2.5 mph | -2.5 to -3.5 mph | > -3.5 mph |
| **era_inflation** | era_14d > season + 1.50, fip gap > 0.5 | era gap > 2.0, fip gap > 1.0 | era gap > 3.0, fip gap > 1.5 |
| **era_deflation_risk** | era_14d < season - 1.50, fip gap > 0.5 | era gap > 2.0, fip gap > 1.0 | era gap > 3.0, fip gap > 1.5 |


**Recommendation Strength Factors**
A signal of `hot` can have its strength increased by 1 degree if hard_hit_pct_14d and barrel_pct_14d are both higher than hard_hit_pct_season and barrel_pct_season, respectively.  Strength is capped at a min of 0 and max of 3

A signal of `cold` can have its strength increased by 1 degree if hard_hit_pct_14d and barrel_pct_14d are both lower than hard_hit_pct_season and barrel_pct_season, respectively.  Strength is capped at a min of 0 and a max of 3


**Action_Flag Matrix**
Based on the signal and its strength rating, apply the action_flag to the player as follows:

| **Signal**                          | **Roster**     | **FA**        |
|-------------------------------------|----------------|---------------|
| positive_regression (any strength)  | hold           | add_candidate |
| hot (strength ≥ 2)                  | hold           | add_candidate |
| hot (strength 1)                    | neutral        | add_candidate |
| negative_regression + no_drop=false | drop_candidate | —             |
| cold (strength 1)                   | hold           | —             |
| cold (strength ≥ 2) + no_drop=false | bench_today    | —             |
| velocity_drop (any strength)        | bench_today    | —             |
| era_inflation                       | hold           | add_candidate |
| era_deflation_risk                  | hold           | —             |
| neutral                             | neutral        | -             |


### No-Drop Override

After all action_flags are assigned:
- If `no_drop = true` AND `action_flag = drop_candidate` → override `action_flag` to `hold`
- Append to notes: "Drop flagged but player is no-drop — GM should consider trade or streaming an alternative instead."

## What This Skill Does NOT Do

- Does not consider upcoming pitching matchups or park factors (Future Predictor)
- Does not consider roster slot efficiency or position eligibility (Roster Construction agent)
- Does not consider opponent strategy or category targeting (Matchup agent)
- Does not make final transaction decisions — those are the GM agent's responsibility
- Does not evaluate free agent alternatives — it only evaluates current roster players

## Example Output

| player_name | position | trend_signal | action_flag | recommendation_strength | key_evidence | notes |
|---|---|---|---|---|---|---|
| Konnor Griffin | OF | negative_regression | drop_candidate | 1 | babip_14d=0.307, xwoba_season=0.303 | Overperforming underlying talent — production likely to decline. Consider drop or stream replacement. |
| JJ Bleday | OF | positive_regression | hold | 1 | babip_14d=0.243, xwoba_season=0.390 | Underperforming underlying talent — expect bounce-back. Hold and monitor. |
| Yoshinobu Yamamoto | P | era_deflation_risk | hold | 1 | era_14d=0.68, era_season=2.68, fip_season=3.43 | ERA looks strong but FIP signals regression risk — defer to Future Predictor for matchup context before starting. |
| Tarik Skubal | P | neutral | neutral | null | null | No meaningful trend signal. |
