# Matchup Agent — Skill Reference

## Purpose
The Matchup Agent runs in two modes. On Sunday night, it analyzes the opponent's roster and historical category performance to determine what categories they tend to win reliably, inconsistently, or weakly. In the mid-week mode, it evaluates the current matchup state against those tendencies to identify tactical opportunities — categories where the opponent is underperforming their norms or where the current gap is exploitable. The output goes to the GM agent, which makes final transaction and strategy decisions.

## Trigger Conditions
- **Sunday run** requires `pybaseball_opponent_roster_YYYYMMDD.parquet`, `scoreboard_history.parquet`, `league_benchmarks.parquet` and `opponent_history.parquet` to exist in `data/`
- **Wednesday run** additionally requires `scoreboard_history.parquet` and the most recent `output_matchup_sunday_*.csv` from a prior Sunday run
- Both runs are manually triggered after the latest data has been pulled

## Workflow

### Sunday Night Run
1. Load data: call `get_opponent_roster_list()`, `get_opponent_category_profile()`, `get_league_benchmarks()`, and `get_category_priority()` from `data_client.py`
2. Apply Decision Rules (Sunday) below to assign a `category_rating` to each category.  Compare opp_mean to league_average for medium/weak boundaries.
3. Produce the Sunday output table (all 10 categories)
4. Save output as `data/output_matchup_sunday_YYYYMMDD.csv`

### Wednesday Run
1. Load data: call `get_current_matchup_scores()` and load the most recent `output_matchup_sunday_*.csv`
2. Apply Decision Rules (Wednesday) below to assign a `status` to each category
3. Check the 20 IP warning condition (see Decision Rules)
4. Produce the Wednesday output table (all 10 categories), ordered by: `losing_badly` first, then `vulnerable`, then `losing_close`, then `winning_close`, then `winning_comfortably`
5. Save output as `data/output_matchup_wednesday_YYYYMMDD.csv`

## Output Schema

### Sunday Output
All 10 categories, one row each:

| Column | Type | Description |
|--------|------|-------------|
| `category` | string | R, HR, RBI, SB, OBP, W, SV, K, ERA, WHIP |
| `category_rating` | string | `strong` / `medium` / `weak` — opponent's historical consistency in this category |
| `opp_mean` | float | Opponent's average score in this category over the history window |
| `opp_min` | float | Opponent's lowest score in this category over the history window |
| `opp_max` | float | Opponent's highest score in this category over the history window |
| `notes` | string | One sentence summarizing the signal — e.g. "Connor scores HR consistently; target only if you have a significant roster advantage" |

### Wednesday Output
All 10 categories, one row each:

| Column | Type | Description |
|--------|------|-------------|
| `category` | string | R, HR, RBI, SB, OBP, W, SV, K, ERA, WHIP |
| `category_rating` | string | Carried over from Sunday output (`strong` / `medium` / `weak`) |
| `my_score` | float | My current score in this category |
| `opp_score` | float | Opponent's current score in this category |
| `gap` | float | my_score − opp_score (positive = I am winning; negative = I am losing). For ERA/WHIP: opp_score − my_score so positive still means I am winning |
| `opp_mean` | float | Opponent's historical average for this category (from Sunday output) |
| `underperformance` | float | opp_mean − opp_score (positive = opponent below their average; negative = opponent above their average). For ERA/WHIP: opp_score − opp_mean |
| `vulnerable` | boolean | True if opponent's current score is beyond their historical floor/ceiling |
| `status` | string | `winning_comfortably` / `winning_close` / `losing_close` / `losing_badly` / `vulnerable` |
| `notes` | string | One sentence for the GM — e.g. "Connor's ERA is well above his historical max; real opportunity to attack this category" |

### 20 IP Warning (Wednesday only)
Append a single warning row below the category table if the IP condition is triggered:

| Field | Value |
|-------|-------|
| `ip_warning` | True / False |
| `ip_note` | e.g. "Team IP pace is below 14 through Wednesday — flag for GM to consider streaming a starter regardless of ERA/WHIP impact" |

Note: IP tracking requires data not yet available in the bootstrap. Flag `ip_warning = False` and `ip_note = "IP tracking not yet available — requires Rust binary"` until the full data pipeline is in place.

## Decision Rules

### Sunday — category_rating
- `consistency = strong` → `category_rating = strong` — opponent wins this category reliably and their average is stronger than the league average benchmark in that category; only contest if you have a clear roster advantage
- `consistency = variable` → `category_rating = variable` — opponent is unpredictable here and their average score in this category is less than 110% of or below the league average; worth contesting, especially if you are currently winning it
- `consistency = weak` → `category_rating = weak` — opponent loses this category consistently and averages 90% or less than league average; prioritize winning this category

### Wednesday — status
Apply to each category after computing `gap`:

| Condition | status |
|-----------|--------|
| gap > 0 AND abs(gap) is large (see thresholds below) | `winning_comfortably` |
| gap > 0 AND abs(gap) is small | `winning_close` |
| gap < 0 AND abs(gap) is small | `losing_close` |
| gap < 0 AND abs(gap) is large | `losing_badly` |
| vulnerable = True AND gap < 0 | `vulnerable` — losing but opponent is below their floor; real catch-up opportunity |
| vulnerable = True AND gap >= 0 | `winning_comfortably` — already winning and opponent is struggling; protect the lead |

**Gap thresholds by category** (what counts as "large" vs "small"):

| Category | Small gap | Large gap |
|----------|-----------|-----------|
| R | < 5 | ≥ 5 |
| HR | < 3 | ≥ 3 |
| RBI | < 5 | ≥ 5 |
| SB | < 2 | ≥ 2 |
| OBP | < 0.015 | ≥ 0.015 |
| W | < 2 | ≥ 2 |
| SV | < 1 | ≥ 1 |
| K | < 8 | ≥ 8 |
| ERA | < 0.50 | ≥ 0.50 |
| WHIP | < 0.10 | ≥ 0.10 |

### Wednesday — 20 IP Warning
- If `matchup_state.transactions_remaining` > 0 AND IP data is available AND projected team IP through Sunday < 20 → set `ip_warning = True`
- In bootstrap phase: always set `ip_warning = False` with note "IP tracking not yet available"

## What This Skill Does NOT Do
- Does not predict future player performance (Future Predictor agent)
- Does not evaluate individual players on the opponent's roster for stat projections — only uses the roster to understand roster composition
- Does not recommend specific transactions — that is the GM agent's responsibility
- Does not evaluate your own roster's upcoming schedule (Future Predictor agent)
- Does not make the punt/target decision — it surfaces opportunities; the GM decides

## Phase 2 Enhancements
- **Opponent historical tendencies across multiple opponents**: track tendencies for all opponents in the league, not just the current week's matchup
- **Opponent transaction behavior**: track how many transactions Connor typically uses and when (first half vs second half of week) to anticipate roster moves
- **IP tracking**: include team IP-to-date in `matchup_state` once Rust binary is pulling Yahoo data; activate the 20 IP warning logic
- **Opponent roster stats**: pull advanced stats for opponent's players to better assess their category ceiling this week, not just historical patterns

## Example Output

### Sunday
| category | category_rating | opp_mean | opp_min | opp_max | notes |
|----------|----------------|----------|---------|---------|-------|
| R | medium | 29.5 | 20 | 34 | Connor's runs output is inconsistent — range of 14 runs week to week. Contestable. |
| HR | medium | 8.8 | 4 | 14 | Wide swing week to week — do not assume Connor wins this. |
| RBI | low | 28.8 | 17 | 43 | Huge variance — Connor's RBI is unpredictable. Contest aggressively. |
| SB | high | 5.5 | 4 | 8 | Connor steals consistently. Difficult to beat unless you have SB upside on your roster. |
| OBP | low | 0.302 | 0.278 | 0.328 | Connor's OBP is weak and inconsistent — priority target. |
| W | medium | 2.8 | 1 | 4 | Moderate consistency. Wins vary with starter performance. |
| SV | high | 0.8 | 0 | 1 | Connor relies on saves but rarely gets more than 1. Matchable. |
| K | medium | 31.8 | 29 | 36 | Fairly consistent range. Contest if you have strong strikeout pitchers. |
| ERA | low | 3.12 | 2.32 | 4.58 | Connor's ERA swings over 2 points week to week — real vulnerability. |
| WHIP | medium | 1.05 | 0.90 | 1.18 | Moderate consistency in WHIP. |

### Wednesday
| category | category_rating | my_score | opp_score | gap | opp_mean | underperformance | vulnerable | status | notes |
|----------|----------------|----------|-----------|-----|----------|-----------------|------------|--------|-------|
| ERA | weak | 9.27 | 2.67 | -6.60 | 3.12 | -0.45 | False | losing_badly | My ERA is well above Connor's — difficult to recover this week. GM should consider benching struggling pitchers. |
| WHIP | medium | 1.79 | 1.00 | -0.79 | 1.05 | 0.05 | False | losing_badly | Losing WHIP badly. Connor performing near his average. |
| OBP | weak | 0.327 | 0.346 | -0.019 | 0.302 | -0.044 | False | losing_badly | Losing OBP despite Connor performing above his historical average. |
| SB | high | 4 | 1 | 3 | 5.5 | 4.5 | True | winning_comfortably | Connor at 1 SB vs his historical floor of 4 — significant underperformance. I am winning; protect the lead. |
| K | medium | 28 | 24 | 4 | 31.8 | 7.8 | True | winning_close | Connor below his historical floor in K — real opportunity to extend lead. |
| R | medium | 27 | 20 | 7 | 29.5 | 9.5 | False | winning_comfortably | Winning comfortably. Connor below average but not below floor. |
| HR | medium | 8 | 5 | 3 | 8.8 | 3.8 | False | winning_comfortably | Winning HR. Connor below his average. |
| RBI | low | 31 | 20 | 11 | 28.8 | 8.8 | False | winning_comfortably | Winning RBI by a wide margin. |
| SV | high | 1 | 0 | 1 | 0.8 | 0.8 | True | winning_comfortably | Winning saves. Connor at 0, below his typical range. |
| W | medium | 2 | 3 | -1 | 2.8 | -0.2 | False | losing_close | Losing wins by 1. Connor performing near his average. Catchable with a good pitching day. |
