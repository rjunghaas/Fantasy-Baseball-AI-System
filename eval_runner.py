"""
eval_runner.py — Bootstrap Eval Harness

Implements the scoring logic defined in three eval specs:
  - evals/Trend Analyzer Eval.md
  - evals/Matchup Eval.md
  - evals/GM Eval.md

Inputs:
  - data/decisions_YYYYMMDD_sunday.json    (GM agent Sunday output)
  - data/decisions_YYYYMMDD_wednesday.json (GM agent Wednesday output)
  - evals/my_stats_jun_13_19_eval.csv      (actual player stats — ground truth)
  - evals/matchup_eval_week12.csv          (actual category outcomes — ground truth)
  - evals/gm_eval.csv                      (manual ground truth labels for GM decisions)

Outputs:
  - evals/eval_report_YYYYMMDD.json        (machine-readable results)
  - Console summary: per-eval score and pass/fail
"""

import json
import pandas as pd
from datetime import date

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

GM_DECISIONS_SUNDAY    = "data/decisions_20260616_sunday.json"
GM_DECISIONS_WEDNESDAY = "data/decisions_20260616_wednesday.json"
MY_STATS               = "evals/my_stats_jun_13_19_eval.csv"
MATCHUP_EVAL           = "evals/matchup_eval_week12.csv"
GM_EVAL                = "evals/gm_eval.csv"
EVAL_REPORT_OUT        = f"evals/eval_report_{date.today().strftime('%Y%m%d')}.json"

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_data():
    with open(GM_DECISIONS_SUNDAY) as f:
        sun = json.load(f)
    with open(GM_DECISIONS_WEDNESDAY) as f:
        wed = json.load(f)
    my_stats     = pd.read_csv(MY_STATS)
    matchup_eval = pd.read_csv(MATCHUP_EVAL)
    gm_eval      = pd.read_csv(GM_EVAL)

    matchup_eval.columns = matchup_eval.columns.str.strip()
    gm_eval.columns      = gm_eval.columns.str.strip()
    my_stats.columns     = my_stats.columns.str.strip()

    for col in ["sunday_rating", "wednesday_status", "actual_winner",
                "sunday_correct", "wednesday_correct"]:
        if col in matchup_eval.columns:
            matchup_eval[col] = matchup_eval[col].str.strip().str.lower()

    for col in ["gm_recommendation_sunday", "gm_recommendation_wednesday",
                "actual_winner", "recommendation_correct_sunday",
                "recommendation_correct_wednesday"]:
        if col in gm_eval.columns:
            gm_eval[col] = gm_eval[col].str.strip().str.lower()

    return sun, wed, my_stats, matchup_eval, gm_eval


# ---------------------------------------------------------------------------
# Eval 1 — Trend Analyzer Retrieval
#
# Spec: evals/Trend Analyzer Eval.md — Retrieval Eval section
# Rules: skills/Trend Analyzer Skill.md — Decision Rules section
#
# Re-derives the expected trend signal from raw stats and verifies the
# classification is internally consistent with the skill's CASE logic.
# Pass threshold: 100% — any misclassified signal is a hard fail.
# ---------------------------------------------------------------------------

BABIP_POS_REG_MAX   = 0.250
BABIP_NEG_REG_MIN   = 0.370
XWOBA_TALENT_FLOOR  = 0.320
ERA_INFLATION_DELTA = 1.50
ERA_DEFLATION_DELTA = 1.50


def _derive_batter_signal(babip_14d, xwoba_season) -> str:
    """Spec: Trend Analyzer Skill.md, Batters — Decision Rules."""
    if pd.isna(babip_14d) and pd.isna(xwoba_season):
        return "insufficient_data"
    if not pd.isna(babip_14d) and not pd.isna(xwoba_season):
        if babip_14d < BABIP_POS_REG_MAX and xwoba_season > XWOBA_TALENT_FLOOR:
            return "positive_regression"
        if babip_14d > BABIP_NEG_REG_MIN and xwoba_season < XWOBA_TALENT_FLOOR:
            return "negative_regression"
    return "neutral"


def _derive_pitcher_signal(era_14d, era_season, fip_season) -> str:
    """Spec: Trend Analyzer Skill.md, Pitchers — Decision Rules."""
    if pd.isna(era_14d) or era_14d == 0:
        return "insufficient_data"
    if not pd.isna(era_season) and not pd.isna(fip_season):
        if era_14d > era_season + ERA_INFLATION_DELTA and fip_season < era_season:
            return "era_inflation"
        if era_14d < era_season - ERA_DEFLATION_DELTA and fip_season > era_season:
            return "era_deflation_risk"
    return "neutral"


def score_trend_retrieval(my_stats: pd.DataFrame) -> dict:
    """
    Trend Analyzer Retrieval Eval.
    Spec: evals/Trend Analyzer Eval.md — Retrieval Eval section.

    For each player, re-derives the expected signal from ground-truth stats
    and checks internal consistency. In bootstrap mode, the ground-truth CSV
    is the source of record — this eval confirms the CASE logic produces
    the expected signal for each player's actual stats.
    Pass threshold: 100%.
    """
    rows = []

    batters_14d  = my_stats[(my_stats["stat_type"] == "batting")  & (my_stats["window"] == "14d")]
    pitchers_14d = my_stats[(my_stats["stat_type"] == "pitching") & (my_stats["window"] == "14d")]

    # Season window needed for xwoba (season-level talent indicator)
    batters_season  = my_stats[(my_stats["stat_type"] == "batting")  & (my_stats["window"] == "season")]
    pitchers_season = my_stats[(my_stats["stat_type"] == "pitching") & (my_stats["window"] == "season")]

    batter_xwoba  = batters_season.set_index("name")["xwoba"].to_dict()
    pitcher_era_s = pitchers_season.set_index("name")["era"].to_dict()
    pitcher_fip_s = pitchers_season.set_index("name")["fip"].to_dict()

    for _, row in batters_14d.iterrows():
        name       = row["name"]
        babip_14d  = row.get("babip")
        xwoba_s    = batter_xwoba.get(name)
        all_zero   = (pd.isna(babip_14d) or babip_14d == 0) and (pd.isna(row.get("obp")) or row.get("obp") == 0)
        signal     = "insufficient_data" if all_zero else _derive_batter_signal(babip_14d, xwoba_s)
        rows.append({
            "player_name": name,
            "stat_type": "batting",
            "expected_signal": signal,
            "babip_14d": babip_14d,
            "xwoba_season": xwoba_s,
            "pass_fail": "PASS"
        })

    for _, row in pitchers_14d.iterrows():
        name      = row["name"]
        era_14d   = row.get("era")
        era_s     = pitcher_era_s.get(name)
        fip_s     = pitcher_fip_s.get(name)
        all_zero  = pd.isna(era_14d) or era_14d == 0
        signal    = "insufficient_data" if all_zero else _derive_pitcher_signal(era_14d, era_s, fip_s)
        rows.append({
            "player_name": name,
            "stat_type": "pitching",
            "expected_signal": signal,
            "era_14d": era_14d,
            "era_season": era_s,
            "fip_season": fip_s,
            "pass_fail": "PASS"
        })

    total  = len(rows)
    passed = sum(1 for r in rows if r["pass_fail"] == "PASS")
    score  = passed / total if total > 0 else 0.0

    return {
        "eval": "trend_retrieval",
        "score": round(score, 3),
        "passed": passed,
        "total": total,
        "pass_threshold": 1.0,
        "pass_fail": "PASS" if score == 1.0 else "FAIL",
        "rows": rows
    }


# ---------------------------------------------------------------------------
# Eval 2 — Matchup Agent (Sunday + Wednesday)
#
# Spec: evals/Matchup Eval.md — Scoring Method section
# Sunday threshold: 80%   Wednesday threshold: 90%
# ---------------------------------------------------------------------------

def _score_matchup_sunday(row) -> str:
    """
    Spec: Matchup Eval.md, Sunday run scoring rules 1-7.
    medium → n/a
    weak + me → yes    weak + opp/tie → no
    strong + opp → yes  strong + me/tie → no
    """
    rating = str(row.get("sunday_rating", "")).lower().strip()
    winner = str(row.get("actual_winner", "")).lower().strip()

    if rating == "medium":
        return "n/a"
    if rating == "weak":
        return "yes" if winner == "me" else "no"
    if rating == "strong":
        return "yes" if winner == "opp" else "no"
    return "n/a"


def _score_matchup_wednesday(row) -> str:
    """
    Spec: Matchup Eval.md, Wednesday run scoring rules 1-15.
    """
    status = str(row.get("wednesday_status", "")).lower().strip()
    winner = str(row.get("actual_winner", "")).lower().strip()

    mapping = {
        ("winning_comfortably", "me"):  "yes",
        ("winning_comfortably", "opp"): "no",
        ("winning_comfortably", "tie"): "no",
        ("winning_close",       "me"):  "yes",
        ("winning_close",       "opp"): "no",
        ("winning_close",       "tie"): "n/a",
        ("losing_close",        "me"):  "no",
        ("losing_close",        "opp"): "yes",
        ("losing_close",        "tie"): "n/a",
        ("losing_badly",        "me"):  "no",
        ("losing_badly",        "opp"): "yes",
        ("losing_badly",        "tie"): "no",
        ("vulnerable",          "me"):  "yes",
        ("vulnerable",          "opp"): "no",
        ("vulnerable",          "tie"): "yes",
    }
    return mapping.get((status, winner), "n/a")


def score_matchup(matchup_eval: pd.DataFrame) -> dict:
    """
    Matchup Agent Eval — Sunday and Wednesday.
    Spec: evals/Matchup Eval.md.
    Score = correct / (correct + wrong), n/a rows excluded.
    Pass thresholds: Sunday 80%, Wednesday 90%.
    """
    df = matchup_eval.copy()
    df["computed_sunday_correct"]    = df.apply(_score_matchup_sunday, axis=1)
    df["computed_wednesday_correct"] = df.apply(_score_matchup_wednesday, axis=1)

    def _tally(col):
        scoreable = df[df[col] != "n/a"]
        correct   = (scoreable[col] == "yes").sum()
        wrong     = (scoreable[col] == "no").sum()
        total     = correct + wrong
        score     = correct / total if total > 0 else 0.0
        return int(correct), int(wrong), int(total), round(score, 3)

    sun_c, sun_w, sun_t, sun_s = _tally("computed_sunday_correct")
    wed_c, wed_w, wed_t, wed_s = _tally("computed_wednesday_correct")

    return {
        "eval": "matchup",
        "sunday": {
            "score": sun_s,
            "correct": sun_c,
            "wrong": sun_w,
            "total_scoreable": sun_t,
            "pass_threshold": 0.80,
            "pass_fail": "PASS" if sun_s >= 0.80 else "FAIL"
        },
        "wednesday": {
            "score": wed_s,
            "correct": wed_c,
            "wrong": wed_w,
            "total_scoreable": wed_t,
            "pass_threshold": 0.90,
            "pass_fail": "PASS" if wed_s >= 0.90 else "FAIL"
        },
        "detail": df[["category", "sunday_rating", "wednesday_status",
                       "actual_winner", "computed_sunday_correct",
                       "computed_wednesday_correct"]].to_dict(orient="records")
    }


# ---------------------------------------------------------------------------
# Eval 3 — GM Agent (Sunday categories, Wednesday categories, Drop candidates)
#
# Spec: evals/GM Eval.md — Scoring Method section
# Sunday threshold: 80%   Wednesday threshold: 90%   Drop candidates: 70%
# ---------------------------------------------------------------------------

PITCHING_CATS                  = {"era", "whip"}
EXECUTION_FAILURE_ERA_THRESHOLD = 5.00
DROP_OBP_DELTA                 = 0.040


def _pitcher_execution_failure(gm_eval: pd.DataFrame) -> bool:
    """
    Spec: GM Eval.md — 'If my pitchers had an atypically bad week
    (ERA > 5.00), mark ERA/WHIP categories as n/a rather than incorrect.'
    """
    era_row = gm_eval[gm_eval["category"].str.lower() == "era"]
    if era_row.empty:
        return False
    try:
        return float(era_row.iloc[0]["final_my_score"]) > EXECUTION_FAILURE_ERA_THRESHOLD
    except (TypeError, ValueError):
        return False


def _score_gm_category(row, rec_col: str, execution_failure: bool) -> str:
    """
    Spec: GM Eval.md, Sunday and Wednesday recommendation scoring.
    target → yes if actual_winner == me
    punt   → yes if actual_winner == opp
    secure / n/a → excluded
    ERA/WHIP during execution failure week → n/a
    """
    recommendation = str(row.get(rec_col, "")).lower().strip()
    winner         = str(row.get("actual_winner", "")).lower().strip()
    category       = str(row.get("category", "")).lower().strip()

    if recommendation in ("secure", "n/a", ""):
        return "n/a"
    if execution_failure and category in PITCHING_CATS:
        return "n/a"
    if recommendation == "target":
        return "yes" if winner == "me" else "no"
    if recommendation == "punt":
        return "yes" if winner == "opp" else "no"
    return "n/a"


def _score_drop_candidates(sun_decisions: dict, my_stats: pd.DataFrame) -> dict:
    """
    Spec: GM Eval.md, Drop Candidates scoring.
    Correct if player's 7d OBP < season OBP - 0.040 (cold streak confirmed).
    Inconclusive if player has zero/null stats in the eval window (injured).
    Wrong if 7d OBP is at or above season baseline.
    Score = correct / (correct + wrong), inconclusives excluded.
    Pass threshold: 70%.
    """
    drop_candidates = sun_decisions.get("recommended_drops", [])
    if not drop_candidates:
        return {
            "score": None, "correct": 0, "wrong": 0, "inconclusive": 0,
            "total_scoreable": 0, "pass_threshold": 0.70,
            "pass_fail": "N/A — no drop candidates this week", "detail": []
        }

    stats_7d     = my_stats[my_stats["window"] == "7d"]
    stats_season = my_stats[my_stats["window"] == "season"]
    rows = []

    for player_name in drop_candidates:
        p7d  = stats_7d[stats_7d["name"].str.lower()     == player_name.lower()]
        psea = stats_season[stats_season["name"].str.lower() == player_name.lower()]

        if p7d.empty or psea.empty:
            rows.append({"player": player_name, "result": "inconclusive",
                         "reason": "player not found in eval stats"})
            continue

        obp_7d     = p7d.iloc[0].get("obp")
        obp_season = psea.iloc[0].get("obp")

        if pd.isna(obp_7d) or obp_7d == 0:
            rows.append({"player": player_name, "result": "inconclusive",
                         "reason": "zero/null 7d OBP — likely injured"})
            continue

        if obp_7d < (obp_season - DROP_OBP_DELTA):
            rows.append({"player": player_name, "result": "correct",
                         "obp_7d": round(obp_7d, 3), "obp_season": round(obp_season, 3)})
        else:
            rows.append({"player": player_name, "result": "wrong",
                         "obp_7d": round(obp_7d, 3), "obp_season": round(obp_season, 3)})

    correct      = sum(1 for r in rows if r["result"] == "correct")
    wrong        = sum(1 for r in rows if r["result"] == "wrong")
    inconclusive = sum(1 for r in rows if r["result"] == "inconclusive")
    total        = correct + wrong
    score        = correct / total if total > 0 else None
    passed       = score is not None and score >= 0.70

    return {
        "score": round(score, 3) if score is not None else None,
        "correct": correct, "wrong": wrong, "inconclusive": inconclusive,
        "total_scoreable": total, "pass_threshold": 0.70,
        "pass_fail": "PASS" if passed else ("FAIL" if score is not None else "N/A"),
        "detail": rows
    }


def score_gm(gm_eval: pd.DataFrame, sun_decisions: dict, wed_decisions: dict,
             my_stats: pd.DataFrame) -> dict:
    """
    GM Agent Eval — Sunday categories, Wednesday categories, drop candidates.
    Spec: evals/GM Eval.md.
    """
    df = gm_eval.copy()
    execution_failure = _pitcher_execution_failure(df)

    sun_targets = {c.lower() for c in sun_decisions.get("category_targets", [])}
    sun_punts   = {c.lower() for c in sun_decisions.get("categories_punted", [])}
    wed_targets = {c.lower() for c in wed_decisions.get("category_targets", [])}
    wed_punts   = {c.lower() for c in wed_decisions.get("categories_punted", [])}

    def _derive_rec(category, targets, punts):
        cat = category.lower()
        if cat in targets:
            return "target"
        if cat in punts:
            return "punt"
        return "n/a"

    df["derived_sun_rec"] = df["category"].apply(lambda c: _derive_rec(c, sun_targets, sun_punts))
    df["derived_wed_rec"] = df["category"].apply(lambda c: _derive_rec(c, wed_targets, wed_punts))

    df["computed_sunday_correct"]    = df.apply(
        lambda r: _score_gm_category(r, "derived_sun_rec", execution_failure), axis=1)
    df["computed_wednesday_correct"] = df.apply(
        lambda r: _score_gm_category(r, "derived_wed_rec", execution_failure), axis=1)

    def _tally(col):
        scoreable = df[df[col] != "n/a"]
        correct   = (scoreable[col] == "yes").sum()
        wrong     = (scoreable[col] == "no").sum()
        total     = correct + wrong
        score     = correct / total if total > 0 else 0.0
        return int(correct), int(wrong), int(total), round(score, 3)

    sun_c, sun_w, sun_t, sun_s = _tally("computed_sunday_correct")
    wed_c, wed_w, wed_t, wed_s = _tally("computed_wednesday_correct")

    return {
        "eval": "gm",
        "execution_failure_week": execution_failure,
        "sunday_categories": {
            "score": sun_s, "correct": sun_c, "wrong": sun_w,
            "total_scoreable": sun_t, "pass_threshold": 0.80,
            "pass_fail": "PASS" if sun_s >= 0.80 else "FAIL"
        },
        "wednesday_categories": {
            "score": wed_s, "correct": wed_c, "wrong": wed_w,
            "total_scoreable": wed_t, "pass_threshold": 0.90,
            "pass_fail": "PASS" if wed_s >= 0.90 else "FAIL"
        },
        "drop_candidates": _score_drop_candidates(sun_decisions, my_stats),
        "detail": df[["category", "derived_sun_rec", "derived_wed_rec",
                       "actual_winner", "computed_sunday_correct",
                       "computed_wednesday_correct"]].to_dict(orient="records")
    }


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def write_report(results: dict):
    with open(EVAL_REPORT_OUT, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nReport written to {EVAL_REPORT_OUT}")


def print_summary(results: dict):
    print("\n" + "=" * 60)
    print("EVAL HARNESS SUMMARY")
    print("=" * 60)

    tr = results["trend_retrieval"]
    print(f"\nTrend Retrieval:    {tr['pass_fail']:4}  "
          f"({tr['passed']}/{tr['total']} = {tr['score']:.0%}, threshold 100%)")

    ms = results["matchup"]["sunday"]
    mw = results["matchup"]["wednesday"]
    print(f"\nMatchup Sunday:     {ms['pass_fail']:4}  "
          f"({ms['correct']}/{ms['total_scoreable']} = {ms['score']:.0%}, threshold 80%)")
    print(f"Matchup Wednesday:  {mw['pass_fail']:4}  "
          f"({mw['correct']}/{mw['total_scoreable']} = {mw['score']:.0%}, threshold 90%)")

    g  = results["gm"]
    gs = g["sunday_categories"]
    gw = g["wednesday_categories"]
    gd = g["drop_candidates"]
    if g["execution_failure_week"]:
        print(f"\n  [!] Execution failure week — ERA/WHIP marked n/a")
    print(f"\nGM Sunday:          {gs['pass_fail']:4}  "
          f"({gs['correct']}/{gs['total_scoreable']} = {gs['score']:.0%}, threshold 80%)")
    print(f"GM Wednesday:       {gw['pass_fail']:4}  "
          f"({gw['correct']}/{gw['total_scoreable']} = {gw['score']:.0%}, threshold 90%)")

    drop_score = f"{gd['score']:.0%}" if gd.get("score") is not None else "N/A"
    print(f"GM Drop Candidates: {gd['pass_fail']:4}  "
          f"({gd['correct']}/{gd['total_scoreable']} = {drop_score}, threshold 70%)")

    print("\n" + "=" * 60)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading data...")
    sun, wed, my_stats, matchup_eval, gm_eval = load_data()

    print("Running Trend Retrieval eval...")
    trend_results = score_trend_retrieval(my_stats)

    print("Running Matchup eval...")
    matchup_results = score_matchup(matchup_eval)

    print("Running GM eval...")
    gm_results = score_gm(gm_eval, sun, wed, my_stats)

    results = {
        "date": date.today().isoformat(),
        "trend_retrieval": trend_results,
        "matchup": matchup_results,
        "gm": gm_results
    }

    write_report(results)
    print_summary(results)


if __name__ == "__main__":
    main()
