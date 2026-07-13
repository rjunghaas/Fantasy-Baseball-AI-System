"""
data_client.py — shared data access layer for all agents.
Agents import functions from here instead of writing raw DuckDB queries.
All connections are in-memory and stateless.
"""
import duckdb
import pandas as pd
from pathlib import Path
from typing import Optional

DATA_DIR = Path("data")

# Players the GM should never drop regardless of contribution score.
# Edit this list to match your keepers or trade-locked players.
NO_DROP_PLAYERS: list[str] = [
    "junior-caminero-001",
    "randy-arozarena-001",
    "cj-abrams-001",
    "yoshinobu-yamamoto-001",
    "tarik-skubal-001",
]

# Positional scarcity multipliers for roster slot efficiency scoring.
# Refined when slot-level position data is available from Yahoo.
POSITION_SCARCITY = {
    "C":  1.30,
    "SS": 1.20,
    "2B": 1.10,
    "3B": 1.10,
    "P":  1.10,
    "1B": 0.95,
    "CI": 0.95,
    "OF": 0.90,
    "MI": 1.05,
    "UT": 0.90,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _latest(prefix: str) -> str:
    """Most recent parquet matching prefix_YYYYMMDD.parquet."""
    files = sorted(DATA_DIR.glob(f"{prefix}_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No parquet found for prefix: {prefix}")
    return str(files[-1])


def _fixed(filename: str) -> str:
    """Fixed-name parquet (no date stamp)."""
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    return str(path)


def _con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def _source_file(source: str) -> str:
    """Resolve source name to parquet path. source: roster | opponent | fa"""
    mapping = {
        "roster":   lambda: _latest("pybaseball_roster"),
        "opponent": lambda: _latest("pybaseball_opponent_roster"),
        "fa":       lambda: _latest("pybaseball_fa"),
    }
    if source not in mapping:
        raise ValueError(f"Unknown source '{source}'. Expected: roster | opponent | fa")
    return mapping[source]()


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def get_my_roster() -> pd.DataFrame:
    """
    Unique players on my roster with stat_type, eligible_positions, and no_drop flag.
    Position comes from roster_positions_YYYYMMDD.csv saved by the Rust binary (full mode).
    Falls back to stat_type when the CSV is not present.
    """
    f = _latest("pybaseball_roster")
    df = _con().execute(f"""
        SELECT DISTINCT player_id, name, stat_type
        FROM read_parquet('{f}')
        WHERE "window" = 'season'
        ORDER BY stat_type, name
    """).fetchdf()

    roster_csvs = sorted(DATA_DIR.glob("roster_positions_*.csv"))
    if roster_csvs:
        pos_df = pd.read_csv(roster_csvs[-1])
        # Columns from Rust: player_key, name, eligible_positions (pipe-separated)
        if "player_key" in pos_df.columns and "eligible_positions" in pos_df.columns:
            pos_df = pos_df.rename(columns={"player_key": "player_id"})[
                ["player_id", "eligible_positions"]
            ].drop_duplicates("player_id")
            df = df.merge(pos_df, on="player_id", how="left")
        else:
            df["eligible_positions"] = df["stat_type"]
    else:
        df["eligible_positions"] = df["stat_type"]

    df["no_drop"] = df["player_id"].isin(NO_DROP_PLAYERS)
    return df


def get_player_positions() -> "dict[str, list[str]]":
    """
    Returns {player_id: [eligible_positions]} for all players on my roster.
    Source: latest roster_positions_YYYYMMDD.csv (written by Rust full mode).
    Eligible positions are pipe-separated in the CSV (e.g. "1B|3B|CI").
    """
    roster_csvs = sorted(DATA_DIR.glob("roster_positions_*.csv"))
    if not roster_csvs:
        return {}
    pos_df = pd.read_csv(roster_csvs[-1])
    result: "dict[str, list[str]]" = {}
    for _, row in pos_df.iterrows():
        pid = str(row["player_key"])
        positions = [p.strip() for p in str(row["eligible_positions"]).split("|") if p.strip()]
        result[pid] = positions
    return result


def get_fa_positions() -> "dict[str, list[str]]":
    """
    Returns {player_id: [eligible_positions]} for free agents.
    Source: latest fa_positions_YYYYMMDD.csv (written by Rust adhoc mode).
    Eligible positions are pipe-separated in the CSV (e.g. "OF|UT").
    """
    fa_csvs = sorted(DATA_DIR.glob("fa_positions_*.csv"))
    if not fa_csvs:
        return {}
    pos_df = pd.read_csv(fa_csvs[-1])
    result: "dict[str, list[str]]" = {}
    for _, row in pos_df.iterrows():
        pid = str(row["player_key"])
        positions = [p.strip() for p in str(row["eligible_positions"]).split("|") if p.strip()]
        result[pid] = positions
    return result


def get_opponent_roster_list() -> pd.DataFrame:
    """Unique players on the current opponent's roster with stat_type."""
    f = _latest("pybaseball_opponent_roster")
    return _con().execute(f"""
        SELECT DISTINCT player_id, name, stat_type
        FROM read_parquet('{f}')
        WHERE "window" = 'season'
        ORDER BY stat_type, name
    """).fetchdf()


# ---------------------------------------------------------------------------
# Stats — multi-window pivot (season, 30d, 14d, 7d side by side)
# Used by Trend Analyzer and Roster Management.
# ---------------------------------------------------------------------------

def get_batter_stats_pivoted(source: str = "roster") -> pd.DataFrame:
    """
    One row per batter with season, 30d, 14d, and 7d stats side by side.
    Splits (vs LHP/RHP) are season-window only — patched into the season row by the shim.
    source: 'roster' | 'opponent' | 'fa'
    """
    f = _source_file(source)
    return _con().execute(f"""
        SELECT
            s.player_id, s.name,
            -- Season
            s.r             AS r_season,
            s.hr            AS hr_season,
            s.rbi           AS rbi_season,
            s.sb            AS sb_season,
            s.obp           AS obp_season,
            s.babip         AS babip_season,
            s.xwoba         AS xwoba_season,
            s.xba           AS xba_season,
            s.hard_hit_pct  AS hard_hit_pct_season,
            s.barrel_pct    AS barrel_pct_season,
            s.chase_rate    AS chase_rate_season,
            s.sprint_speed  AS sprint_speed,
            -- Splits (season only)
            s.obp_vs_lhp, s.obp_vs_rhp,
            s.woba_vs_lhp,  s.woba_vs_rhp,
            s.slg_vs_lhp,   s.slg_vs_rhp,
            -- 30d
            d30.r           AS r_30d,
            d30.hr          AS hr_30d,
            d30.obp         AS obp_30d,
            d30.babip       AS babip_30d,
            d30.xwoba       AS xwoba_30d,
            -- 14d
            d14.r           AS r_14d,
            d14.hr          AS hr_14d,
            d14.obp         AS obp_14d,
            d14.babip       AS babip_14d,
            d14.xwoba       AS xwoba_14d,
            d14.hard_hit_pct AS hard_hit_pct_14d,
            -- 7d
            d7.r            AS r_7d,
            d7.hr           AS hr_7d,
            d7.obp          AS obp_7d,
            d7.babip        AS babip_7d,
            d7.xwoba        AS xwoba_7d
        FROM read_parquet('{f}') s
        LEFT JOIN read_parquet('{f}') d30
          ON s.player_id = d30.player_id AND d30."window" = '30d'  AND d30.stat_type = 'batting'
        LEFT JOIN read_parquet('{f}') d14
          ON s.player_id = d14.player_id AND d14."window" = '14d'  AND d14.stat_type = 'batting'
        LEFT JOIN read_parquet('{f}') d7
          ON s.player_id = d7.player_id  AND d7."window"  = '7d'   AND d7.stat_type  = 'batting'
        WHERE s."window" = 'season'
          AND s.stat_type = 'batting'
        ORDER BY s.name
    """).fetchdf()


def get_pitcher_stats_pivoted(source: str = "roster") -> pd.DataFrame:
    """
    One row per pitcher with season, 30d, 14d, and 7d stats side by side.
    Column names match pybaseball_shim.py exactly: k (strikeouts), w (wins), sv (saves).
    source: 'roster' | 'opponent' | 'fa'
    """
    f = _source_file(source)
    return _con().execute(f"""
        SELECT
            s.player_id, s.name,
            -- Season
            s.era           AS era_season,
            s.whip          AS whip_season,
            s.k             AS k_season,
            s.w             AS w_season,
            s.sv            AS sv_season,
            s.ip            AS ip_season,
            s.fip           AS fip_season,
            s.velocity      AS velocity_season,
            s.whiff_pct     AS whiff_pct_season,
            s.zone_pct      AS zone_pct_season,
            s.k_pct         AS k_pct_season,
            s.bb_pct        AS bb_pct_season,
            s.lob_pct       AS lob_pct_season,
            -- 30d
            d30.era         AS era_30d,
            d30.whip        AS whip_30d,
            d30.k           AS k_30d,
            d30.fip         AS fip_30d,
            d30.velocity    AS velocity_30d,
            -- 14d
            d14.era         AS era_14d,
            d14.whip        AS whip_14d,
            d14.k           AS k_14d,
            d14.fip         AS fip_14d,
            d14.velocity    AS velocity_14d,
            -- 7d
            d7.era          AS era_7d,
            d7.whip         AS whip_7d,
            d7.k            AS k_7d,
            d7.velocity     AS velocity_7d
        FROM read_parquet('{f}') s
        LEFT JOIN read_parquet('{f}') d30
          ON s.player_id = d30.player_id AND d30."window" = '30d'  AND d30.stat_type = 'pitching'
        LEFT JOIN read_parquet('{f}') d14
          ON s.player_id = d14.player_id AND d14."window" = '14d'  AND d14.stat_type = 'pitching'
        LEFT JOIN read_parquet('{f}') d7
          ON s.player_id = d7.player_id  AND d7."window"  = '7d'   AND d7.stat_type  = 'pitching'
        WHERE s."window" = 'season'
          AND s.stat_type = 'pitching'
        ORDER BY s.name
    """).fetchdf()


# ---------------------------------------------------------------------------
# Trend Analyzer
# ---------------------------------------------------------------------------

def get_batter_trend_signals(source: str = "roster") -> pd.DataFrame:
    """
    Batters with trend signals from season vs 14d comparison.
    source: 'roster' | 'opponent' | 'fa'
    For 'fa': only hot and positive_regression returned (actionable adds).
    For 'roster': no_drop flag added from NO_DROP_PLAYERS.
    """
    f = _source_file(source)
    df = _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.babip         AS babip_season,
            d.babip         AS babip_14d,
            s.xwoba         AS xwoba_season,
            d.xwoba         AS xwoba_14d,
            s.obp           AS obp_season,
            d.obp           AS obp_14d,
            s.hard_hit_pct  AS hard_hit_pct_season,
            d.hard_hit_pct  AS hard_hit_pct_14d,
            CASE
                WHEN d.babip < 0.250 AND s.xwoba > 0.320 THEN 'positive_regression'
                WHEN d.babip > 0.370 AND s.xwoba < 0.320 THEN 'negative_regression'
                WHEN d.obp > s.obp + 0.040               THEN 'hot'
                WHEN d.obp < s.obp - 0.040               THEN 'cold'
                ELSE 'neutral'
            END AS trend_signal
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') d
          ON s.player_id = d.player_id
         AND d."window" = '14d'
         AND d.stat_type = 'batting'
        WHERE s."window" = 'season'
          AND s.stat_type = 'batting'
        ORDER BY trend_signal, s.xwoba DESC
    """).fetchdf()

    if source == "fa":
        df = df[df["trend_signal"].isin(["hot", "positive_regression"])]

    if source == "roster":
        roster = get_my_roster()
        df = df.merge(roster[["player_id", "no_drop"]], on="player_id", how="left")
        df["no_drop"] = df["no_drop"].fillna(False)

    return df.reset_index(drop=True)


def get_pitcher_trend_signals(source: str = "roster") -> pd.DataFrame:
    """
    Pitchers with trend signals from season vs 14d comparison.
    source: 'roster' | 'opponent' | 'fa'
    For 'fa': only era_inflation returned (buy-low candidates).
    For 'roster': no_drop flag added.
    """
    f = _source_file(source)
    df = _con().execute(f"""
        SELECT
            s.player_id,
            s.name,
            s.era           AS era_season,
            d.era           AS era_14d,
            s.fip           AS fip_season,
            d.fip           AS fip_14d,
            s.whip          AS whip_season,
            d.whip          AS whip_14d,
            s.velocity      AS velocity_season,
            d.velocity      AS velocity_14d,
            s.k_pct         AS k_pct_season,
            d.k_pct         AS k_pct_14d,
            CASE
                WHEN d.velocity < s.velocity - 1.5                          THEN 'velocity_drop'
                -- era_inflation: ERA spiked and 14d ERA is higher than 14d FIP (spike not supported by peripherals)
                WHEN d.era > s.era + 1.50 AND (d.era - d.fip) > 0.5        THEN 'era_inflation'
                -- era_deflation_risk: ERA dropped sharply but 14d FIP is still much higher (ERA won't hold)
                WHEN d.era < s.era - 1.50 AND (d.fip - d.era) > 0.5        THEN 'era_deflation_risk'
                ELSE 'neutral'
            END AS trend_signal
        FROM read_parquet('{f}') s
        JOIN read_parquet('{f}') d
          ON s.player_id = d.player_id
         AND d."window" = '14d'
         AND d.stat_type = 'pitching'
        WHERE s."window" = 'season'
          AND s.stat_type = 'pitching'
        ORDER BY trend_signal, s.era
    """).fetchdf()

    if source == "fa":
        # velocity_drop is a red flag (injury risk), not an add signal
        df = df[df["trend_signal"] == "era_inflation"]

    if source == "roster":
        roster = get_my_roster()
        df = df.merge(roster[["player_id", "no_drop"]], on="player_id", how="left")
        df["no_drop"] = df["no_drop"].fillna(False)

    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Roster Management — 14d contribution scores
# Same structure as season versions but pulling from the 14d window.
# Captures current-week production pace rather than season-long baseline.
# ---------------------------------------------------------------------------

def get_batter_contribution_scores_14d(source: str = "roster") -> pd.DataFrame:
    """
    Per-batter per-category contribution relative to league average, using 14d stats.
    Categories: R, HR, RBI, SB, OBP.
    Counting stats (R, HR, RBI, SB) are normalized to a weekly rate (÷ 2) so they are
    comparable in scale to league_avg (which is a team weekly average).
    OBP is a rate stat and is compared directly — no normalization needed.
    Players with no 14d data (recently called up, injured) will be absent from this result.
    source: 'roster' | 'fa'
    """
    f  = _source_file(source)
    lb = _fixed("league_benchmarks.parquet")

    return _con().execute(f"""
        WITH player_14d AS (
            -- Normalize counting stats to weekly rate: 14d cumulative ÷ 2 ≈ per-week pace
            SELECT player_id, name,
                r   / 2.0 AS r,
                hr  / 2.0 AS hr,
                rbi / 2.0 AS rbi,
                sb  / 2.0 AS sb,
                obp        AS obp   -- rate stat: no normalization
            FROM read_parquet('{f}')
            WHERE "window" = '14d' AND stat_type = 'batting'
        ),
        league AS (
            SELECT stat_name, AVG(league_avg) AS league_avg
            FROM read_parquet('{lb}')
            WHERE stat_name IN ('R', 'HR', 'RBI', 'SB', 'OBP')
            GROUP BY stat_name
        ),
        unpivoted AS (
            SELECT player_id, name, 'R'   AS cat, CAST(r   AS DOUBLE) AS val FROM player_14d UNION ALL
            SELECT player_id, name, 'HR'  AS cat, CAST(hr  AS DOUBLE) AS val FROM player_14d UNION ALL
            SELECT player_id, name, 'RBI' AS cat, CAST(rbi AS DOUBLE) AS val FROM player_14d UNION ALL
            SELECT player_id, name, 'SB'  AS cat, CAST(sb  AS DOUBLE) AS val FROM player_14d UNION ALL
            SELECT player_id, name, 'OBP' AS cat, CAST(obp AS DOUBLE) AS val FROM player_14d
        )
        SELECT
            u.player_id,
            u.name,
            u.cat                             AS category,
            ROUND(u.val, 3)                   AS value_14d,
            ROUND(l.league_avg, 3)            AS league_avg_team,
            ROUND(u.val - l.league_avg, 3)    AS contribution_delta,
            false                             AS lower_is_better
        FROM unpivoted u
        LEFT JOIN league l ON u.cat = l.stat_name
        ORDER BY u.name, u.cat
    """).fetchdf()


def get_pitcher_contribution_scores_14d(source: str = "roster") -> pd.DataFrame:
    """
    Per-pitcher per-category contribution relative to league average, using 14d stats.
    Categories: K, W, SV, ERA, WHIP.
    Counting stats (K, W, SV) are normalized to a weekly rate (÷ 2) so they are
    comparable in scale to league_avg (which is a team weekly average).
    ERA and WHIP are rate stats and are compared directly — no normalization needed.
    Players with insufficient 14d IP (recently injured, SP skipped starts) may be absent.
    source: 'roster' | 'fa'
    """
    f  = _source_file(source)
    lb = _fixed("league_benchmarks.parquet")

    return _con().execute(f"""
        WITH player_14d AS (
            -- Normalize counting stats to weekly rate: 14d cumulative ÷ 2 ≈ per-week pace
            SELECT player_id, name,
                k   / 2.0 AS k,
                w   / 2.0 AS w,
                sv  / 2.0 AS sv,
                era        AS era,   -- rate stat: no normalization
                whip       AS whip   -- rate stat: no normalization
            FROM read_parquet('{f}')
            WHERE "window" = '14d' AND stat_type = 'pitching'
        ),
        league AS (
            SELECT stat_name, AVG(league_avg) AS league_avg
            FROM read_parquet('{lb}')
            WHERE stat_name IN ('K', 'W', 'SV', 'ERA', 'WHIP')
            GROUP BY stat_name
        ),
        unpivoted AS (
            SELECT player_id, name, 'K'    AS cat, CAST(k    AS DOUBLE) AS val, false AS lib FROM player_14d UNION ALL
            SELECT player_id, name, 'W'    AS cat, CAST(w    AS DOUBLE) AS val, false AS lib FROM player_14d UNION ALL
            SELECT player_id, name, 'SV'   AS cat, CAST(sv   AS DOUBLE) AS val, false AS lib FROM player_14d UNION ALL
            SELECT player_id, name, 'ERA'  AS cat, CAST(era  AS DOUBLE) AS val, true  AS lib FROM player_14d UNION ALL
            SELECT player_id, name, 'WHIP' AS cat, CAST(whip AS DOUBLE) AS val, true  AS lib FROM player_14d
        )
        SELECT
            u.player_id,
            u.name,
            u.cat                                          AS category,
            ROUND(u.val, 3)                                AS value_14d,
            ROUND(l.league_avg, 3)                         AS league_avg_team,
            ROUND(
                CASE WHEN u.lib THEN l.league_avg - u.val
                     ELSE u.val - l.league_avg END,
                3
            )                                              AS contribution_delta,
            u.lib                                          AS lower_is_better
        FROM unpivoted u
        LEFT JOIN league l ON u.cat = l.stat_name
        ORDER BY u.name, u.cat
    """).fetchdf()


# ---------------------------------------------------------------------------
# Matchup Agent
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Mid-week matchup state helpers
# ---------------------------------------------------------------------------

def _latest_csv(prefix: str) -> str:
    """Most recent CSV matching prefix_YYYYMMDD.csv."""
    files = sorted(DATA_DIR.glob(f"{prefix}_*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV found for prefix: {prefix}")
    return str(files[-1])


def get_midweek_matchup_state(week: int) -> "dict[str, dict[str, float]]":
    """
    Read midweek_matchup_state.csv and return counting stats for the given week.
    Returns { "R": {"mine": 32.0, "opp": 28.0}, "HR": {...}, ... }
    Returns empty dict if the file doesn't exist or the week has no row.
    """
    csv_path = DATA_DIR / "midweek_matchup_state.csv"
    if not csv_path.exists():
        return {}

    df = pd.read_csv(csv_path)
    row = df[df["week"] == week]
    if row.empty:
        return {}

    r = row.iloc[0]
    stats: "dict[str, dict[str, float]]" = {}
    for cat in ["R", "HR", "RBI", "SB", "W", "SV", "K"]:
        col_mine = f"{cat.lower()}_mine"
        col_opp  = f"{cat.lower()}_opp"
        if col_mine in r.index and col_opp in r.index:
            stats[cat] = {"mine": float(r[col_mine]), "opp": float(r[col_opp])}
    return stats


def get_midweek_scoreboard_merged(week: int) -> pd.DataFrame:
    """
    Merge rate stats (OBP/ERA/WHIP) from opponent_history.parquet with manual
    counting stats from midweek_matchup_state.csv for the given week.
    Returns stat_name, my_value, opp_value, lower_is_better for all 10 categories.
    Rate stats come from Yahoo (real values midweek); counting stats from the CSV.
    """
    f = _fixed("opponent_history.parquet")
    df = _con().execute(f"""
        SELECT stat_name, my_value, opp_value, lower_is_better
        FROM read_parquet('{f}')
        WHERE is_current_week = true AND week = {week}
        ORDER BY stat_name
    """).fetchdf()

    counting = get_midweek_matchup_state(week)
    for cat, vals in counting.items():
        mask = df["stat_name"] == cat
        if mask.any():
            df.loc[mask, "my_value"]  = vals["mine"]
            df.loc[mask, "opp_value"] = vals["opp"]
        else:
            lower = cat in ("ERA", "WHIP")
            df = pd.concat([df, pd.DataFrame([{
                "stat_name": cat,
                "my_value": vals["mine"],
                "opp_value": vals["opp"],
                "lower_is_better": lower,
            }])], ignore_index=True)

    return df


def get_current_matchup_scores(week: Optional[int] = None) -> pd.DataFrame:
    """
    Live category scores for the current week from my perspective.
    On Wednesday runs, pass the current week number to merge counting stats
    from midweek_matchup_state.csv with rate stats from opponent_history.parquet.
    When week is None, returns rate stats only (Sunday run — counting stats not yet meaningful).
    Returns stat_name, my_value, opp_value, winning, gap.
    """
    if week is not None:
        df = get_midweek_scoreboard_merged(week)
    else:
        f = _fixed("opponent_history.parquet")
        df = _con().execute(f"""
            SELECT stat_name, my_value, opp_value, lower_is_better
            FROM read_parquet('{f}')
            WHERE is_current_week = true
            ORDER BY stat_name
        """).fetchdf()

    df["winning"] = df.apply(
        lambda r: r["my_value"] < r["opp_value"] if r["lower_is_better"]
                  else r["my_value"] > r["opp_value"],
        axis=1,
    )
    df["gap"] = df.apply(
        lambda r: r["opp_value"] - r["my_value"] if r["lower_is_better"]
                  else r["my_value"] - r["opp_value"],
        axis=1,
    )
    return df


def get_opponent_history() -> pd.DataFrame:
    """All completed matchups vs the current opponent, one row per (week, category)."""
    f = _fixed("opponent_history.parquet")
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{f}')
        WHERE is_current_week = false
        ORDER BY week, stat_name
    """).fetchdf()


def get_opponent_category_profile() -> pd.DataFrame:
    """
    Per-category summary of how the current opponent has performed historically.
    Includes mean, std, min, max, and their win rate against me per category.
    strength_label: strong (>=60% win rate vs me) | variable | weak.
    """
    f = _fixed("opponent_history.parquet")
    return _con().execute(f"""
        WITH completed AS (
            SELECT * FROM read_parquet('{f}') WHERE is_current_week = false
        )
        SELECT
            stat_name,
            lower_is_better,
            COUNT(*)            AS weeks,
            ROUND(AVG(opp_value), 3)    AS opp_mean,
            ROUND(STDDEV(opp_value), 3) AS opp_std,
            ROUND(MIN(opp_value), 3)    AS opp_min,
            ROUND(MAX(opp_value), 3)    AS opp_max,
            ROUND(AVG(
                CASE
                    WHEN lower_is_better THEN CAST(opp_value < my_value AS INT)
                    ELSE CAST(opp_value > my_value AS INT)
                END
            ), 2) AS opp_win_rate_vs_me,
            CASE
                WHEN AVG(
                    CASE
                        WHEN lower_is_better THEN CAST(opp_value < my_value AS INT)
                        ELSE CAST(opp_value > my_value AS INT)
                    END
                ) >= 0.60 THEN 'strong'
                WHEN AVG(
                    CASE
                        WHEN lower_is_better THEN CAST(opp_value < my_value AS INT)
                        ELSE CAST(opp_value > my_value AS INT)
                    END
                ) >= 0.40 THEN 'variable'
                ELSE 'weak'
            END AS strength_label
        FROM completed
        GROUP BY stat_name, lower_is_better
        ORDER BY opp_win_rate_vs_me DESC
    """).fetchdf()


def get_league_benchmarks() -> pd.DataFrame:
    """
    Per-team per-category season averages, league average, and win rates.
    league_avg = average team score per category per week across all teams.
    Used by Matchup agent for context and Roster Management for contribution scoring.
    """
    f = _fixed("league_benchmarks.parquet")
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{f}')
        ORDER BY stat_name, win_rate DESC
    """).fetchdf()


def get_category_priority() -> pd.DataFrame:
    """
    Ranks categories by action priority for the current week.

    priority:
      attack  = currently losing AND opponent historically weak here (exploit)
      defend  = currently winning AND opponent historically strong here (protect lead)
      concede = currently losing AND opponent historically dominant (accept loss)
      monitor = neither strongly winning nor losing

    Sorted: attack → defend → monitor → concede.
    """
    scores  = get_current_matchup_scores()
    profile = get_opponent_category_profile()
    merged  = scores.merge(profile, on="stat_name", how="left")

    def _priority(row):
        winning  = bool(row["winning"])
        strength = row.get("strength_label", "variable")
        if not winning and strength == "weak":
            return "attack"
        elif winning and strength == "strong":
            return "defend"
        elif not winning and strength == "strong":
            return "concede"
        else:
            return "monitor"

    order = {"attack": 0, "defend": 1, "monitor": 2, "concede": 3}
    merged["priority"]  = merged.apply(_priority, axis=1)
    merged["sort_key"]  = merged["priority"].map(order)
    return (
        merged
        .sort_values("sort_key")
        .drop(columns="sort_key")
        .reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# Future Predictor
# ---------------------------------------------------------------------------

def get_probable_starters() -> pd.DataFrame:
    """Upcoming probable starters for the fetched date window."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("probable_starters")}')
        ORDER BY date, home_team
    """).fetchdf()


def get_schedule() -> pd.DataFrame:
    """Upcoming MLB game schedule."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_latest("schedule")}')
        ORDER BY date, home_team
    """).fetchdf()


def get_park_factors() -> pd.DataFrame:
    """Park factors for all MLB stadiums."""
    return _con().execute(f"""
        SELECT *
        FROM read_parquet('{_fixed("park_factors.parquet")}')
    """).fetchdf()


def get_fa_pitcher_starts() -> pd.DataFrame:
    """
    FA pitchers who have confirmed or probable starts in the fetched date window.
    Joins pybaseball_fa pitching stats with probable_starters on player name (case-insensitive).
    Name mismatches may occur for accented names — verify manually if a known starter is missing.
    """
    fa_file       = _latest("pybaseball_fa")
    starters_file = _latest("probable_starters")

    return _con().execute(f"""
        WITH fa_pitchers AS (
            SELECT player_id, name, era, whip, k, sv, fip, ip, velocity, k_pct
            FROM read_parquet('{fa_file}')
            WHERE stat_type = 'pitching' AND "window" = 'season'
        ),
        all_starts AS (
            SELECT
                home_pitcher    AS pitcher_name,
                home_pitcher_id AS pitcher_mlbam_id,
                date,
                home_team AS pitcher_team,
                home_abbr AS pitcher_team_abbr,
                away_team AS opponent_team,
                away_abbr AS opponent_team_abbr,
                venue,
                'home' AS home_away
            FROM read_parquet('{starters_file}')
            WHERE home_pitcher IS NOT NULL
            UNION ALL
            SELECT
                away_pitcher,
                away_pitcher_id,
                date,
                away_team, away_abbr,
                home_team, home_abbr,
                venue,
                'away'
            FROM read_parquet('{starters_file}')
            WHERE away_pitcher IS NOT NULL
        )
        SELECT
            p.player_id,
            p.name,
            p.era, p.whip, p.k, p.sv, p.fip, p.ip, p.velocity, p.k_pct,
            s.date,
            s.pitcher_team,
            s.pitcher_team_abbr,
            s.opponent_team,
            s.opponent_team_abbr,
            s.venue,
            s.home_away
        FROM fa_pitchers p
        JOIN all_starts s
          ON LOWER(TRIM(p.name)) = LOWER(TRIM(s.pitcher_name))
        ORDER BY s.date, p.name
    """).fetchdf()


# ---------------------------------------------------------------------------
# Roster Management
# ---------------------------------------------------------------------------

def get_batter_contribution_scores(source: str = "roster") -> pd.DataFrame:
    """
    Per-batter per-category contribution relative to league average.
    Categories: R, HR, RBI, SB, OBP.

    contribution_delta = player season value - league average team weekly value.
    league_avg is at the TEAM level (not per-player). Interpret as:
    positive delta = player is adding above what an average team produces in this category.

    source: 'roster' | 'fa'
    """
    f  = _source_file(source)
    lb = _fixed("league_benchmarks.parquet")

    return _con().execute(f"""
        WITH player_season AS (
            SELECT player_id, name, r, hr, rbi, sb, obp
            FROM read_parquet('{f}')
            WHERE "window" = 'season' AND stat_type = 'batting'
        ),
        league AS (
            SELECT stat_name, AVG(league_avg) AS league_avg
            FROM read_parquet('{lb}')
            WHERE stat_name IN ('R', 'HR', 'RBI', 'SB', 'OBP')
            GROUP BY stat_name
        ),
        unpivoted AS (
            SELECT player_id, name, 'R'   AS cat, CAST(r   AS DOUBLE) AS val FROM player_season UNION ALL
            SELECT player_id, name, 'HR'  AS cat, CAST(hr  AS DOUBLE) AS val FROM player_season UNION ALL
            SELECT player_id, name, 'RBI' AS cat, CAST(rbi AS DOUBLE) AS val FROM player_season UNION ALL
            SELECT player_id, name, 'SB'  AS cat, CAST(sb  AS DOUBLE) AS val FROM player_season UNION ALL
            SELECT player_id, name, 'OBP' AS cat, CAST(obp AS DOUBLE) AS val FROM player_season
        )
        SELECT
            u.player_id,
            u.name,
            u.cat                             AS category,
            ROUND(u.val, 3)                   AS season_value,
            ROUND(l.league_avg, 3)            AS league_avg_team,
            ROUND(u.val - l.league_avg, 3)    AS contribution_delta,
            false                             AS lower_is_better
        FROM unpivoted u
        LEFT JOIN league l ON u.cat = l.stat_name
        ORDER BY u.name, u.cat
    """).fetchdf()


def get_pitcher_contribution_scores(source: str = "roster") -> pd.DataFrame:
    """
    Per-pitcher per-category contribution relative to league average.
    Categories: K, W, SV, ERA, WHIP.
    For ERA/WHIP (lower_is_better): contribution_delta = league_avg - player_value.
    Positive delta always means above-average contribution regardless of direction.

    source: 'roster' | 'fa'
    """
    f  = _source_file(source)
    lb = _fixed("league_benchmarks.parquet")

    return _con().execute(f"""
        WITH player_season AS (
            SELECT player_id, name, k, w, sv, era, whip
            FROM read_parquet('{f}')
            WHERE "window" = 'season' AND stat_type = 'pitching'
        ),
        league AS (
            SELECT stat_name, AVG(league_avg) AS league_avg
            FROM read_parquet('{lb}')
            WHERE stat_name IN ('K', 'W', 'SV', 'ERA', 'WHIP')
            GROUP BY stat_name
        ),
        unpivoted AS (
            SELECT player_id, name, 'K'    AS cat, CAST(k    AS DOUBLE) AS val, false AS lib FROM player_season UNION ALL
            SELECT player_id, name, 'W'    AS cat, CAST(w    AS DOUBLE) AS val, false AS lib FROM player_season UNION ALL
            SELECT player_id, name, 'SV'   AS cat, CAST(sv   AS DOUBLE) AS val, false AS lib FROM player_season UNION ALL
            SELECT player_id, name, 'ERA'  AS cat, CAST(era  AS DOUBLE) AS val, true  AS lib FROM player_season UNION ALL
            SELECT player_id, name, 'WHIP' AS cat, CAST(whip AS DOUBLE) AS val, true  AS lib FROM player_season
        )
        SELECT
            u.player_id,
            u.name,
            u.cat                                          AS category,
            ROUND(u.val, 3)                                AS season_value,
            ROUND(l.league_avg, 3)                         AS league_avg_team,
            ROUND(
                CASE WHEN u.lib THEN l.league_avg - u.val
                     ELSE u.val - l.league_avg END,
                3
            )                                              AS contribution_delta,
            u.lib                                          AS lower_is_better
        FROM unpivoted u
        LEFT JOIN league l ON u.cat = l.stat_name
        ORDER BY u.name, u.cat
    """).fetchdf()


def get_roster_slot_efficiency(source: str = "roster") -> pd.DataFrame:
    """
    Combines batter and pitcher contribution scores with positional scarcity.
    Returns one row per (player, category) with efficiency_score = delta * scarcity_factor.

    Roster Management uses this to identify drop candidates:
    lowest efficiency_score in categories the Matchup agent has marked 'concede' = best drops.

    source: 'roster' | 'fa'
    NOTE: scarcity uses P vs non-P as a proxy until Yahoo slot data is available.
    FA players always get no_drop=False.
    """
    batters  = get_batter_contribution_scores(source)
    pitchers = get_pitcher_contribution_scores(source)

    if source == "roster":
        player_meta = get_my_roster()
    else:
        f = _source_file(source)
        player_meta = _con().execute(f"""
            SELECT DISTINCT player_id, name, stat_type
            FROM read_parquet('{f}')
            WHERE "window" = 'season'
        """).fetchdf()
        player_meta["no_drop"] = False

    all_scores = pd.concat([batters, pitchers], ignore_index=True)
    all_scores = all_scores.merge(
        player_meta[["player_id", "stat_type", "no_drop"]],
        on="player_id",
        how="left"
    )

    def _scarcity(stat_type):
        return POSITION_SCARCITY.get("P" if stat_type == "pitching" else "OF", 1.0)

    all_scores["scarcity_factor"]  = all_scores["stat_type"].apply(_scarcity)
    all_scores["efficiency_score"] = (
        all_scores["contribution_delta"] * all_scores["scarcity_factor"]
    ).round(3)

    return all_scores.sort_values(
        ["efficiency_score", "player_id"],
        ascending=[True, True]
    ).reset_index(drop=True)
