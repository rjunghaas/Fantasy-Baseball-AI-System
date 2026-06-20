# GM Eval

## Purpose
[What does this eval measure and why does it matter?]

This eval is for the GM Agent which synthesizes recommendations from the Trend Analyzer Agent and Matchup Agents.  It will look at 2 things: a) the category recommendations on Sunday and Wednesday and whether these are accurate for targeting and b) drop_candidates and whether these are accurate given the future performance of that player.

## Eval Type
[Which of the five eval types from SKILL.md does this cover?
Outcome / Calibration / Consistency / Retrieval / Counterfactual]

**Sunday recommendations (Calibration)**
This eval is to determine if the Sunday category recommendations are accurate.  If the GM recommends targeting the category, is the opponent actually vulnerable?

**Wednesday recommendations (Outcome)**
This eval is to determine if the Wednesday recommendations to target or concede categories is accurate.

**Drop Candidates (Calibration)**
This eval is to determine if the players that the GM recommends as "Drop Candidates" continue to produce poor results.

## Inputs
[What data goes in — which files, which agent output]

**Sunday recommendations**
1. output_matchup_sunday_*.csv
2. get_batter_stats_pivoted() and get_pitcher_stats_pivoted() from data_client.py

**Wednesday recommendations**
1. output_matchup_wednesday_*.csv
2. get_batter_stats_pivoted() and get_pitcher_stats_pivoted() from data_client.py

**Drop Candidates**
1. output_trend_analyzer_*.csv

## Ground Truth
[How do you know what the right answer is?
What are you comparing the agent output against?]

**Sunday and Wednesday recommendations**
1. gm_eval.csv

**Drop candidates**
1. my_stats_*eval.csv

## Scoring Method
[How do you score a single recommendation — what counts as correct, 
partial credit, or wrong?]

**Sunday and Wednesday recommendations**
- target → correct if actual_winner = me
- punt → correct if actual_winner = opp
- secure / n/a → excluded from scoring
- ERA and WHIP: if my pitchers had an atypically bad week (ERA > 5.00), 
  mark as n/a rather than incorrect — execution failure, not agent logic failure.
  Document the reason in notes column.

**Drop Candidates**
- correct if player's jun13_19 stats confirm the drop signal 
  (e.g., cold batter: 7d OBP remains below season OBP by > 0.040)
- inconclusive if player was injured during the eval window
- wrong if player performed at or above their season baseline
Score = correct / (correct + wrong), inconclusives excluded

## Pass/Fail Threshold
Sunday recommendations should be correct 80% of the time
Wednesday recommendations should be correct 90% of the time
Drop candidates should be correct 70% of the time

## Known Limitations
The GM Agent will later have sub-agents built for Roster Management and Future Predictions (leveraging matchups and park factors).  The GM does not have access to this data now.  As mentioned in the Matchup Eval.md, there is not benchmarking data for the specific categories at this time either.

## How to Run
1. After each GM run, save decisions_YYYYMMDD.json
2. At week end, populate gm_eval.csv with final category scores
3. Apply scoring rules above — mark n/a for pitch execution weeks
4. For drop candidates, cross-reference my_stats_*eval.csv 7d window
5. Record Sunday %, Wednesday %, and drop candidate % separately

## Example
category | gm_recommendation_sunday | gm_tier_sunday | gm_recommendation_wednesday | gm_tier_wednesday | final_my_score | final_opp_score | actual_winner | recommendation_correct_sunday | recommendation_correct_wednesday
r | target | 2 | n/a | secure | 42 | 29 | me | yes | yes