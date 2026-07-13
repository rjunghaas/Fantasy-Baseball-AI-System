"""
eval_runner.py — Full Eval Harness

Implements scoring logic from:
  evals/Matchup Eval.md
  evals/Trend Analyzer Eval.md
  evals/Roster Management Eval.md
  evals/GM Eval.md

Run after Sunday night data pull, before running agents for the new week.

File resolution:
  - Decisions JSONs are matched by their "mode" field ("sunday" / "midweek").
  - Previous-week agent outputs (RM CSVs, TA CSVs, matchup CSVs) are identified
    by the as_of date in the Sunday decisions JSON.
  - Ground truth parquets (7d window) are always the latest file in data/.
  - "Last week's 7d stats" (for Trend Analyzer improvement count) are the
    second-to-latest pybaseball parquet — both Sundays' files must be present.

Output: evals/eval_report_YYYYMMDD.json  +  console summary
"""

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR  = Path("data")
EVALS_DIR = Path("evals")

# Column names used by pybaseball parquets (lowercase)
BATTER_COLS  = {"R": "r", "HR": "hr", "RBI": "rbi", "SB": "sb", "OBP": "obp"}
PITCHER_COLS = {"W": "w", "SV": "sv", "K": "k",   "ERA": "era", "WHIP": "whip"}
LOWER_IS_BETTER = {"ERA", "WHIP"}

BATTER_CATS  = list(BATTER_COLS.keys())   # ["R","HR","RBI","SB","OBP"]
PITCHER_CATS = list(PITCHER_COLS.keys())  # ["W","SV","K","ERA","WHIP"]


# ─────────────────────────────────────────────────────────────────────────────
# File helpers
# ─────────────────────────────────────────────────────────────────────────────

def _latest(prefix: str, ext: str, directory: Path = DATA_DIR) -> Path:
    files = sorted(directory.glob(f"{prefix}_*.{ext}"))
    if not files:
        raise FileNotFoundError(f"No {prefix}_*.{ext} in {directory}/")
    return files[-1]


def _second_latest(prefix: str, ext: str, directory: Path = DATA_DIR) -> Path:
    files = sorted(directory.glob(f"{prefix}_*.{ext}"))
    if len(files) < 2:
        raise FileNotFoundError(
            f"Need ≥2 files matching {prefix}_*.{ext} in {directory}/ "
            f"(latest = ground truth; second-latest = prior week stats)"
        )
    return files[-2]


def _for_date(prefix: str, dt: str, ext: str, directory: Path = DATA_DIR) -> Path:
    path = directory / f"{prefix}_{dt}.{ext}"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    return path


def _load_decisions_by_mode(mode: str) -> tuple[str, dict]:
    """
    Find the most recent decisions JSON whose 'mode' field matches.
    'wednesday' matches both 'midweek' and 'wednesday'.
    Returns (date_str, data).
    """
    candidates = []
    for p in sorted(DATA_DIR.glob("decisions_*.json")):
        try:
            with open(p) as f:
                d = json.load(f)
            m = d.get("mode", "").lower()
            if mode == "sunday" and m == "sunday":
                candidates.append((p.stem.split("_")[1], d))
            elif mode == "wednesday" and m in ("midweek", "wednesday"):
                candidates.append((p.stem.split("_")[1], d))
        except Exception:
            continue
    if not candidates:
        raise FileNotFoundError(f"No decisions JSON with mode='{mode}' found in {DATA_DIR}/")
    return candidates[-1]


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = df.columns.str.strip()
    return df


def _read_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _tally(results: list) -> tuple[int, int, int, float]:
    """items are 'yes'/'no'/'n/a'. Returns (correct, wrong, total_scoreable, score)."""
    scoreable = [r for r in results if r != "n/a"]
    correct   = sum(1 for r in scoreable if r == "yes")
    wrong     = sum(1 for r in scoreable if r == "no")
    total     = correct + wrong
    score     = correct / total if total > 0 else 0.0
    return correct, wrong, total, round(score, 3)


def _category_winner(my_val, opp_val, lower_is_better: bool) -> str:
    if pd.isna(my_val) or pd.isna(opp_val):
        return "unknown"
    if lower_is_better:
        if float(my_val) < float(opp_val):  return "me"
        if float(my_val) > float(opp_val):  return "opp"
        return "tie"
    else:
        if float(my_val) > float(opp_val):  return "me"
        if float(my_val) < float(opp_val):  return "opp"
        return "tie"


def _loss_margin_pct(my_val, opp_val, lower_is_better: bool) -> float:
    """Positive = I am losing by this fraction. Negative = I am winning."""
    if pd.isna(my_val) or pd.isna(opp_val) or float(opp_val) == 0:
        return 0.0
    if lower_is_better:
        return float((float(my_val) - float(opp_val)) / abs(float(opp_val)))
    else:
        return float((float(opp_val) - float(my_val)) / abs(float(opp_val)))


def _load_matchup_scores(week: int) -> dict:
    """
    Load per-category final scores for the given week from scoreboard_history.parquet.
    Returns {CATEGORY_UPPER: {"my_value": float, "opp_value": float, "lower_is_better": bool}}.
    """
    path = DATA_DIR / "scoreboard_history.parquet"
    df   = _read_parquet(path)
    df["week"] = pd.to_numeric(df["week"], errors="coerce").astype("Int64")
    wk  = df[df["week"] == int(week)]
    if wk.empty:
        raise ValueError(f"No scoreboard_history data for week {week}")
    result = {}
    for _, row in wk.iterrows():
        cat = str(row.get("stat_name", "")).upper().strip()
        result[cat] = {
            "my_value":        float(row.get("my_value",  0) or 0),
            "opp_value":       float(row.get("opp_value", 0) or 0),
            "lower_is_better": bool(row.get("lower_is_better", False)),
        }
    return result


def _build_stats_index(parquet_path: Path, window: str) -> dict:
    """
    Build {player_id_or_name: {col_lower: value}} for one window from a pybaseball parquet.
    Both player_id and name keys are stored so lookups work either way.
    """
    try:
        df = _read_parquet(parquet_path)
        df = df[df["window"] == window]
        idx = {}
        for _, row in df.iterrows():
            entry = {c.lower(): row[c] for c in df.columns}
            for key_col in ("player_id", "name"):
                k = str(row.get(key_col, "")).strip().lower()
                if k:
                    idx[k] = entry
        return idx
    except Exception:
        return {}


def _lookup_player(idx: dict, player_id: str, name: str) -> dict:
    return (idx.get(str(player_id).strip().lower())
            or idx.get(str(name).strip().lower())
            or {})


# ─────────────────────────────────────────────────────────────────────────────
# Eval 1 — Matchup Agent
# Spec: evals/Matchup Eval.md
# ─────────────────────────────────────────────────────────────────────────────

def _matchup_sunday_result(rating: str, winner: str) -> str:
    r = rating.lower().strip()
    if r == "medium":  return "n/a"
    if r == "weak":    return "yes" if winner == "me" else "no"
    if r == "strong":  return "yes" if winner == "opp" else "no"
    return "n/a"


def _matchup_wednesday_result(status: str, winner: str) -> str:
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
        ("losing_badly",        "tie"): "n/a",
        ("vulnerable",          "me"):  "yes",
        ("vulnerable",          "opp"): "no",
        ("vulnerable",          "tie"): "yes",
    }
    return mapping.get((status.lower().strip(), winner.lower().strip()), "n/a")


def score_matchup(sun_date: str, week: int) -> dict:
    """
    Spec: evals/Matchup Eval.md
    actual_winner is the CATEGORY winner, not the overall matchup winner.
    Sunday threshold: 80%.  Wednesday threshold: 90%.
    """
    try:
        scores = _load_matchup_scores(week)
    except Exception as e:
        return {"eval": "matchup", "error": str(e)}

    out = {"eval": "matchup", "week": week}

    # ── Sunday ────────────────────────────────────────────────────────────────
    try:
        sun_csv = _read_csv(_for_date("output_matchup_sunday", sun_date, "csv"))
        sun_csv.columns = [c.lower() for c in sun_csv.columns]
        cat_col    = next((c for c in sun_csv.columns if c in ("category", "stat_name")), sun_csv.columns[0])
        rating_col = next((c for c in sun_csv.columns if "rating" in c), None)

        sun_rows = []
        if rating_col:
            for _, row in sun_csv.iterrows():
                cat    = str(row[cat_col]).upper().strip()
                rating = str(row.get(rating_col, "medium")).strip()
                sc     = scores.get(cat, {})
                my_v   = sc.get("my_value", 0)
                opp_v  = sc.get("opp_value", 0)
                lib    = sc.get("lower_is_better", False)
                winner = _category_winner(my_v, opp_v, lib)
                result = _matchup_sunday_result(rating, winner)
                sun_rows.append({
                    "category": cat, "sunday_rating": rating,
                    "my_score": my_v, "opp_score": opp_v,
                    "winner": winner, "result": result,
                })

        c, w, t, s = _tally([r["result"] for r in sun_rows])
        out["sunday"] = {
            "score": s, "correct": c, "wrong": w, "total_scoreable": t,
            "pass_threshold": 0.80,
            "pass_fail": "PASS" if s >= 0.80 else "FAIL",
            "detail": sun_rows,
        }
    except FileNotFoundError as e:
        out["sunday"] = {"error": str(e)}

    # ── Wednesday ─────────────────────────────────────────────────────────────
    try:
        wed_csv = _read_csv(_latest("output_matchup_wednesday", "csv"))
        wed_csv.columns = [c.lower() for c in wed_csv.columns]
        cat_col    = next((c for c in wed_csv.columns if c in ("category", "stat_name")), wed_csv.columns[0])
        status_col = next((c for c in wed_csv.columns if "status" in c or "wednesday" in c), None)

        wed_rows = []
        if status_col:
            for _, row in wed_csv.iterrows():
                cat    = str(row[cat_col]).upper().strip()
                status = str(row.get(status_col, "")).strip()
                sc     = scores.get(cat, {})
                my_v   = sc.get("my_value", 0)
                opp_v  = sc.get("opp_value", 0)
                lib    = sc.get("lower_is_better", False)
                winner = _category_winner(my_v, opp_v, lib)
                result = _matchup_wednesday_result(status, winner)
                wed_rows.append({
                    "category": cat, "wednesday_status": status,
                    "my_score": my_v, "opp_score": opp_v,
                    "winner": winner, "result": result,
                })

        c, w, t, s = _tally([r["result"] for r in wed_rows])
        out["wednesday"] = {
            "score": s, "correct": c, "wrong": w, "total_scoreable": t,
            "pass_threshold": 0.90,
            "pass_fail": "PASS" if s >= 0.90 else "FAIL",
            "detail": wed_rows,
        }
    except FileNotFoundError as e:
        out["wednesday"] = {"error": str(e)}

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Eval 2 — Trend Analyzer
# Spec: evals/Trend Analyzer Eval.md
# ─────────────────────────────────────────────────────────────────────────────

def _improvement_count(stats_this: dict, stats_last: dict, stat_type: str) -> int:
    """
    Count categories that improved this week vs last week (0–5).
    ERA/WHIP: improvement = lower value this week.
    All other stats: improvement = higher or equal value this week.
    Returns -1 if no data available for comparison.
    """
    col_map = BATTER_COLS if stat_type == "batting" else PITCHER_COLS
    count, valid = 0, 0
    for cat, col in col_map.items():
        this_v = stats_this.get(col)
        last_v = stats_last.get(col)
        if this_v is None or last_v is None or pd.isna(this_v) or pd.isna(last_v):
            continue
        valid += 1
        if cat in LOWER_IS_BETTER:
            if float(this_v) < float(last_v):
                count += 1
        else:
            if float(this_v) >= float(last_v):
                count += 1
    return count if valid > 0 else -1


def _trend_signal_result(signal: str, imp: int, era_this, era_last) -> str:
    """
    Spec: Trend Analyzer Eval.md — Trend_Signal scoring.
    era_inflation / era_deflation_risk use ERA comparison only (imp ignored).
    """
    s = signal.lower().strip()

    if s in ("velocity_drop", "insufficient_data"):
        return "n/a"

    if s == "era_inflation":
        if pd.isna(era_this) or pd.isna(era_last) or era_this == era_last:
            return "n/a"
        return "yes" if float(era_this) > float(era_last) else "no"

    if s == "era_deflation_risk":
        if pd.isna(era_this) or pd.isna(era_last) or era_this == era_last:
            return "n/a"
        return "yes" if float(era_this) < float(era_last) else "no"

    if imp < 0:  # no comparable data
        return "n/a"

    if s == "hot":
        if imp > 3: return "yes"
        if imp < 3: return "no"
        return "n/a"
    if s == "cold":
        if imp < 2: return "yes"
        if imp > 2: return "no"
        return "n/a"
    if s == "positive_regression":
        if imp > 2: return "yes"
        if imp < 2: return "no"
        return "n/a"
    if s == "negative_regression":
        if imp < 3: return "yes"
        if imp > 3: return "no"
        return "n/a"
    if s == "neutral":
        return "n/a"

    return "n/a"


def _action_flag_result(flag: str, imp: int) -> str:
    """
    Spec: Trend Analyzer Eval.md — Action_Flag scoring.
    bench_today excluded (requires single-day data not in weekly parquets).
    """
    f = str(flag).lower().strip()
    if f in ("", "nan", "none", "bench_today"):
        return "n/a"
    if imp < 0:
        return "n/a"
    if f == "drop_candidate":
        if imp < 2: return "yes"
        if imp > 2: return "no"
        return "n/a"
    if f in ("hold", "neutral"):
        if imp > 2: return "yes"
        if imp < 2: return "no"
        return "n/a"
    return "n/a"


def _score_ta_csv(ta_df: pd.DataFrame, this_idx: dict, last_idx: dict,
                   include_action_flag: bool, source: str) -> list[dict]:
    rows = []
    for _, row in ta_df.iterrows():
        pid       = str(row.get("player_id", ""))
        name      = str(row.get("name", pid))
        stat_type = str(row.get("stat_type", "batting")).lower()
        signal    = str(row.get("trend_signal", "insufficient_data"))
        flag      = str(row.get("action_flag", "")) if include_action_flag else ""

        this_s = _lookup_player(this_idx, pid, name)
        last_s = _lookup_player(last_idx, pid, name)
        imp    = _improvement_count(this_s, last_s, stat_type)

        era_this = float(this_s.get("era", float("nan"))) if stat_type == "pitching" else float("nan")
        era_last = float(last_s.get("era", float("nan"))) if stat_type == "pitching" else float("nan")

        sig_r = _trend_signal_result(signal, imp, era_this, era_last)
        flg_r = _action_flag_result(flag, imp) if include_action_flag else "n/a"

        rows.append({
            "player_id": pid, "name": name, "source": source, "stat_type": stat_type,
            "trend_signal": signal, "action_flag": flag if include_action_flag else "n/a",
            "improvement_count": imp if imp >= 0 else "n/a",
            "signal_result": sig_r, "flag_result": flg_r,
        })
    return rows


def score_trend_analyzer(sun_date: str) -> dict:
    """
    Spec: evals/Trend Analyzer Eval.md
    Sunday:    60% signal / 70% action flag
    Wednesday: 75% signal / 85% action flag
    Ground truth: 7d stats from current Sunday's parquets (this week).
    Prior week:   7d stats from second-to-latest parquets (last Sunday).
    """
    # Current Sunday = ground truth (this week's stats)
    this_roster = _build_stats_index(_latest("pybaseball_roster",          "parquet"), "7d")
    this_fa     = _build_stats_index(_latest("pybaseball_fa",              "parquet"), "7d")
    this_opp    = _build_stats_index(_latest("pybaseball_opponent_roster", "parquet"), "7d")

    # Last Sunday = prior week stats for improvement comparison
    try:
        last_roster = _build_stats_index(_second_latest("pybaseball_roster",          "parquet"), "7d")
        last_fa     = _build_stats_index(_second_latest("pybaseball_fa",              "parquet"), "7d")
        last_opp    = _build_stats_index(_second_latest("pybaseball_opponent_roster", "parquet"), "7d")
    except FileNotFoundError:
        last_roster, last_fa, last_opp = {}, {}, {}

    out = {"eval": "trend_analyzer"}

    sources = [
        ("roster",   "trend_analyzer_roster",   this_roster, last_roster, True),
        ("fa",       "trend_analyzer_fa",        this_fa,     last_fa,     True),
        ("opponent", "trend_analyzer_opponent",  this_opp,    last_opp,    False),
    ]

    for run_label, sig_thresh, flg_thresh, date_fn in [
        ("sunday",    0.60, 0.70, lambda: sun_date),
        ("wednesday", 0.75, 0.85, lambda: None),
    ]:
        all_sig, all_flg, detail = [], [], []

        for src_name, prefix, this_idx, last_idx, incl_flag in sources:
            try:
                if run_label == "sunday":
                    path = _for_date(prefix, sun_date, "csv")
                else:
                    path = _latest(prefix, "csv")
                ta_df = _read_csv(path)
            except FileNotFoundError:
                continue

            rows = _score_ta_csv(ta_df, this_idx, last_idx, incl_flag, src_name)
            detail.extend(rows)
            all_sig.extend([r["signal_result"] for r in rows])
            if incl_flag:
                all_flg.extend([r["flag_result"] for r in rows])

        sig_c, sig_w, sig_t, sig_s = _tally(all_sig)
        flg_c, flg_w, flg_t, flg_s = _tally(all_flg)

        out[run_label] = {
            "trend_signal": {
                "score": sig_s, "correct": sig_c, "wrong": sig_w,
                "total_scoreable": sig_t, "pass_threshold": sig_thresh,
                "pass_fail": "PASS" if sig_s >= sig_thresh else "FAIL",
            },
            "action_flag": {
                "score": flg_s, "correct": flg_c, "wrong": flg_w,
                "total_scoreable": flg_t, "pass_threshold": flg_thresh,
                "pass_fail": "PASS" if flg_s >= flg_thresh else "FAIL",
            },
            "detail": detail,
        }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Eval 3 — Roster Management
# Spec: evals/Roster Management Eval.md
# ─────────────────────────────────────────────────────────────────────────────

def _load_league_avgs() -> dict:
    """
    Load most-recent-week league_avg per category from league_benchmarks.parquet.
    Returns {CATEGORY_UPPER: float}.
    """
    df = _read_parquet(DATA_DIR / "league_benchmarks.parquet")
    if "week" in df.columns:
        df = df[df["week"] == df["week"].max()]
    avgs = {}
    for _, row in df.iterrows():
        cat = str(row.get("category", row.get("stat_name", ""))).upper().strip()
        val = row.get("league_avg")
        if cat and val is not None and not pd.isna(val):
            avgs[cat] = float(val)
    return avgs


def _within_tolerance(actual, expected, pct: float) -> str:
    if actual is None or expected is None or pd.isna(actual) or pd.isna(expected):
        return "n/a"
    if float(expected) == 0:
        return "n/a"
    lo = min((1 - pct) * float(expected), (1 + pct) * float(expected))
    hi = max((1 - pct) * float(expected), (1 + pct) * float(expected))
    return "yes" if lo <= float(actual) <= hi else "no"


def _score_rm_player(row: pd.Series, stat_type: str,
                      actual_7d: dict, league_avgs: dict) -> dict:
    """
    Score one player from a Roster Management output CSV.
    Returns per-category season/14d results and total_efficiency_14d result.
    """
    cats    = BATTER_CATS    if stat_type == "batting"  else PITCHER_CATS
    col_map = BATTER_COLS    if stat_type == "batting"  else PITCHER_COLS
    name    = str(row.get("name", row.get("player_id", "")))

    cat_season, cat_14d, delta_14d = {}, {}, {}

    for cat in cats:
        pyb_col  = col_map[cat].lower()
        delta_s  = row.get(f"{cat}_delta_season")
        delta_d  = row.get(f"{cat}_delta_14d")
        avg      = league_avgs.get(cat)
        actual   = actual_7d.get(pyb_col)

        # expected = avg + delta for counting stats; avg - delta for ERA/WHIP
        # (ERA_delta is already inverted: positive = better than average)
        if cat in LOWER_IS_BETTER:
            exp_s = (float(avg) - float(delta_s)) if (avg is not None and delta_s is not None and not pd.isna(delta_s)) else None
            exp_d = (float(avg) - float(delta_d)) if (avg is not None and delta_d is not None and not pd.isna(delta_d)) else None
        else:
            exp_s = (float(avg) + float(delta_s)) if (avg is not None and delta_s is not None and not pd.isna(delta_s)) else None
            exp_d = (float(avg) + float(delta_d)) if (avg is not None and delta_d is not None and not pd.isna(delta_d)) else None

        cat_season[cat] = _within_tolerance(actual, exp_s, 0.20)
        cat_14d[cat]    = _within_tolerance(actual, exp_d, 0.30)

        if delta_d is not None and not pd.isna(delta_d):
            delta_14d[cat] = float(delta_d)

    # total_efficiency_14d check
    total_eff_14d = row.get("total_efficiency_14d")
    eff_result    = "n/a"
    actual_eff    = None

    if (total_eff_14d is not None and not pd.isna(total_eff_14d)
            and delta_14d and sum(delta_14d.values()) != 0):
        delta_sum       = sum(delta_14d.values())
        scarcity_factor = float(total_eff_14d) / delta_sum

        actual_deltas = {}
        for cat in cats:
            pyb_col    = col_map[cat].lower()
            actual_val = actual_7d.get(pyb_col)
            avg        = league_avgs.get(cat)
            if actual_val is not None and avg is not None and not pd.isna(actual_val):
                if cat in LOWER_IS_BETTER:
                    actual_deltas[cat] = float(avg) - float(actual_val)
                else:
                    actual_deltas[cat] = float(actual_val) - float(avg)

        if actual_deltas:
            actual_eff = scarcity_factor * sum(actual_deltas.values())
            eff_result = _within_tolerance(actual_eff, float(total_eff_14d), 0.10)

    return {
        "name":                       name,
        "stat_type":                  stat_type,
        "season_category_results":    cat_season,
        "14d_category_results":       cat_14d,
        "total_efficiency_14d_pred":  round(float(total_eff_14d), 3) if total_eff_14d is not None else None,
        "total_efficiency_14d_actual": round(actual_eff, 3) if actual_eff is not None else None,
        "total_efficiency_14d_result": eff_result,
    }


def score_roster_management(sun_date: str) -> dict:
    """
    Spec: evals/Roster Management Eval.md
    Category thresholds (season and 14d): 80%
    total_efficiency_14d threshold: 90%
    """
    try:
        league_avgs = _load_league_avgs()
    except FileNotFoundError as e:
        return {"eval": "roster_management", "error": str(e)}

    # Latest pybaseball parquets = ground truth (this week's 7d stats)
    def _7d_idx(prefix: str) -> dict:
        return _build_stats_index(_latest(prefix, "parquet"), "7d")

    try:
        roster_7d = _7d_idx("pybaseball_roster")
        fa_7d     = _7d_idx("pybaseball_fa")
    except FileNotFoundError as e:
        return {"eval": "roster_management", "error": f"Ground truth parquet missing: {e}"}

    out = {"eval": "roster_management"}

    for src_label, batter_prefix, pitcher_prefix, idx_7d in [
        ("roster", "roster_management_batter_output",    "roster_management_pitcher_output",    roster_7d),
        ("fa",     "roster_management_batter_fa_output", "roster_management_pitcher_fa_output", fa_7d),
    ]:
        all_rows     = []
        eff_results  = []
        cat_s_all    = {c: [] for c in BATTER_CATS + PITCHER_CATS}
        cat_d_all    = {c: [] for c in BATTER_CATS + PITCHER_CATS}

        for stat_type, prefix, cats in [
            ("batting",  batter_prefix,  BATTER_CATS),
            ("pitching", pitcher_prefix, PITCHER_CATS),
        ]:
            try:
                df = _read_csv(_for_date(prefix, sun_date, "csv"))
            except FileNotFoundError:
                continue

            for _, row in df.iterrows():
                pid       = str(row.get("player_id", row.get("name", "")))
                name      = str(row.get("name", pid))
                actual_7d = _lookup_player(idx_7d, pid, name)
                scored    = _score_rm_player(row, stat_type, actual_7d, league_avgs)
                all_rows.append(scored)
                eff_results.append(scored["total_efficiency_14d_result"])
                for cat in cats:
                    cat_s_all[cat].append(scored["season_category_results"].get(cat, "n/a"))
                    cat_d_all[cat].append(scored["14d_category_results"].get(cat, "n/a"))

        # Per-category pass/fail
        cat_scores = {}
        for cat in (BATTER_CATS + PITCHER_CATS):
            s_c, _, s_t, s_s = _tally(cat_s_all[cat])
            d_c, _, d_t, d_s = _tally(cat_d_all[cat])
            if s_t + d_t > 0:
                cat_scores[cat] = {
                    "season": {"score": s_s, "correct": s_c, "total": s_t,
                               "pass_fail": "PASS" if s_s >= 0.80 else "FAIL"},
                    "14d":    {"score": d_s, "correct": d_c, "total": d_t,
                               "pass_fail": "PASS" if d_s >= 0.80 else "FAIL"},
                }

        # total_efficiency_14d aggregate
        eff_c, eff_w, eff_t, eff_s = _tally(eff_results)

        # Top 5 misses by absolute delta
        misses = [
            r for r in all_rows
            if r["total_efficiency_14d_result"] == "no"
            and r["total_efficiency_14d_actual"]  is not None
            and r["total_efficiency_14d_pred"]     is not None
        ]
        misses.sort(
            key=lambda r: abs((r["total_efficiency_14d_actual"] or 0)
                              - (r["total_efficiency_14d_pred"]  or 0)),
            reverse=True,
        )

        out[src_label] = {
            "category_scores": cat_scores,
            "total_efficiency_14d": {
                "score": eff_s, "correct": eff_c, "wrong": eff_w,
                "total_scoreable": eff_t, "pass_threshold": 0.90,
                "pass_fail": "PASS" if eff_s >= 0.90 else "FAIL",
            },
            "top_5_misses": [
                {
                    "name":         r["name"],
                    "stat_type":    r["stat_type"],
                    "predicted":    r["total_efficiency_14d_pred"],
                    "actual":       r["total_efficiency_14d_actual"],
                    "abs_delta":    round(abs(
                        (r["total_efficiency_14d_actual"] or 0)
                        - (r["total_efficiency_14d_pred"]  or 0)
                    ), 3),
                }
                for r in misses[:5]
            ],
            "detail": all_rows,
        }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Eval 4 — GM Agent
# Spec: evals/GM Eval.md
# ─────────────────────────────────────────────────────────────────────────────

def _gm_category_result(cat: str, targets: set, punted: set,
                         winner: str, margin: float,
                         target_loss_pct: float, punt_loss_pct: float) -> str:
    c = cat.upper()
    if c in targets:
        if winner in ("me", "tie"):
            return "yes"
        if 0 < margin < target_loss_pct:  # lost but within tight margin
            return "yes"
        return "no"
    if c in punted:
        if winner in ("me", "tie"):
            return "no"
        if margin > punt_loss_pct:
            return "yes"
        return "no"
    return "n/a"


def _recalc_eff_14d(player_name: str, rm_b: pd.DataFrame, rm_p: pd.DataFrame,
                     stats_7d: dict, league_avgs: dict) -> tuple[float | None, str]:
    """
    Find player in RM output, recalculate total_efficiency_14d from actual 7d stats.
    Returns (recalc_value, stat_type) or (None, "not_found").
    """
    for rm_df, st in ((rm_b, "batting"), (rm_p, "pitching")):
        if rm_df is None or rm_df.empty:
            continue
        match = rm_df[rm_df["name"].str.lower() == player_name.lower()]
        if match.empty:
            continue
        row       = match.iloc[0]
        pid       = str(row.get("player_id", row.get("name", "")))
        actual_7d = _lookup_player(stats_7d, pid, player_name)
        scored    = _score_rm_player(row, st, actual_7d, league_avgs)
        return scored.get("total_efficiency_14d_actual"), st
    return None, "not_found"


def _score_drop(player_name: str, rm_roster_b, rm_roster_p,
                 roster_7d, fa_7d, league_avgs) -> dict:
    """
    Spec: GM Eval.md — Drop Candidates.
    Look up historical total_efficiency_14d from RM output (this is what the skill
    used to make the recommendation). Recalculate using this week's actual 7d stats.
    Dropped players may be in FA pool — check both parquets.
    """
    # Find player in RM roster output for historical efficiency
    hist_eff = None
    for rm_df in (rm_roster_b, rm_roster_p):
        if rm_df is None or rm_df.empty:
            continue
        match = rm_df[rm_df["name"].str.lower() == player_name.lower()]
        if not match.empty:
            hist_eff = match.iloc[0].get("total_efficiency_14d")
            break

    if hist_eff is None or pd.isna(hist_eff):
        return {"player": player_name, "result": "n/a",
                "reason": "historical total_efficiency_14d not found in RM output"}

    # Dropped player may now be in FA pool or have been picked up — try both
    combined_7d = {**roster_7d, **fa_7d}
    recalc, _ = _recalc_eff_14d(player_name, rm_roster_b, rm_roster_p, combined_7d, league_avgs)

    if recalc is None:
        return {"player": player_name, "result": "n/a",
                "reason": "player not found in either parquet — may have been picked up mid-week"}

    hist = float(hist_eff)
    if hist >= recalc:
        result = "yes"   # player didn't improve → drop was correct
    elif hist < 0.8 * recalc:
        result = "no"    # player significantly improved → drop was wrong
    else:
        result = "n/a"   # borderline improvement

    return {
        "player": player_name,
        "historical_eff_14d": round(hist, 3),
        "recalc_eff_14d":     round(recalc, 3),
        "result": result,
    }


def _score_add(rec: dict, category_targets: set,
                rm_fa_b, rm_fa_p, roster_7d, fa_7d, league_avgs) -> dict:
    """
    Spec: GM Eval.md — Add Candidates.
    Two checks:
      1. target_category_execution: did the add have a positive delta in ≥1 target cat?
      2. counterfactual: were there ≤2 FAs in the filtered pool with higher recalc eff?
    """
    player_name = rec.get("player", "")
    # Added player is now on roster
    combined_7d = {**roster_7d, **fa_7d}

    recalc_rec, stat_type = _recalc_eff_14d(player_name, rm_fa_b, rm_fa_p, combined_7d, league_avgs)

    if recalc_rec is None or stat_type == "not_found":
        return {"player": player_name, "result": "n/a",
                "reason": "player not found in FA RM output"}

    actual_7d = _lookup_player(combined_7d, "", player_name)
    col_map   = BATTER_COLS if stat_type == "batting" else PITCHER_COLS
    cats      = BATTER_CATS if stat_type == "batting" else PITCHER_CATS

    # Check 1 — target category execution
    target_exec = "no"
    for cat in cats:
        if cat.upper() not in category_targets:
            continue
        actual_val = actual_7d.get(col_map[cat].lower())
        avg        = league_avgs.get(cat.upper())
        if actual_val is None or avg is None or pd.isna(actual_val):
            continue
        delta = (float(avg) - float(actual_val)) if cat in LOWER_IS_BETTER \
                else (float(actual_val) - float(avg))
        if delta > 0:
            target_exec = "yes"
            break

    # Check 2 — counterfactual: FAs in filtered pool with higher recalc eff
    fa_frames = [df for df in (rm_fa_b, rm_fa_p) if df is not None and not df.empty]
    rm_fa_all = pd.concat(fa_frames, ignore_index=True) if fa_frames else pd.DataFrame()

    fa_better = 0
    if recalc_rec is not None and not rm_fa_all.empty:
        # Determine FA stat_type by which delta columns are present
        for _, fa_row in rm_fa_all.iterrows():
            fa_name = str(fa_row.get("name", "")).lower()
            if fa_name == player_name.lower():
                continue
            # FA must have positive delta in ≥1 target category (at 14d window)
            fa_st   = "batting" if "R_delta_14d" in fa_row.index else "pitching"
            fa_cats = BATTER_CATS if fa_st == "batting" else PITCHER_CATS
            has_target = any(
                cat.upper() in category_targets
                and fa_row.get(f"{cat}_delta_14d") is not None
                and not pd.isna(fa_row.get(f"{cat}_delta_14d", float("nan")))
                and float(fa_row.get(f"{cat}_delta_14d", 0)) > 0
                for cat in fa_cats
            )
            if not has_target:
                continue
            fa_pid      = str(fa_row.get("player_id", fa_row.get("name", "")))
            fa_actual   = _lookup_player(combined_7d, fa_pid, fa_name)
            fa_scored   = _score_rm_player(fa_row, fa_st, fa_actual, league_avgs)
            fa_eff      = fa_scored.get("total_efficiency_14d_actual")
            if fa_eff is not None and fa_eff > recalc_rec:
                fa_better += 1

    counterfactual = "yes" if fa_better <= 2 else "no"
    overall        = "yes" if target_exec == "yes" and counterfactual == "yes" else "no"

    return {
        "player":                   player_name,
        "target_category_execution": target_exec,
        "fa_better_count":          fa_better,
        "counterfactual_result":    counterfactual,
        "rec_eff_14d_actual":       round(recalc_rec, 3) if recalc_rec is not None else None,
        "result":                   overall,
    }


def score_gm(sun_date: str, sun_decisions: dict, wed_decisions: dict) -> dict:
    """
    Spec: evals/GM Eval.md
    Sub-evals: Sunday categories (80%), Wednesday categories (90%),
               Drop candidates (90%), Add candidates (90%).
    """
    week = sun_decisions.get("week")
    try:
        scores = _load_matchup_scores(week)
    except Exception as e:
        return {"eval": "gm", "error": str(e)}

    try:
        league_avgs = _load_league_avgs()
        roster_7d   = _build_stats_index(_latest("pybaseball_roster", "parquet"), "7d")
        fa_7d       = _build_stats_index(_latest("pybaseball_fa",     "parquet"), "7d")
    except FileNotFoundError as e:
        return {"eval": "gm", "error": str(e)}

    def _load_rm(prefix):
        try:
            return _read_csv(_for_date(prefix, sun_date, "csv"))
        except FileNotFoundError:
            return None

    rm_roster_b = _load_rm("roster_management_batter_output")
    rm_roster_p = _load_rm("roster_management_pitcher_output")
    rm_fa_b     = _load_rm("roster_management_batter_fa_output")
    rm_fa_p     = _load_rm("roster_management_pitcher_fa_output")

    out = {"eval": "gm", "week": week}

    # ── Category evals ────────────────────────────────────────────────────────
    for run_label, decisions, target_loss_pct, punt_loss_pct, threshold in [
        ("sunday",    sun_decisions, 0.10, 0.20, 0.80),
        ("wednesday", wed_decisions, 0.05, 0.30, 0.90),
    ]:
        targets = {c.upper() for c in decisions.get("category_targets",  [])}
        punted  = {c.upper() for c in decisions.get("categories_punted", [])}
        rows = []
        for cat, sc in scores.items():
            my_v   = sc["my_value"]
            opp_v  = sc["opp_value"]
            lib    = sc["lower_is_better"]
            winner = _category_winner(my_v, opp_v, lib)
            margin = _loss_margin_pct(my_v, opp_v, lib)
            result = _gm_category_result(cat, targets, punted, winner, margin,
                                          target_loss_pct, punt_loss_pct)
            rows.append({
                "category":           cat,
                "classification":     "target" if cat in targets else ("punt" if cat in punted else "unclassified"),
                "my_score":           my_v,
                "opp_score":          opp_v,
                "winner":             winner,
                "loss_margin_pct":    round(margin * 100, 1),
                "result":             result,
            })
        c, w, t, s = _tally([r["result"] for r in rows])
        out[f"{run_label}_categories"] = {
            "score": s, "correct": c, "wrong": w, "total_scoreable": t,
            "pass_threshold": threshold,
            "pass_fail": "PASS" if s >= threshold else "FAIL",
            "detail": rows,
        }

    # ── Drop candidate eval ───────────────────────────────────────────────────
    # Drops are embedded as "drop" field in recommended_adds entries
    drops = []
    for item in sun_decisions.get("recommended_adds", []):
        d = item.get("drop", "")
        if d and d not in drops:
            drops.append(d)
    for item in wed_decisions.get("recommended_adds", []):
        d = item.get("drop", "")
        if d and d not in drops:
            drops.append(d)

    drop_rows = []
    for player_name in drops:
        drop_rows.append(_score_drop(
            player_name, rm_roster_b, rm_roster_p, roster_7d, fa_7d, league_avgs
        ))

    dc, dw, dt, ds = _tally([r["result"] for r in drop_rows])
    out["drop_candidates"] = {
        "score": ds, "correct": dc, "wrong": dw, "total_scoreable": dt,
        "pass_threshold": 0.90,
        "pass_fail": ("PASS" if ds >= 0.90 else "FAIL") if dt > 0 else "N/A — no drops this week",
        "detail": drop_rows,
    }

    # ── Add candidate eval ────────────────────────────────────────────────────
    sun_targets = {c.upper() for c in sun_decisions.get("category_targets", [])}
    wed_targets = {c.upper() for c in wed_decisions.get("category_targets", [])}
    all_targets = sun_targets | wed_targets

    add_rows = []
    for item in sun_decisions.get("recommended_adds", []) + wed_decisions.get("recommended_adds", []):
        rec = item if isinstance(item, dict) else {"player": str(item)}
        add_rows.append(_score_add(
            rec, all_targets, rm_fa_b, rm_fa_p, roster_7d, fa_7d, league_avgs
        ))

    ac, aw, at_, as_ = _tally([r["result"] for r in add_rows])
    out["add_candidates"] = {
        "score": as_, "correct": ac, "wrong": aw, "total_scoreable": at_,
        "pass_threshold": 0.90,
        "pass_fail": ("PASS" if as_ >= 0.90 else "FAIL") if at_ > 0 else "N/A — no adds this week",
        "detail": add_rows,
    }

    return out


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────

def write_report(results: dict) -> Path:
    EVALS_DIR.mkdir(exist_ok=True)
    path = EVALS_DIR / f"eval_report_{date.today().strftime('%Y%m%d')}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    return path


def _pf(sub: dict) -> str:
    return sub.get("pass_fail", "?")


def _score_str(sub: dict) -> str:
    s = sub.get("score")
    c = sub.get("correct", "?")
    t = sub.get("total_scoreable", "?")
    pct = f"{s:.0%}" if isinstance(s, float) else "N/A"
    return f"{c}/{t} = {pct}"


def print_summary(results: dict):
    print("\n" + "=" * 65)
    print(f"EVAL HARNESS SUMMARY — week {results.get('week_evaluated','?')}"
          f"  (run {results.get('date','?')})")
    print("=" * 65)

    # Matchup
    m = results.get("matchup", {})
    if "error" in m:
        print(f"\n[Matchup]  ERROR: {m['error']}")
    else:
        for run, thresh in (("sunday", "80%"), ("wednesday", "90%")):
            sub = m.get(run, {})
            if "error" in sub:
                print(f"\nMatchup {run.capitalize()}: ERROR — {sub['error']}")
            elif sub:
                print(f"\nMatchup {run.capitalize()}:   {_pf(sub):4}  "
                      f"{_score_str(sub)}, threshold {thresh}")

    # Trend Analyzer
    ta = results.get("trend_analyzer", {})
    for run, sig_t, flg_t in (("sunday", "60%", "70%"), ("wednesday", "75%", "85%")):
        sub = ta.get(run, {})
        sig = sub.get("trend_signal", {})
        flg = sub.get("action_flag", {})
        if sig:
            print(f"\nTrend {run.capitalize()} — Signals:   {_pf(sig):4}  "
                  f"{_score_str(sig)}, threshold {sig_t}")
        if flg:
            print(f"Trend {run.capitalize()} — Flags:     {_pf(flg):4}  "
                  f"{_score_str(flg)}, threshold {flg_t}")

    # Roster Management
    rm = results.get("roster_management", {})
    if "error" in rm:
        print(f"\n[Roster Management]  ERROR: {rm['error']}")
    else:
        for src in ("roster", "fa"):
            sub = rm.get(src, {})
            eff = sub.get("total_efficiency_14d", {})
            if eff:
                print(f"\nRM {src.capitalize()} — Eff 14d:   {_pf(eff):4}  "
                      f"{_score_str(eff)}, threshold 90%")
            for cat, cs in sub.get("category_scores", {}).items():
                for window in ("season", "14d"):
                    cw = cs.get(window, {})
                    if cw.get("pass_fail") == "FAIL":
                        print(f"  [FAIL] {src} {cat} {window}: {cw.get('score', 0):.0%}")
            misses = sub.get("top_5_misses", [])
            if misses:
                print(f"  Top RM {src} misses:")
                for mm in misses:
                    print(f"    {mm['name']}: pred={mm['predicted']}, "
                          f"actual={mm['actual']}, Δ={mm['abs_delta']}")

    # GM
    g = results.get("gm", {})
    if "error" in g:
        print(f"\n[GM]  ERROR: {g['error']}")
    else:
        for key, label, thresh in [
            ("sunday_categories",    "GM Sunday Cats",    "80%"),
            ("wednesday_categories", "GM Wednesday Cats", "90%"),
            ("drop_candidates",      "GM Drops",          "90%"),
            ("add_candidates",       "GM Adds",           "90%"),
        ]:
            sub = g.get(key, {})
            if sub:
                print(f"\n{label}:   {_pf(sub):4}  {_score_str(sub)}, threshold {thresh}")

    print("\n" + "=" * 65)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("Loading decisions JSON files...")
    try:
        sun_date, sun_decisions = _load_decisions_by_mode("sunday")
        print(f"  Sunday:    week {sun_decisions.get('week')}, as_of {sun_date}")
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        return

    try:
        _, wed_decisions = _load_decisions_by_mode("wednesday")
        print(f"  Wednesday: week {wed_decisions.get('week')}")
    except FileNotFoundError as e:
        print(f"WARNING: {e} — Wednesday evals will be skipped or empty")
        wed_decisions = {"mode": "midweek", "week": sun_decisions.get("week"),
                         "category_targets": [], "categories_punted": [],
                         "recommended_adds": []}

    week = sun_decisions.get("week")

    print("\nRunning Matchup eval...")
    matchup_results = score_matchup(sun_date, week)

    print("Running Trend Analyzer eval...")
    trend_results = score_trend_analyzer(sun_date)

    print("Running Roster Management eval...")
    rm_results = score_roster_management(sun_date)

    print("Running GM eval...")
    gm_results = score_gm(sun_date, sun_decisions, wed_decisions)

    results = {
        "date":            date.today().isoformat(),
        "week_evaluated":  week,
        "sunday_run_date": sun_date,
        "matchup":         matchup_results,
        "trend_analyzer":  trend_results,
        "roster_management": rm_results,
        "gm":              gm_results,
    }

    path = write_report(results)
    print(f"\nReport written → {path}")
    print_summary(results)


if __name__ == "__main__":
    main()
