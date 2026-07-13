# Matchup Eval

## Purpose
This eval is for the Matchup Agent whose job is two-fold:
1. On Sundays, we want to evaluate which categories of the fantasy opposing manager have the highest volatility based on historical data and predict which are most vulnerable due to the volatility.  The eval is assessing the accuracy of determining these categories by historic data and by volatility.
2. On Wednesdays, the Matchup Agent reviews the current category scores of both me and my opponent, then assess which I should focus on and which I should concede for the rest of the week's matchup.  The eval will assess how accurately the final scores reflect these recommendations.

## Eval Type

**Sunday Run Eval (Calibration)**
This eval will evaluate the the classification of each category based on historical performance and volatility and whether that classification was accurate relative to the final results of the matchup.

**Wednesday Run Eval (Outcome)**
This eval is determining whether the classification of winning_comfortably, winning_close, losing_close losing_comfortably, and vulnerable is accurate given the final score of the matchup.  In other words, does the logic for making these classifications give me correct signals about what stats to focus on in the second half of the matchup.

## Inputs

**Sunday run**
- get_opponent_roster(), get_opponent_history(), get_opponent_category_profile() from data_client.py

**Wednesday run**
- output_matchup_sunday_*.csv
- get_matchup_state(), get_category_gaps() from data_client.py

## Ground Truth
The ground truth will be a Parquet file called "scoreboard_history.parquet" which will contain the final scores of the matchup at the time this eval is run.

## Scoring Method

**Sunday run**
1. If the sunday_rating is medium, then sunday_correct will be "n/a"
2. If sunday_rating is weak and actual_winner is me, then sunday_correct is "yes"
3. If sunday_rating is weak and actual_winner is opp, then sunday_correct is "no"
4. If sunday_rating is weak and actual_winner is tie, then sunday_correct is "no"
5. If sunday_rating is strong and actual winner is me, then sunday_correct is "no"
6. If sunday_rating is strong and actual winner is opp, then sunday_correct is "yes"
7. If sunday_rating is strong and actual winner is tie, then sunday_correct is "no"

**Wednesday run**
1. If wednesday_status = winning_comfortably and actual_winner is me, then wednesday_correct is "yes"
2. If wednesday_status = winning_comfortably and actual_winner is opp, then wednesday_correct is "no"
3. If wednesday_status = winning_comfortably and actual_winner is tie, then wednesday_correct is "no"
4. If wednesday_status = winning_close and actual_winner is me, then wednesday_correct is "yes"
5. If wednesday_status = winning_close and actual_winner is opp, then wednesday_correct is "no"
6. If wednesday_status = winning_close and actual_winner is tie, then wednesday_correct is "n/a"
7. If wednesday_status = losing_close and actual_winner is me, then wednesday_correct is "no"
8. If wednesday_status = losing_close and actual_winner is opp, then wednesday_correct is "yes"
9. If wednesday_status = losing_close and actual_winner is tie, then wednesday_correct is "n/a"
10. If wednesday_status = losing_badly and actual_winner is me, then wednesday_correct is "no"
11. If wednesday_status = losing_badly and actual_winner is opp, then wednesday_correct is "yes"
12. If wednesday_status = losing_badly and actual_winner is tie, then wednesday_correct is "n/a"
13. If wednesday_status = vulnerable and actual_winner is me, then wednesday_correct is "yes"
14. If wednesday_status = vulnerable and actual_winner is opp, then wednesday_correct is "no"
15. If wednesday_status = vulnerable and actual_winner is tie, then wednesday_correct is "yes"

## Pass/Fail Threshold
For Sunday run, we should have 80% correct
For Wednesday run, we should have 90% correct

## Known Limitations
The eval is not able to separate out the weighting of the historic performance vs. the volatility in the recommendation against the final outcome, so this is left to human judgment for making any adjustments

## How to Run
1. On Sunday, save opponent history and the agent's output_matchup_sunday_*.csv
2. On Wednesday, save matchup_state and the agent's output_matchup_wednesday_*.csv
3. Run eval_runner.py after Sunday night data pull — it will auto-derive final scores from scoreboard_history.parquet.
4. Record week score in a summary log — pass/fail vs 80%/90% thresholds.  Also include the raw number of categories for the Sunday and Wednesday run that were scored appropriately (i.e. "Sunday: 7/9 scoreable categories correct (78%)")

## Example
For both Sunday and Wednesday scoring, actual_winner refers to who won that individual category, not the overall matchup.

category | sunday_rating | wednesday_status | final_my_score | final_opp_score | actual_winner | sunday_correct | wednesday_correct 
R | medium | winning_comfortably | 42 | 29 | me | n/a | yes