# GM Agent — Skill Reference

## Purpose
This GM Agent will take outputs from the Trend Analyzer Skill, the Matchup Skill, and calls from data_client.py to synthesize data and make recommendations to the human player according to the Decision Rules below.  The output should be formatted into the provided Output Schema.

## Trigger Conditions
This skill will be triggered after runs of the Trend Analyzer and Matchup Skills.  It should require output_trend_analyzer_*.csv, output_matchup_sunday_*.csv or output_matchup_wednesday_*.csv.  It will also require access to data_client.py for pulling additional data.

## Workflow
1. Load data: load latest output_matchup_*_*.csv file and latest output_trend_analyzer_*.csv file.  Additionally, load data_client.py and call get_batter_stats_pivoted() and get_pitcher_stats_pivoted()
2. Apply relevant decision rules below based on day of output_matchup_*.csv file
3. Produce output schema as described in decision rules

## Output Schema
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
    "first_half_target": 3,
     "second_half_target": 4,
     "used_this_week": 5,
     "remaining": 2
  },
  "confidence": "high",
  "rationale_summary": "one paragraph"
}

## Decision Rules
Sunday Run — Category Prioritization
Goal: Identify which players to prioritize for the 12 batter / 8 pitcher active roster based on the opponent's weak and medium categories.

1. Load output_matchup_sunday_*.csv. Identify all categories where category_rating = weak. These are Tier 1 target categories.
2. Identify all categories where category_rating = medium. These are Tier 2 target categories.
3. Categories where category_rating = strong are conceded — do not prioritize roster construction around these.
4. Call get_batter_stats_pivoted() and get_pitcher_stats_pivoted(). Rank players by season-long production in Tier 1 target categories first, then Tier 2.
5. For each ranked player, check their action_flag from output_trend_analyzer.csv:
    - If action_flag = drop_candidate or bench_today, do not exclude them from the priority ranking. Instead, note the conflict: "Prioritized for [category] production despite [action_flag] signal — Trend Analyzer flags [key_stat]."
    - This preserves both signals for the human to weigh, rather than silently discarding the Trend Analyzer's input.
6. Tie-breaking when two players rank within 10% in the same target category: prefer the player with the better Trend Analyzer status — hot or positive_regression outranks neutral, which outranks cold or negative_regression.
7. For any player not prioritized by category targeting, check their Trend Analyzer output:
    - If action_flag = drop_candidate AND recommendation_strength >= 2 AND no_drop = false → surface as a drop candidate for the human to review
    - If no_drop = true, do not surface as a drop candidate — instead surface as "trade candidate" per the no-drop override note already present in the Trend Analyzer output
8. Populating the Output Schema
    - Hardcode week to "12" for bootstrap
    - Mode = "full"
    - As_of = date of the run
    - Recommended_adds = [] (will add functionality later to get data on free agent pool of players and provide logic for this)
    - Recommended_drops = this will be players surfaced in rule 7.  Rationale will be key_stat and recommendation_strength 
    - Category_targets = weak and medium categories
    - Categories_punted = strong categories
    - Transaction budget =
        - first_half_target = 2
        - second_half_target = 5
        - used_this_week = 0
        - remaining = 7
    - Confidence
        - If there are at least 2 more Tier 1 than Tier 2 categories, then "high"
        - If number of Tier 1 categories is equal to more 1 more than Tier 2 categories, then "medium"
        - Else "low"
    - Rationale_summary - 3-5 sentences that address
        1. Which categories are being targeted this week and why (tier + opponent state)
        2. Which categories are being conceded and why
        3. Any player-level conflicts between Matchup priority and Trend Analyzer signal



Wednesday Run — Tactical Reprioritization
Goal: Adjust category focus based on the current state of the matchup rather than season-long opponent tendencies.

1. Load output_matchup_wednesday_*.csv. Classify categories into three action tiers:
    - Tier A (highest priority): status = vulnerable — opponent underperforming their historical floor; biggest opportunity to gain ground
    - Tier B: status = winning_close — defend; a small lead here is worth protecting
    - Tier C: status = losing_close — catchable; worth spending remaining transactions/lineup decisions to close the gap
    - Conceded: status = losing_badly — do not prioritize; spending resources here has low expected payoff this week
    - Secure: status = winning_comfortably — no action needed; do not spend transactions protecting an already-comfortable lead
2. Call get_batter_stats_pivoted() and get_pitcher_stats_pivoted(). Rank players by current production in Tier A categories first, then Tier B, then Tier C.
3. For each ranked player, check their action_flag from output_trend_analyzer.csv:
    - If action_flag = drop_candidate or bench_today, do not exclude them from the priority ranking. Note the conflict in the same format as the Sunday rule.
4. Tie-breaking: same rule as Sunday — prefer better Trend Analyzer status among similarly-ranked players.
5. For any player not prioritized by this tiering, check their Trend Analyzer output using the same drop-candidate logic as Sunday step 7.
6. Check the IP warning flag from output_matchup_wednesday_*.csv. If ip_warning = True, surface this prominently to the human — this takes priority over all other recommendations since missing the 20 IP threshold forfeits all pitching categories regardless of how well any individual category is being managed.
7. Populating the Output Schema
    - Hardcode week to "12" for bootstrap
    - Mode = "midweek"
    - As_of = date of the run
    - Recommended_adds = [] (will add functionality later to get data on free agent pool of players and provide logic for this)
    - Recommended_drops = this will be players surfaced as drop_candidate in rule 5.  Rationale will be key_stat and recommendation_strength 
    - Category_targets = Tier A, Tier B, and Tier C categories
    - Categories_punted = Conceded categories
    - Transaction budget =
        - first_half_target = 2
        - second_half_target = 5
        - used_this_week = transactions_used from matchup_state.csv
        - remaining = transactions_remaining from matchup_state.csv
    - Confidence
        - If 50% or more of category_targets are Tier A and more than 50% of Trend Analyzer recommendation_strength = 3, then "high"
        - If 30% or more category_targets are Tier A or Tier B and more than 30% of Trend Analayzer recommendation_strength >= 2, then "medium"
        - Else "low"
    - Rationale_summary - 3-5 sentences that address
        1. Which categories are being targeted this week and why (tier + opponent state)
        2. Which categories are being conceded and why
        3. Any player-level conflicts between Matchup priority and Trend Analyzer signal
        4. The IP warning status, if relevant
        5. One sentence on overall confidence and what would change it


## What This Skill Does NOT Do
The GM Agent will mostly pull and synthesize outputs from other agents, so it will delegate the following to other agents:
1. Trend Analyzer agent will look at player performance to determine if they are hot, cold, due for a positive or a negative regression and output this to the GM Agent
2. The Matchup agent will look at the opponent's strategy and strengths/weaknesses and report this to GM
3. Later, the Roster Management agent will figure out optimal roster slot and transaction usage and report this to the GM
4. Later, the Future Predictor will analyze upcoming matchups and parks to determine who should play and output this to the GM Agent. 
5. Does not evaluate free agents directly — relies on human judgment until FA pool integration exists. Does not override no-drop constraints.


## Example Output
{
  "week": 12,
  "mode": "midweek",
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
     "used_this_week": 3,
     "remaining": 4
  },
  "confidence": "medium",
  "rationale_summary": "HR, SB, K should be targeted due to being vulnerable, and R and ERA should be conceded because these are losing badly.  Salvador Perez has been a good source of HR, but has been cold lately, so he is a risk to put in the lineup.  Likewise, Michael King has been a source of K for the season, but his WHIP has been high lately.  IP is at 15.2 innings which is satisfactory at this stage of the week.  Overall, I have medium confidence as HR, SB, and K are Tier A categories and all have recommendation strength of 2 or 3."
}