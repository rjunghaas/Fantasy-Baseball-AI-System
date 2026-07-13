# Future Predictor — Skill Reference

## Purpose
This skill will collect information about upcoming games to help forecast player availability and upcoming matchups.  The skill will then calculate 3 multipliers and use them to calculate a weekly_adjusted_contribution. GM will apply the weekly_adjusted_contribution to the total_efficiency_scores from Roster Management skill to produce fully adjusted numbers.

## Trigger Conditions
- Runs on Sunday nights and Wednesday nights as part of the full agent pipeline
- Parquet files `park_factors.parquet`, `schedule_YYYYMMDD.parquet`, `probable_starters_YYYYMMDD.parquet`, `pybaseball_roster_YYYYMMDD.parquet`, and `pybaseball_fa_YYYYMMDD.parquet` must exist in the `data/` directory for this agent to run
- The agent uses data_client.py to load pre-computed trend signals — it does not query Parquet directly

## Workflow
1. Load data: use get_probable_starters(), get_schedule(), get_park_factors(), and get_fa_pitcher_starts() from data_client.py to load data
2. games_multiplier:  We will normalize to an assumed 7 games per week.  For each batter in `pybaseball_roster_YYYYMMDD.parquet` and `pybaseball_fa_YYYYMMDD.parquet`, get their teams and count the number of games their teams will play until the end of the week.  Divide this number of games by the assumed 7 games in a week to get the games_multiplier.  For each pitcher, set this to 1.0
3. two_start_multiplier:  For all pitchers in `probable_starters_YYYYMMDD.parquet`, find all pitchers who are listed twice.  Make their two_start_multiplier = 2.0.  For all other players, they will have a two_start_multiplier = 1.0
4. park_factor_multiplier:  In `park_factors.parquet`, use the runs_factor and divide by 100.  Then for the scheduled games of each team's batters, take a weighted average of the park_factor to get park_factor_multiplier.  As an example, let's say a player's team is playing 3 games at Target Field where the runs_factor is 106 and 4 games at Wrigley where the runs_factor is 90.  Their park_factor_multiplier would be (106/100) * (3/7) + (90/100) * (4/7) = 0.96857.  For pitchers, take the inverse of each park's runs_factor and apply the same formula for pitchers except using the inverse of runs_factor.
5. weekly_adjusted_contribution:  Take the 3 multipliers so far and multiply them together to get the player's weekly_adjusted_contribution.  Thus, games_multipler * two_start_multiplier * park_factor_multipler = weekly_adjusted_contribution.
6. Output the full list of roster and free agent players into a csv called `future_predictor_YYYYMMDD.csv` ordered by weekly_adjusted_contribution in descending order.  For the `no_drop` column: roster players check against the NO_DROP_PLAYERS list in data_client.py; FA players always get no_drop = False.

## Output Schema
The output schema for `future_predictor_YYYYMMDD.csv`

| Column | Type | Description |
|--------|------|-------------|
| `player_id` | string | Unique identifier for player |
| `name` | string | String value of player's name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, MI, CI, OF, P, BN, IL, UT) |
| `no_drop` | boolean | Whether league rules allow this player to be dropped |
| `games_multiplier` | float | Games available to be played normalized to 7 |
| `two_start_multiplier` | float | Multiplier if pitcher is projected to make 2 starts |
| `park_factor_multiplier` | float | Park factor adjustment for where team will play games over the next week |
| `weekly_adjusted_contribution` | float | Product of games_multiplier, two_start_multiplier, and park_factor_multiplier |

## Decision Rules
The logic for the multipliers and weekly_adjusted_contribution is set out above in Workflow.

## What This Skill Does NOT Do
The skill will compute the factors and contributions based on the players' teams and where those teams are scheduled to play in the upcoming week.

## Example Output
| player_id | name | position | no_drop | games_multiplier | two_start_multiplier | park_factor_multiplier | weekly_adjusted_contribution
| josh_naylor_001 | Josh Naylor | 1B | false | 0.857 | 1.0 | 1.023876 | 0.87746
| tarik_skubal_001 | Tarik Skubal | P | true | 1.0 | 2.0 | 0.98464 | 1.96928