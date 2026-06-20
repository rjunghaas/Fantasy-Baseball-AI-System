"""
data_client.py — shared data access layer for all agents.
Agents import functions from here instead of writing raw DuckDB queries.
All connections are in-memory and stateless.
"""
import duckdb
import pandas as pd
from pathlib import Path

DATA_DIR = Path("data")


def _latest(prefix: str) -> str:
    files = sorted(DATA_DIR.glob(f"{prefix}_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet data found for prefix: {prefix}")
    return str(files[-1])


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def get_my_roster() -> pd.DataFrame:
    """All players on my roster (all slots including BN and IL)."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("rosters")}')
    """).fetchdf()


def get_active_roster() -> pd.DataFrame:
    """My roster excluding IL slots."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("rosters")}')
        WHERE roster_slot != 'IL'
    """).fetchdf()


# ---------------------------------------------------------------------------
# Stats — pivoted so each player has one row with both season and recent stats
# ---------------------------------------------------------------------------

def get_batter_stats_pivoted() -> pd.DataFrame:
    """
    One row per batter with season and 14d stats side by side.
    Agents use this for trend detection (compare recent vs season pace).
    """
    f = _latest("stats")
    return _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.obp        AS obp_season,
            s.hr         AS hr_season,
            s.r          AS r_season,
            s.rbi        AS rbi_season,
            s.sb         AS sb_season,
            s.xwoba      AS xwoba_season,
            s.babip      AS babip_season,
            s.hard_hit_pct AS hard_hit_pct_season,
            s.barrel_pct AS barrel_pct_season,
            r.obp        AS obp_14d,
            r.hr         AS hr_14d,
            r.r          AS r_14d,
            r.rbi        AS rbi_14d,
            r.sb         AS sb_14d,
            r.xwoba      AS xwoba_14d,
            r.babip      AS babip_14d,
            r.hard_hit_pct AS hard_hit_pct_14d,
            r.barrel_pct AS barrel_pct_14d
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') r
          ON s.player_id = r.player_id
         AND r.window = '14d'
         AND r.stat_type = 'batting'
        WHERE s.window = 'season'
          AND s.stat_type = 'batting'
    """).fetchdf()


def get_pitcher_stats_pivoted() -> pd.DataFrame:
    """
    One row per pitcher with season and 14d stats side by side.
    """
    f = _latest("stats")
    return _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.era        AS era_season,
            s.whip       AS whip_season,
            s.strikeouts AS k_season,
            s.wins       AS wins_season,
            s.saves      AS saves_season,
            s.fip        AS fip_season,
            s.xfip       AS xfip_season,
            s.velocity   AS velocity_season,
            r.era        AS era_14d,
            r.whip       AS whip_14d,
            r.strikeouts AS k_14d,
            r.fip        AS fip_14d,
            r.velocity   AS velocity_14d
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') r
          ON s.player_id = r.player_id
         AND r.window = '14d'
         AND r.stat_type = 'pitching'
        WHERE s.window = 'season'
          AND s.stat_type = 'pitching'
    """).fetchdf()


# ---------------------------------------------------------------------------
# Trend signals — computed from pivoted stats
# These are the inputs the Trend Analyzer agent reasons over.
# ---------------------------------------------------------------------------

def get_batter_trend_signals() -> pd.DataFrame:
    """
    Batters on my roster with trend signals computed.
    Positive regression candidate: babip_14d < 0.250 AND xwoba_season > 0.320
    Negative regression candidate: babip_14d > 0.370 AND xwoba_season < 0.320
    Hot streak: obp_14d > obp_season + 0.040
    Cold streak: obp_14d < obp_season - 0.040
    """
    roster = get_my_roster()
    my_ids = roster["player_id"].tolist()
    id_list = ", ".join(f"'{p}'" for p in my_ids)

    f = _latest("stats")
    return _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.babip      AS babip_season,
            r.babip      AS babip_14d,
            s.xwoba      AS xwoba_season,
            r.xwoba      AS xwoba_14d,
            s.obp        AS obp_season,
            r.obp        AS obp_14d,
            s.hard_hit_pct AS hard_hit_pct_season,
            r.hard_hit_pct AS hard_hit_pct_14d,
            CASE
                WHEN r.babip < 0.250 AND s.xwoba > 0.320 THEN 'positive_regression'
                WHEN r.babip > 0.370 AND s.xwoba < 0.320 THEN 'negative_regression'
                WHEN r.obp > s.obp + 0.040              THEN 'hot'
                WHEN r.obp < s.obp - 0.040              THEN 'cold'
                ELSE 'neutral'
            END AS trend_signal
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') r
          ON s.player_id = r.player_id
         AND r.window = '14d'
         AND r.stat_type = 'batting'
        WHERE s.window = 'season'
          AND s.stat_type = 'batting'
          AND s.player_id IN ({id_list})
        ORDER BY trend_signal, s.xwoba DESC
    """).fetchdf()


def get_pitcher_trend_signals() -> pd.DataFrame:
    """
    Pitchers on my roster with trend signals computed.
    Velocity drop: velocity_14d < velocity_season - 1.5 mph  (injury risk)
    ERA inflation: era_14d > era_season + 1.50 AND fip_season < era_season (luck-driven, should correct)
    ERA deflation: era_14d < era_season - 1.50 AND fip_season > era_season (regression risk)
    """
    roster = get_my_roster()
    my_ids = roster["player_id"].tolist()
    id_list = ", ".join(f"'{p}'" for p in my_ids)

    f = _latest("stats")
    return _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.era        AS era_season,
            r.era        AS era_14d,
            s.fip        AS fip_season,
            r.fip        AS fip_14d,
            s.whip       AS whip_season,
            r.whip       AS whip_14d,
            s.velocity   AS velocity_season,
            r.velocity   AS velocity_14d,
            CASE
                WHEN r.velocity < s.velocity - 1.5                          THEN 'velocity_drop'
                WHEN r.era > s.era + 1.50 AND s.fip < s.era                 THEN 'era_inflation'
                WHEN r.era < s.era - 1.50 AND s.fip > s.era                 THEN 'era_deflation_risk'
                ELSE 'neutral'
            END AS trend_signal
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') r
          ON s.player_id = r.player_id
         AND r.window = '14d'
         AND r.stat_type = 'pitching'
        WHERE s.window = 'season'
          AND s.stat_type = 'pitching'
          AND s.player_id IN ({id_list})
        ORDER BY trend_signal, s.era
    """).fetchdf()


# ---------------------------------------------------------------------------
# Matchup — opponent roster, history, tendencies, and category gaps
# ---------------------------------------------------------------------------

def get_matchup_state() -> pd.DataFrame:
    """Current week's category scores, transactions used/remaining, and opponent name."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("matchup_state")}')
    """).fetchdf()


def get_opponent_roster() -> pd.DataFrame:
    """Opponent's current roster."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("opponent_roster")}')
    """).fetchdf()


def get_opponent_history() -> pd.DataFrame:
    """Last N weeks of opponent category scores with results."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("opponent_history")}')
        ORDER BY week
    """).fetchdf()


def get_opponent_tendencies() -> pd.DataFrame:
    """
    Per-category summary of opponent's historical performance.
    Returns mean, min, max for each category and a priority flag:
      high   = opponent mean is in top tier (historically strong in this category)
      medium = inconsistent — sometimes wins, sometimes doesn't
      low    = opponent historically weak in this category
    Priority thresholds are relative to the opponent's own distribution,
    not league average (we don't have league data in bootstrap).
    """
    f = _latest("opponent_history")
    return _con().execute(f"""
        WITH stats AS (
            SELECT
                AVG(r)    AS r_mean,    MIN(r)    AS r_min,    MAX(r)    AS r_max,
                AVG(hr)   AS hr_mean,   MIN(hr)   AS hr_min,   MAX(hr)   AS hr_max,
                AVG(rbi)  AS rbi_mean,  MIN(rbi)  AS rbi_min,  MAX(rbi)  AS rbi_max,
                AVG(sb)   AS sb_mean,   MIN(sb)   AS sb_min,   MAX(sb)   AS sb_max,
                AVG(obp)  AS obp_mean,  MIN(obp)  AS obp_min,  MAX(obp)  AS obp_max,
                AVG(w)    AS w_mean,    MIN(w)    AS w_min,    MAX(w)    AS w_max,
                AVG(sv)   AS sv_mean,   MIN(sv)   AS sv_min,   MAX(sv)   AS sv_max,
                AVG(k)    AS k_mean,    MIN(k)    AS k_min,    MAX(k)    AS k_max,
                AVG(era)  AS era_mean,  MIN(era)  AS era_min,  MAX(era)  AS era_max,
                AVG(whip) AS whip_mean, MIN(whip) AS whip_min, MAX(whip) AS whip_max,
                STDDEV(r)    AS r_std,
                STDDEV(hr)   AS hr_std,
                STDDEV(rbi)  AS rbi_std,
                STDDEV(sb)   AS sb_std,
                STDDEV(obp)  AS obp_std,
                STDDEV(w)    AS w_std,
                STDDEV(sv)   AS sv_std,
                STDDEV(k)    AS k_std,
                STDDEV(era)  AS era_std,
                STDDEV(whip) AS whip_std
            FROM read_parquet('{f}')
        )
        SELECT
            -- Runs
            ROUND(r_mean, 1) AS r_mean, r_min, r_max,
            CASE WHEN r_std < 6  THEN 'high' WHEN r_std < 12 THEN 'medium' ELSE 'low' END AS r_consistency,
            -- HR
            ROUND(hr_mean, 1) AS hr_mean, hr_min, hr_max,
            CASE WHEN hr_std < 3 THEN 'high' WHEN hr_std < 6  THEN 'medium' ELSE 'low' END AS hr_consistency,
            -- RBI
            ROUND(rbi_mean, 1) AS rbi_mean, rbi_min, rbi_max,
            CASE WHEN rbi_std < 5 THEN 'high' WHEN rbi_std < 10 THEN 'medium' ELSE 'low' END AS rbi_consistency,
            -- SB
            ROUND(sb_mean, 1) AS sb_mean, sb_min, sb_max,
            CASE WHEN sb_std < 2 THEN 'high' WHEN sb_std < 4  THEN 'medium' ELSE 'low' END AS sb_consistency,
            -- OBP
            ROUND(obp_mean, 3) AS obp_mean, obp_min, obp_max,
            CASE WHEN obp_std < 0.010 THEN 'high' WHEN obp_std < 0.020 THEN 'medium' ELSE 'low' END AS obp_consistency,
            -- Wins
            ROUND(w_mean, 1) AS w_mean, w_min, w_max,
            CASE WHEN w_std < 1 THEN 'high' WHEN w_std < 2  THEN 'medium' ELSE 'low' END AS w_consistency,
            -- Saves
            ROUND(sv_mean, 1) AS sv_mean, sv_min, sv_max,
            CASE WHEN sv_std < 1 THEN 'high' WHEN sv_std < 2  THEN 'medium' ELSE 'low' END AS sv_consistency,
            -- Strikeouts
            ROUND(k_mean, 1) AS k_mean, k_min, k_max,
            CASE WHEN k_std < 3 THEN 'high' WHEN k_std < 6  THEN 'medium' ELSE 'low' END AS k_consistency,
            -- ERA (lower is better — invert logic)
            ROUND(era_mean, 2) AS era_mean, era_min, era_max,
            CASE WHEN era_std < 0.5 THEN 'high' WHEN era_std < 1.0 THEN 'medium' ELSE 'low' END AS era_consistency,
            -- WHIP (lower is better)
            ROUND(whip_mean, 2) AS whip_mean, whip_min, whip_max,
            CASE WHEN whip_std < 0.1 THEN 'high' WHEN whip_std < 0.2 THEN 'medium' ELSE 'low' END AS whip_consistency
        FROM stats
    """).fetchdf()


def get_category_gaps() -> pd.DataFrame:
    """
    Joins current matchup state with opponent historical tendencies.
    Returns one row per category with:
      my_score, opp_score, gap (mine - opp, positive = I am winning),
      opp_typical_range (mean ± context), vulnerability flag.

    vulnerability = True when opponent's current score is below their historical min
    for that category (they are performing unusually poorly — exploitable).

    For ERA/WHIP, lower is better so gap logic is inverted.
    """
    state = get_matchup_state().iloc[0]
    tend = get_opponent_tendencies().iloc[0]

    categories = [
        # (name, my_val, opp_val, opp_mean, opp_min, opp_max, lower_is_better)
        ("R",    state["r_mine"],    state["r_opp"],    tend["r_mean"],    tend["r_min"],    tend["r_max"],    False),
        ("HR",   state["hr_mine"],   state["hr_opp"],   tend["hr_mean"],   tend["hr_min"],   tend["hr_max"],   False),
        ("RBI",  state["rbi_mine"],  state["rbi_opp"],  tend["rbi_mean"],  tend["rbi_min"],  tend["rbi_max"],  False),
        ("SB",   state["sb_mine"],   state["sb_opp"],   tend["sb_mean"],   tend["sb_min"],   tend["sb_max"],   False),
        ("OBP",  state["obp_mine"],  state["obp_opp"],  tend["obp_mean"],  tend["obp_min"],  tend["obp_max"],  False),
        ("W",    state["w_mine"],    state["w_opp"],    tend["w_mean"],    tend["w_min"],    tend["w_max"],    False),
        ("SV",   state["sv_mine"],   state["sv_opp"],   tend["sv_mean"],   tend["sv_min"],   tend["sv_max"],   False),
        ("K",    state["k_mine"],    state["k_opp"],    tend["k_mean"],    tend["k_min"],    tend["k_max"],    False),
        ("ERA",  state["era_mine"],  state["era_opp"],  tend["era_mean"],  tend["era_min"],  tend["era_max"],  True),
        ("WHIP", state["whip_mine"], state["whip_opp"], tend["whip_mean"], tend["whip_min"], tend["whip_max"], True),
    ]

    rows = []
    for cat, my_val, opp_val, opp_mean, opp_min, opp_max, lower_better in categories:
        if lower_better:
            gap = opp_val - my_val          # positive = I am winning (my ERA lower)
            vulnerability = opp_val > opp_max  # opp ERA worse than their historical worst
        else:
            gap = my_val - opp_val          # positive = I am winning
            vulnerability = opp_val < opp_min  # opp scoring below their historical floor

        rows.append({
            "category":         cat,
            "my_score":         my_val,
            "opp_score":        opp_val,
            "gap":              round(float(gap), 3),
            "winning":          gap > 0,
            "opp_mean":         opp_mean,
            "opp_min":          opp_min,
            "opp_max":          opp_max,
            "opp_typical_range": f"{opp_min}–{opp_max} (avg {opp_mean})",
            "vulnerable":       bool(vulnerability),
        })

    return pd.DataFrame(rows)
