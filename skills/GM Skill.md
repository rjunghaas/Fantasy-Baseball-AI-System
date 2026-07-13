# GM Agent — Skill Reference

## Purpose
This GM Agent will take outputs from the the Matchup Skill, the Roster Management Skill, the Future Predictor Skill, and the Trend Analyzer Skill and calls from data_client.py to synthesize data and make recommendations to the human player according to the Decision Rules below.  The output should be formatted into the provided Output Schema.

## Trigger Conditions
- **Sunday run** requires `output_matchup_sunday_YYYYMMDD.csv`, `roster_management_batter_output_YYYYMMDD.csv`, `roster_management_pitcher_output_YYYYMMDD.csv`, `roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`, `future_predictor_YYYYMMDD.csv`, `trend_analyzer_roster_YYYYMMDD.csv`, `trend_analyzer_opponent_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`
- **Wednesday run** requires `output_matchup_wednesday_YYYYMMDD.csv`, `roster_management_batter_output_YYYYMMDD.csv`, `roster_management_pitcher_output_YYYYMMDD.csv`, `roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`, `future_predictor_YYYYMMDD.csv`, `trend_analyzer_roster_YYYYMMDD.csv`, `trend_analyzer_opponent_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`
- GM Skill should be triggered once runs are completed by the the Matchup Skill, the Roster Management Skill, the Future Predictor Skill, and the Trend Analyzer Skill and outputs generated with the latest data outputs from each.

## Constraints
1. Roster construction constraints:
    - Lineup lock:  Players with games already started cannot be added or dropped that day
    - IL timing:  Players removed from the IL cannot be activated in the middle of the week without an open roster slot.  If all roster spots are full, a player must be dropped for the player on IL to be activated to the active roster.  Yahoo does not allow subsequent roster adds if a real-life player has been activated from the IL, but the fantasy player is still on the IL, so this move has to be prioritized before any other additions.  Players must be on the IL in real-life to be added to an IL slot.  There are 3 IL slots, so if a fourth player is added to the IL in real-life, the human player must decide whether to keep that player using a roster spot or to drop them.
    - Position eligibility:  Each team can have up to 12 active batters and 8 active pitchers per day.  Once a player's game has started, they cannot be dropped or moved to the bench (see Lineup lock above).  The total roster is 23 players, meaning 3 or more players on the roster cannot be counted for their stats that day.  Batter positions in the league are C, 1B, 2B, 3B, SS, CI, MI, OF, OF, OF, OF, UT.  Player positions must be designated as such to fill these positions on the team.  CI means player must have 1B or 3B as one of their positions.  MI means player must have 2B or SS as one of their positions.  UT means any batter position can fill that slot; it cannot be filled with a pitcher.  Yahoo has up to 8 pitcher (P) slots that can be active per day.  It is allowed to leave a roster spot unfilled that day.  For instance, if we are prioritizing a ratio stat such as OBP, ERA, or WHIP and there are not strong matchups, we could choose to bench a player and not fill a roster spot.  In that case, the player's stats would not count.
2. Acquisition constraints:
    - Waiver lock:  When players are waived by a team, they are subject to a 5-day waiver period where they cannot be added to a roster.  Once that waiver period expires, they can be added to a team.  During the waiver lock period, a team can place a "waiver claim" on the player.  Waiver claims are processed in priority order based on which team acquired a player through a waiver claim longer ago.  In other words, a successful waiver claim means that team will be placed lowest in priority for the next time a waiver claim is placed.
    - Weekly acquisition limits:  The league has a hard limit of 7 additions per week.  This resets every Sunday night.  I have added soft limits of 2 additions in the first half of the week (prior to the Wednesday run) and 5 additions in the second half of the week.  This soft limit is the human player's choice whether to violate it.
3. Statistical / scoring constraints:
    - IP limits:  The league rule is that a team must have 20 innings pitched (IP) by the end of the week.  If the team's ptichers do not meet this rule, that team will forfeit all 5 pitching categories to the opposing team (assuming they also meet the 20 IP limit).  The Wednesday run will project this based on probable starters to warn the human player if they are in danger of falling short of this.
    - Counting stat accumulation timing:  Early-week adds matter more than late-week adds for counting stats (R, HR, RBI, SB, K, W, SV).  The GM should not how manuy games remain when a move is being considered.
    - Streaming vs. holding:  For pitchers especially, a two-start pitcher available on waivers mid-week may have already started once.  The GM should check two_start_multipler from Future Predictor before recommending a streaming add.
4. No-drop list:  The no_drop flag from Roster Management will override any drop recommendations for these players.  League rules are that these players cannot be dropped, so we should not make recommendations for these players.

## Workflow

**Sunday run**
1. Load data:  Load relevant CSV files noted above in Trigger Conditions
2. Matchup Strategy:  Identify `low` and `medium` categories as our primary target categories.  These are the categories that the GM strategy will be optimizing for initially until it gathers further data in the Wednesday run.  All subsequent decisions will filter through these target categories.
3. Weekly Adjustment Calculations:  Get each batter's `total_efficiency_14d` from `roster_management_batter_output_YYYYMMDD.csv` and each pitcher's `total_efficiency_14d` from `roster_management_pitcher_output_YYYYMMDD.csv`.  Get `weekly_adjusted_contribution` from `future_predictor_YYYYMMDD.csv` for each of these player's.  Multiply `total_efficiency_14d` and `weekly_adjusted_contribution` for each player to get each player's `weekly_value`.  Sort by `weekly_value` from lowest to highest.
4. Based on sorted `weekly_value` for each player, use Decision Rules below to determine which are `candidates_for_evaluation`.  
5. Populating the Output Schema
    - Add week number
    - Mode = "full"
    - As_of = date of the run
    - Recommended_adds = [] Free agent players identified through Decision Rules 5 and 6.  Add Confidence Ratings for each move alongside the recommended_add players here
    - Recommended_drops = this will be players identified as `candidates_for_evaluation` who have suitable replacements identified in Decision Rules 5 and 6. 
    - Category_targets = low and medium categories
    - Categories_punted = Opponent categories rated as `strong`. These are categories that we are deprioritizing
    - Transaction budget =
        - first_half_target = 2
        - second_half_target = 5
        - used_this_week = 0
        - remaining = 7
    - Rationale_summary - 3-5 sentences that address
        1. Which categories are being targeted this week and why (tier + opponent state)
        2. Which categories are being conceded and why
        3. Summary of players recommended to be added against players recommended to be dropped and confidence levels

**Wednesday run**
1. Load data:  Load relevant CSV files noted above in Trigger Conditions
2. Matchup Strategy:  Identify `vulnerable`, `winning_close`, and `losing_close` categories as our primary target categories.  These are the categories that the GM strategy will be optimizing for initially until it gathers further data in the Wednesday run.  All subsequent decisions will filter through these target categories.  `winning_comfortably` means hold, no moves needed.  `losing_badly` means concede, no moves needed.  We should prioritize these categories as follows:  `winning_close`, `losing_close`, `vulnerable`.
3. Weekly Adjustment Calculations:  Get each batter's `total_efficiency_14d` from `roster_management_batter_output_YYYYMMDD.csv` and each pitcher's `total_efficiency_14d` from `roster_management_pitcher_output_YYYYMMDD.csv`.  Get `weekly_adjusted_contribution` from `future_predictor_YYYYMMDD.csv` for each of these player's.  Multiply `total_efficiency_14d` and `weekly_adjusted_contribution` for each player to get each player's `weekly_value`.  Sort by `weekly_value` from lowest to highest.
4. Based on sorted `weekly_value` for each player, use Decision Rules below to determine which are `candidates_for_evaluation`.  
5. Populating the Output Schema
    - Add week number
    - Mode = "midweek"
    - As_of = date of the run
    - Recommended_adds = [] Free agent players identified through Decision Rules 5 and 6.  Add Confidence Ratings for each move alongside the recommended_add players here
    - Recommended_drops = this will be players identified as `candidates_for_evaluation` who have suitable replacements identified in Decision Rules 5 and 6. 
    - Category_targets = `winning_close`, `losing_close`, and `vulnerable` categories
    - Categories_punted = Opponent categories rated as `losing_badly`. These are categories that we are deprioritizing
    - Transaction budget =
        - used_this_week = 3
        - second_half_remaining = 4
    - Rationale_summary - 3-5 sentences that address
        1. Which categories are being targeted the rest of the week and why (tier + opponent state)
        2. Which categories are being conceded and why
        3. Summary of players recommended to be added against players recommended to be dropped and confidence levels


## Output Schema

**Sunday Run**
{
  "week": 14,
  "mode": "full",
  "as_of": "2026-06-08",
  "recommended_adds": [
    {"player": "Name", "position": "OF", "rationale": "...", "confidence": "high"}
  ],
  "recommended_drops": [
    {"player": "Name", "rationale": "..."}
  ],
  "category_targets": ["HR", "SB", "K"],
  "categories_punted": ["W"],
  "transactions_budget": {
     "first_half_target": 2,
     "second_half_target": 5,
     "used_this_week": 0,
     "remaining": 7
  },
  "confidence": "high",
  "rationale_summary": "one paragraph"
}

**Wednesday Run**
{
  "week": 14,
  "mode": "midweek",
  "as_of": "2026-06-08",
  "recommended_adds": [
    {"player": "Name", "position": "OF", "rationale": "...", "confidence": "high"}
  ],
  "recommended_drops": [
    {"player": "Name", "rationale": "..."}
  ],
  "category_targets": ["HR", "SB", "K"],
  "categories_punted": ["W"],
  "transactions_budget": {
     "used_this_week": 3,
     "second_half_remaining": 4
  },
  "confidence": "high",
  "rationale_summary": "one paragraph"
}

**Adhoc Run**
```json
{
  "week": 14,
  "mode": "adhoc",
  "as_of": "2026-06-08",
  "position_needed": "OF",
  "recommended_adds": [
    {
      "player": "Name",
      "type": "batter",
      "eligible_positions": ["OF", "UT"],
      "total_efficiency_14d": 3.42,
      "rationale": "Top available OF by 14d efficiency; strong R and HR contribution delta."
    }
  ],
  "confidence": "medium",
  "rationale_summary": "Injury replacement recommendations for OF slot. Top 2 batters and top 2 pitchers by recent efficiency. No matchup context used — this is a pure availability check."
}
```

## Decision Rules
**Sunday Run — Category Prioritization**
Goal: Identify which players to prioritize for the 12 batter / 8 pitcher active roster based on the opponent's weak and medium categories.

1. After multiplying `total_efficiency_14d` and `weekly_adjusted_contribution`, then sorting, a rating of `candidate_for_evaluation` should be assigned to the lowest 2-3 batters and lowest 2-3 pitchers on my roster.
2. Then, locate players with `candidate_for_evaluation` rating in `trend_analyzer_roster_YYYYMMDD.csv`.  If `action_flag` for players with `candidate_for_evaluation` is either `drop_candidate` or `bench_today`, these players will be evaluated subsequently against the free agent pool to determine if the GM should make a recommendation to replace them.  Remove `candidate_for_evaluation` rating if they do not have `drop_candidate` or `bench_today`.
3. Next, from `roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`, `future_predictor_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`, we will get all of the players from the Trend Analyzer FA output and calculate their weekly_value in the same way by multiplying `total_efficiency_14d` by their `weekly_adjusted_contribution` and then rank these from highest to lowest.
4. Before ranking FA add_candidates by weekly_value, filter to only those with a positive contribution_delta in at least one target category. Use the per-category deltas from roster_management_batter_fa_output_YYYYMMDD.csv and roster_management_pitcher_fa_output_YYYYMMDD.csv (e.g., R_delta_14d, HR_delta_14d, etc. for batters; K_delta_14d, ERA_delta_14d, etc. for pitchers). Discard any FA add_candidate whose deltas are zero or negative in all target categories — they may have high overall weekly_value driven by categories you're punting, and are not useful this week.

The remaining eligible FAs are then ranked by weekly_value (still using total_efficiency_14d × weekly_adjusted_contribution, for apples-to-apples comparison with your roster drop candidates) and evaluated in Rules 5 and 6.  If no eligible FAs, then note in the Output rationale_summary.
5. Take the `candidate_for_evaluation` batters from my roster and their weekly_value scores.  Recommend a replacement for the `candidate_for_evaluation` batter if a. there is a free agent with a weekly_value is at least 10% higher than the `candidate_for_evaluation` and b. the replacement can cover at least one position that the `candidate_for_evaluation` can.  If multiple players meet this criteria, take the one with the highest weekly value.
  - Confidence is based on following:  low = weekly_value is 10-15% higher (inclusive of 15% exactly) and player does not cover all positions that `candidate_for_evaluation` covers, medium = weekly value is greater than 15% and player does not cover all positions that `candidate_for_evaluation` covers, medium = weekly value is 10-15% higher (inclusive of 15% exactly) and player covers all positions that `candidate_for_evaluation` covers, and high = weekly value is greater than 15% and player covers all positions that `candidate_for_evaluation` covers.  
  - Position coverage logic means take position(s) of `candidate_for_evaluation` player.  FA player must share at least one position with `candidate_for_evaluation` player so that the slot will not go unfilled on my roster if the swap is made.
6. Take the `candidate_for_evaluation` pitchers from my roster and their weekly_value scores.  Recommend a replacement for the `candidate_for_evaluation` pitcher if there is a free agent with a weekly_value is at least 10% higher than the `candidate_for_evaluation`.  If multiple players meet this criteria, take the one with the highest weekly value.  Confirm that recommended replacement pitcher has position "P".  if no eligible pitcher FA is found, note in rationale_summary that no pitcher upgrade was identified.
  - Confidence is based on following:  low = weekly_value is 15-20% higher, medium = weekly_value is 20-25% or higher, high = weekly value is 25% or higher.  Note that the weekly_value takes into account the two_start_multiplier.
7. For the recommended transactions for batters and pitchers, take into account the 2 addition soft transaction limit for the first half of the week.  Recommend the high confidence transactions ordered by weekly_value difference.  If there are more than 2, then the human player can make a judgment call of which 2 they will execute or whether to go over the soft limit.  If there are fewer than 2 high confidence recommendations, show the highest medium confidence recommendation to give human player additional context.  Again, human player can decide whether to execute this transaction or not.

**Wednesday Run - Targeted Optimizations**
Goal:  Identify players that can help ensure I protect categories that I am winning and capture additional categories that are close at the midpoint of the matchup.

1. After multiplying `total_efficiency_14d` and `weekly_adjusted_contribution`, then sorting, a rating of `candidate_for_evaluation` should be assigned to the lowest 2-3 batters and lowest 2-3 pitchers on my roster.
2. Then, locate players with `candidate_for_evaluation` rating in `trend_analyzer_roster_YYYYMMDD.csv`.  If `action_flag` for players with `candidate_for_evaluation` is either `drop_candidate` or `bench_today`, these players will be evaluated subsequently against the free agent pool to determine if the GM should make a recommendation to replace them.  Remove `candidate_for_evaluation` rating if they do not have `drop_candidate` or `bench_today`.
3. Next, from `roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`, `future_predictor_YYYYMMDD.csv`, and `trend_analyzer_fa_YYYYMMDD.csv`, we will get all of the players from the Trend Analyzer FA output and calculate their weekly_value in the same way by multiplying `total_efficiency_14d` by their `weekly_adjusted_contribution` and then rank these from highest to lowest.
4. Before ranking FA add_candidates by weekly_value, filter to only those with a positive contribution_delta in at least one target category. Use the per-category deltas from roster_management_batter_fa_output_YYYYMMDD.csv and roster_management_pitcher_fa_output_YYYYMMDD.csv (e.g., R_delta_14d, HR_delta_14d, etc. for batters; K_delta_14d, ERA_delta_14d, etc. for pitchers). Discard any FA add_candidate whose deltas are zero or negative in all target categories — they may have high overall weekly_value driven by categories you're punting, and are not useful this week.

The remaining eligible FAs are then ranked by weekly_value (still using total_efficiency_14d × weekly_adjusted_contribution, for apples-to-apples comparison with your roster drop candidates) and evaluated in Rules 5 and 6.  If no eligible FAs, then note in the Output rationale_summary.
5. Take the `candidate_for_evaluation` batters from my roster and their weekly_value scores.  Recommend a replacement for the `candidate_for_evaluation` batter if a. there is a free agent with a category delta that is at least 10% higher than the `candidate_for_evaluation` (FA_category_delta in one of target categories >= 1.10 * candidate_category_delta in that category), b. the replacement has a weekly_value equal to or greater than the `candidate_for_evaluation`, and c. the replacement can cover at least one position that the `candidate_for_evaluation` can.  If multiple players meet this criteria, take the one with the highest weekly value.  We want to shif the unit of focus to category deltas in the target categories identified since there are fewer games, so we are trying to exaggerate our impact in the target categories to win the overall matchup.
  - Confidence is based on following:  low = one category that is 10-15% higher (inclusive of 15% exactly) and player does not cover all positions that `candidate_for_evaluation` covers, medium = one category delta is greater than 15% and player does not cover all positions that `candidate_for_evaluation` covers, medium = more than one category delta is 10% or higher and player covers all positions that `candidate_for_evaluation` covers, and high = more than one category delta is greater than 15% and player covers all positions that `candidate_for_evaluation` covers.  
  - Position coverage logic means take position(s) of `candidate_for_evaluation` player.  FA player must share at least one position with `candidate_for_evaluation` player so that the slot will not go unfilled on my roster if the swap is made.
6. Take the `candidate_for_evaluation` pitchers from my roster and their weekly_value scores.  Recommend a replacement for the `candidate_for_evaluation` pitcher if there is a free agent with a target category delta that is at least 15% higher than the `candidate_for_evaluation` (FA_category_delta in one of target categories >= 1.15 * candidate_category_delta in that category) and weekly_value is equal to or higher than `candidate_for_evaluation`.  If multiple players meet this criteria, take the one with the most category deltas over 15% better than the `candidate_for_evaluation`.  Confirm that recommended replacement pitcher has position "P".  We want to shif the unit of focus to category deltas in the target categories identified since there are fewer games, so we are trying to exaggerate our impact in the target categories to win the overall matchup.  If no eligible pitcher FA is found, note in rationale_summary that no pitcher upgrade was identified.
  - Confidence is based on following:  low = one category delta is 15-20% higher (inclusive of 20%), medium = one category_delta is 20% or higher, high = more than one category deltas are 15% or higher.  Note that the weekly_value takes into account the two_start_multiplier.
7. For the recommended transactions for batters and pitchers, take into account the remaining transactions left in the week.  Recommend the high confidence transactions ordered by confidence, if tied on qualifying deltas, break tie by highest weekly_value.  If there are more than 2, then the human player can make a judgment call of which 2 they will execute or whether to go over the soft limit.  If there are fewer than 2 high confidence recommendations, show the highest medium confidence recommendation to give human player additional context.  Again, human player can decide whether to execute this transaction or not.


**Adhoc run (injury replacement)**

Trigger: Rust binary called with `--mode adhoc` and optionally `--position <POS>`.
Input files required: `fa_positions_YYYYMMDD.csv`, `pybaseball_fa_YYYYMMDD.parquet`,
`roster_management_batter_fa_output_YYYYMMDD.csv`, `roster_management_pitcher_fa_output_YYYYMMDD.csv`.

1. Load position data: Call `get_fa_positions()` from data_client.py to build `{player_id: [eligible_positions]}` for the FA pool.
2. Load FA efficiency scores: Load `roster_management_batter_fa_output_YYYYMMDD.csv` and `roster_management_pitcher_fa_output_YYYYMMDD.csv`, get `total_efficiency_14d` per player.
3. If `--position` was provided, filter the batter FA pool to only those whose `eligible_positions` list contains the requested position. Pitchers always require "P" in eligible_positions regardless of the `--position` flag.
4. Rank remaining FA batters by `total_efficiency_14d` descending. Take top 2. Rank remaining FA pitchers by `total_efficiency_14d` descending. Take top 2.
5. For each recommended add, include: player name, eligible_positions list, total_efficiency_14d, type (batter/pitcher), and a one-line rationale citing the efficiency score.
6. Write output to `decisions_adhoc_YYYYMMDD.json` using the adhoc output schema below.

No matchup context, no weekly_value calculation, no transaction budget logic — this is a pure efficiency-based availability check for emergency roster replacement.


## What This Skill Does NOT Do
The GM Agent will mostly pull and synthesize outputs from other agents, so it will delegate the following to other agents:
1. Trend Analyzer agent will look at player performance to determine if they are hot, cold, due for a positive or a negative regression and output this to the GM Agent
2. The Matchup agent will look at the opponent's strategy and strengths/weaknesses and report this to GM
3. The Roster Management agent will add slot efficiency scoring and category deltas for the GM to use to make evaluations.
4. The Future Predictor will analyze upcoming matchups and parks to determine who should play and output this to the GM Agent. 
5. Will make recommendations to human user about players to drop and free agents to add.  Human player will ultimately make final decisions


## Example Output
{
  "week": 12,
  "mode": "full",
  "as_of": "2026-06-13",
  "recommended_adds": [],
  "recommended_drops": [
    {"player": "Paul Goldschmidt", "rationale": "{key_stat} | recommendation_strength={N}"}
  ],
  "category_targets": ["HR", "SB", "K"],
  "categories_punted": ["R", "ERA"],
  "transactions_budget": {
     "first_half_target": 2,
     "second_half_target": 5,
     "used_this_week": 0,
     "remaining": 7
  },
  "confidence": "medium",
  "rationale_summary": "HR, SB, K should be targeted due to being vulnerable, and R and ERA should be conceded because these are losing badly.  Salvador Perez has been a good source of HR, but has been cold lately, so he is a risk to put in the lineup.  Likewise, Michael King has been a source of K for the season, but his WHIP has been high lately.  IP is at 15.2 innings which is satisfactory at this stage of the week.  Overall, I have medium confidence as HR, SB, and K are Tier A categories and all have recommendation strength of 2 or 3."
}