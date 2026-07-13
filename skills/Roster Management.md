# Roster Management — Skill Reference

## Purpose
This skill will analyze my fantasy baseball team roster and the free agent pool to assess their contribution scores and roster slot efficiency and output a ranked table in CSV format that will be consumed by another agent for further decision making.  It will summarize the total efficiency that the player is contributing and make a recommendation that will be used by that other agent.

## Trigger Conditions
- Runs on Sunday nights and Wednesday nights as part of the full agent pipeline
- Parquet files `league_benchmarks.parquet`, `pybaseball_roster_YYYYMMDD.parquet` and `pybaseball_fa_YYYYMMDD.parquet` must exist in the `data/` directory for this agent to run
- The agent uses data_client.py to load pre-computed trend signals — it does not query Parquet directly

## Workflow
Steps 2–5 run twice: once with source="roster" and once with source="fa". The logic is identical; only the parquet source and no_drop handling differ. For roster players, player_id/name/position/no_drop come from `pybaseball_roster_YYYYMMDD.parquet`. For FA players, they come from `pybaseball_fa_YYYYMMDD.parquet`, and no_drop is always False.

1. Load data: call the following functions from data_client.py for each source ("roster" and "fa"):
    - `get_batter_contribution_scores(source)`, `get_batter_contribution_scores_14d(source)`
    - `get_pitcher_contribution_scores(source)`, `get_pitcher_contribution_scores_14d(source)`
    - `get_roster_slot_efficiency(source)`
2. For batters, take the output of get_batter_contribution_scores and pivot into a single row for each player
    - Get player_id, name, position (if multiple positions, create a comma-separated list), and no_drop from the relevant parquet (roster or FA)
    - Add columns R_delta_season, HR_delta_season, RBI_delta_season, SB_delta_season, OBP_delta_season from the player_id rows in get_batter_contribution_scores output
    - Take sum of each player's efficiency_scores (5 total, 1 per category) to produce total_efficiency_season
3. For get_batter_contribution_scores_14d:
    - Add more columns to each player's row from step 2
    - Get player's 14d deltas: R_delta_14d, HR_delta_14d, RBI_delta_14d, SB_delta_14d, OBP_delta_14d
    - Take sum of each player's efficiency_scores (5 total, 1 per category) to produce total_efficiency_14d
    - Add a column called recommendation (see Decision Rules)
4. For pitchers, take the output of get_pitcher_contribution_scores and pivot into a single row for each player
    - Get player_id, name, position (if multiple positions, create a comma-separated list), and no_drop from the relevant parquet (roster or FA)
    - Add columns W_delta_season, SV_delta_season, K_delta_season, ERA_delta_season, WHIP_delta_season from the player_id rows in get_pitcher_contribution_scores output
    - Take sum of each player's efficiency_scores (5 total, 1 per category) to produce total_efficiency_season
5. Repeat the same logic as step 4 for get_pitcher_contribution_scores_14d output:
    - Add more columns to each player's row from step 4
    - Get player's 14d deltas: W_delta_14d, SV_delta_14d, K_delta_14d, ERA_delta_14d, WHIP_delta_14d
    - Take sum of each player's efficiency_scores (5 total, 1 per category) to produce total_efficiency_14d
    - Add a column called recommendation (see Decision Rules)
6. Order roster batters by total_efficiency_14d descending and output as `roster_management_batter_output_YYYYMMDD.csv`
7. Order roster pitchers by total_efficiency_14d descending and output as `roster_management_pitcher_output_YYYYMMDD.csv`
8. Order FA batters by total_efficiency_14d descending and output as `roster_management_batter_fa_output_YYYYMMDD.csv`
9. Order FA pitchers by total_efficiency_14d descending and output as `roster_management_pitcher_fa_output_YYYYMMDD.csv`

## Output Schema
All four output files share the same column structure. The roster files and FA files are identical in schema — the only difference is the population evaluated. `no_drop` is always False in the FA files.

The output CSV for `roster_management_batter_output_YYYYMMDD.csv` and `roster_management_batter_fa_output_YYYYMMDD.csv` will have the following columns:
| Column | Type | Description |
|--------|------|-------------|
| `player_id` | string | Unique identifier for player |
| `name` | string | String value of player's name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, MI, CI, OF, P, BN, IL, UT) |
| `no_drop` | boolean | Whether league rules allow this player to be dropped |
| `R_delta_season` | float | Season contribution score for runs |
| `HR_delta_season` | float | Season contribution score for home runs |
| `RBI_delta_season` | float | Season contribution score for runs batted in |
| `SB_delta_season` | float | Season contribution score for stolen bases |
| `OBP_delta_season` | float | Season contribution score for on base percentage |
| `total_efficiency_season` | float | Season efficiency score for the player |
| `R_delta_14d` | float | 14d Contribution score for runs |
| `HR_delta_14d` | float | 14d contribution score for home runs |
| `RBI_delta_14d` | float | 14d contribution score for runs batted in |
| `SB_delta_14d` | float | 14d contribution score for stolen bases |
| `OBP_delta_14d` | float | 14d contribution score for on base percentage |
| `total_efficiency_14d` | float | 14d efficiency score for the player |
| `recommendation` | string | Enum of drop_candidate, hold, or add_candidate |

The output CSV for `roster_management_pitcher_output_YYYYMMDD.csv` and `roster_management_pitcher_fa_output_YYYYMMDD.csv` will have the following columns:
| Column | Type | Description |
|--------|------|-------------|
| `player_id` | string | Unique identifier for player |
| `name` | string | String value of player's name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, MI, CI, OF, P, BN, IL, UT) |
| `no_drop` | boolean | Whether league rules allow this player to be dropped |
| `W_delta_season` | float | Season contribution score for wins |
| `SV_delta_season` | float | Season contribution score for saves |
| `K_delta_season` | float | Season contribution score for strikeouts |
| `ERA_delta_season` | float | Season contribution score for earned run average |
| `WHIP_delta_season` | float | Season contribution score for walks and hits per inning pitched |
| `total_efficiency_season` | float | Season efficiency score for the player |
| `W_delta_14d` | float | 14d contribution score for wins |
| `SV_delta_14d` | float | 14d contribution score for saves |
| `K_delta_14d` | float | 14d contribution score for strikeouts |
| `ERA_delta_14d` | float | 14d contribution score for earned run average |
| `WHIP_delta_14d` | float | 14d contribution score for walks and hits per inning pitched |
| `total_efficiency_14d` | float | 14d efficiency score for the player |
| `recommendation` | string | Enum of drop_candidate, hold, or add_candidate |

## Decision Rules
Logic for `recommendation` column
    - If player is on my roster and total_efficiency_14d is in lowest 3 deciles of total_efficiency_14d, mark as "drop_candidate"
    - If a player is on my roster and total_efficiency_14d is in the highest 3 deciles or no_drop = true, then mark as "hold"
    - If player is in the free agent pool and total_efficiency_14d is in the highest 4 deciles, mark as "add_candidate"
    - Decile thresholds for drop_candidate and hold are computed within my roster only. add_candidate thresholds are computed within the FA pool only. The two populations are ranked separately.
    - If none of these criteria are met, leave this value empty

## What This Skill Does NOT Do
This skill is just going to evaluate all players over season and 14d intervals to look at their contribution scores for their categories and then add a sum-product for their total_efficiency in both time ranges.  It will not make decisions about adding or dropping players, but will just highlight a recommendatino.

## Example Output
Example output for `roster_management_batter_output_YYYYMMDD.csv`
player_id | name | position | no_drop | R_delta_season | HR_delta_season | RBI_delta_season | SB_delta_season | OBP_delta_season | total_efficiency_season | R_delta_14d | HR_delta_14d | RBI_delta_14d | SB_delta_14d | OBP_delta_14d | total_efficiency_14d | recommendation
konnor_griffin_001 | Konnor Griffin | SS, OF | false | +1.8 | +0.4 | -0.1 | +1.7 | -0.2 | 4.2 | +.87 | -0.02 | +0.23 | +0.81 | +0.06 | 3.9 | 

Example output for `roster_management_pitcher_output_YYYYMMDD.csv`
player_id | name | position | no_drop | W_delta_season | SV_delta_season | K_delta_season | ERA_delta_season | WHIP_delta_season | total_efficiency_season |  W_delta_14d | SV_delta_14d | K_delta_14d | ERA_delta_14d | WHIP_delta_14d | total_efficiency_14d | recommendation
mason_miller_001 | Mason Miller | P | true | +0.0 | +0.98 | +0.87 | +0.84 | +0.77 | 4.68 | -.02 | +1.7 | +0.45 | +0.76 | -0.11 | 1.56  | hold

