# Trend Analyzer — Skill Reference

## Purpose
The Trend Analyzer agent connects to Parquet data for players on my fantasy baseball roster using data_client.py. It evaluates recent performance trends against season baselines to identify players who are slumping, hot, or due for statistical regression. It produces a ranked output for the GM agent to act on. It does not consider upcoming matchups, park factors, roster slot efficiency, or opponent strategy — those are handled by other agents.

## Trigger Conditions
- Runs on Sunday nights and Wednesday nights as part of the full agent pipeline
- Parquet files `stats_YYYYMMDD.parquet` and `rosters_YYYYMMDD.parquet` must exist in the `data/` directory for this agent to run
- The agent uses data_client.py to load pre-computed trend signals — it does not query Parquet directly

## Workflow

1. Load data: Call `get_batter_trend_signals()` and `get_pitcher_trend_signals()` from data_client.py. Also call `get_my_roster()` to retrieve the `no_drop` flag for each player.
2. Join the `no_drop` flag onto both DataFrames by `player_id`.
3. Filter any rows where 14d stats are 0.0 or null. Flag these players as `insufficient_data` — set `action_flag = hold`, `recommendation_strength = null`, `key_stat = null`, and `notes = "No recent data — player may be injured or not rostered. Monitor."`.
4. For each remaining row, apply the Decision Rules below to assign `action_flag` and `recommendation_strength`.
5. Apply the no-drop override: if `no_drop = true` and `action_flag = drop_candidate`, override to `hold` and update notes accordingly.
6. Populate `key_stat` and `notes` for each player.
7. Produce the output table ordered by: `drop_candidate` first, then `bench_today`, then `hold`, then `neutral`. Within each group, order by `recommendation_strength` descending.

## Output Schema

Produce a table with the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `player_name` | string | Player's full name |
| `position` | string | Roster position (C, 1B, 2B, 3B, SS, CI, OF, P, BN, IL) |
| `trend_signal` | string | Signal from data_client.py: hot / cold / positive_regression / negative_regression / era_inflation / era_deflation_risk / velocity_drop / insufficient_data |
| `action_flag` | string | Agent recommendation: `drop_candidate` / `bench_today` / `hold` / `neutral` |
| `recommendation_strength` | int or null | 1–3 based on consecutive data pulls confirming the signal. Null if insufficient_data. |
| `key_stat` | string or null | Short summary of the stat(s) driving the signal. e.g. `"babip_14d=0.243, xwoba_season=0.390"`. Null if insufficient_data. |
| `notes` | string | One sentence for the GM explaining the signal and any overrides applied. |

**Note on consecutive pulls:** recommendation_strength is based on how many consecutive data pulls confirm the signal. In the bootstrap phase (single snapshot), all signals default to strength=1. Strength will increase naturally as weekly Parquet files accumulate.

## Decision Rules

### Batters

**Positive regression** (babip_14d < 0.250 AND xwoba_season > 0.320):
- 3 consecutive pulls → `action_flag = bench_today`, strength = 3
- 2 consecutive pulls → `action_flag = bench_today`, strength = 2
- 1 pull → `action_flag = hold`, strength = 1
- key_stat: `"babip_14d={value}, xwoba_season={value}"`
- notes: "Underperforming underlying talent — expect bounce-back. Hold and monitor."

**Negative regression** (babip_14d > 0.370 AND xwoba_season < 0.320):
- 3 consecutive pulls → `action_flag = drop_candidate`, strength = 3
- 2 consecutive pulls → `action_flag = hold`, strength = 2
- 1 pull → `action_flag = hold`, strength = 1
- key_stat: `"babip_14d={value}, xwoba_season={value}"`
- notes: "Overperforming underlying talent — production likely to decline. Consider drop or stream replacement."

**Hot** (obp_14d > obp_season + 0.040):
- Any strength → `action_flag = hold`
- key_stat: `"obp_14d={value}, obp_season={value}"`
- notes: "Running hot — ride the streak but monitor for negative regression signal."

**Cold** (obp_14d < obp_season - 0.040):
- 3 consecutive pulls → `action_flag = bench_today`, strength = 3
- 2 consecutive pulls → `action_flag = hold`, strength = 2
- 1 pull → `action_flag = hold`, strength = 1
- key_stat: `"obp_14d={value}, obp_season={value}"`
- notes: "Persistent cold streak — bench to protect OBP category while monitoring."

**Neutral:** `action_flag = neutral`, strength = null, key_stat = null, notes = "No meaningful trend signal."

### Pitchers

**ERA Inflation** (era_14d > era_season + 1.50 AND fip_season < era_season):
- 3 consecutive pulls → `action_flag = bench_today`, strength = 3
- 2 consecutive pulls → `action_flag = hold`, strength = 2
- 1 pull → `action_flag = hold`, strength = 1
- key_stat: `"era_14d={value}, era_season={value}, fip_season={value}"`
- notes: "ERA elevated but FIP suggests talent is intact — results should correct. Hold unless benching protects ERA/WHIP this week."

**ERA Deflation Risk** (era_14d < era_season - 1.50 AND fip_season > era_season):
- Any strength → `action_flag = hold`
- key_stat: `"era_14d={value}, era_season={value}, fip_season={value}"`
- notes: "ERA looks strong but FIP signals regression risk — defer to Future Predictor for matchup context before starting."

**Velocity Drop** (velocity_14d < velocity_season - 1.5 mph):
- Any strength → `action_flag = drop_candidate`
- key_stat: `"velocity_season={value}, velocity_14d={value}"`
- notes: "Significant velocity decline — possible health concern. Flag for GM regardless of ERA/WHIP. Consider drop or IL monitoring."

**Insufficient data:** `action_flag = hold`, strength = null, key_stat = null, notes = "No recent data — player may be injured or not rostered. Monitor."

**Neutral:** `action_flag = neutral`, strength = null, key_stat = null, notes = "No meaningful trend signal."

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

| player_name | position | trend_signal | action_flag | recommendation_strength | key_stat | notes |
|---|---|---|---|---|---|---|
| Konnor Griffin | OF | negative_regression | hold | 1 | babip_14d=0.667, xwoba_season=0.303 | Overperforming underlying talent — production likely to decline. Consider drop or stream replacement. |
| JJ Bleday | OF | positive_regression | hold | 1 | babip_14d=0.243, xwoba_season=0.390 | Underperforming underlying talent — expect bounce-back. Hold and monitor. |
| Yoshinobu Yamamoto | P | era_deflation_risk | hold | 1 | era_14d=0.68, era_season=2.68, fip_season=3.43 | ERA looks strong but FIP signals regression risk — defer to Future Predictor for matchup context before starting. |
| Tarik Skubal | P | neutral | neutral | null | null | No meaningful trend signal. |
