# Future Predictor Eval

## Purpose
This eval will not be run in its current form.  This is strictly to note the reasoning for not having an eval for the Future Predictor skill.

## Eval Type
N/A

## Inputs
N/A

## Ground Truth
N/A

## Scoring Method
N/A 

In the future, I may implement a check for two-start pitchers, but felt that this was not needed at this time since we are simply taking data from the MLB API in the Rust binary and counting whether a pitcher is listed twice in the next 7 days as a probable starting pitcher.

## Pass/Fail Threshold
N/A

## Known Limitations
The Future Predictor outputs 3 multipliers per player.  Each of these is deterministic, so I chose not to do an eval for them.
1. games_multiplier is just a scalar of games played / 7.  This is just meant to measure whether a player is scheduled to play a full slate of games or has off days scheduled in the week.
2. two_start_multipler (as mentioned above) is just whether the MLB API's probable starters lists a pitcher as expected to start 2 games in the upcoming week
3. park_factors_multiplier is based on park factors that are collected at the start of the season and is also just a mathematical formula based on those park factors.

## How to Run
N/A

## Example
N/A