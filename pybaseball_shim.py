# pybaseball_shim.py
import argparse
import pybaseball as pb
import polars as pl
from datetime import date, timedelta
import pandas as pd

BATTING_POSITIONS = {"C", "1B", "2B", "3B", "SS", "OF", "CI", "MI", "UT"}
PITCHING_POSITIONS = {"SP", "RP", "P"}
FIP_CONSTANT = 3.15

def _extract_split(splits_df, stat: str):
    try:
        return float(splits_df[stat].values[0])
    except (KeyError, IndexError, ValueError):
        return None

def get_splits_stats(mlbam_id: int, season: int) -> dict:
    empty = pd.DataFrame()

    try:
        season_splits = pb.get_splits(mlbam_id, season, pitching_splits=False)
    except (IndexError, KeyError, Exception):
        season_splits = {}

    try:
        career_splits = pb.get_splits(mlbam_id, pitching_splits=False)
    except (IndexError, KeyError, Exception):
        career_splits = {}

    vsl_s = season_splits.get("vsl", empty)
    vsr_s = season_splits.get("vsr", empty)
    vsl_c = career_splits.get("vsl", empty)
    vsr_c = career_splits.get("vsr", empty)

    return {
        "obp_vs_lhp":         _extract_split(vsl_s, "OBP"),
        "slg_vs_lhp":         _extract_split(vsl_s, "SLG"),
        "woba_vs_lhp":        _extract_split(vsl_s, "wOBA"),
        "obp_vs_rhp":         _extract_split(vsr_s, "OBP"),
        "slg_vs_rhp":         _extract_split(vsr_s, "SLG"),
        "woba_vs_rhp":        _extract_split(vsr_s, "wOBA"),
        "career_obp_vs_lhp":  _extract_split(vsl_c, "OBP"),
        "career_slg_vs_lhp":  _extract_split(vsl_c, "SLG"),
        "career_woba_vs_lhp": _extract_split(vsl_c, "wOBA"),
        "career_obp_vs_rhp":  _extract_split(vsr_c, "OBP"),
        "career_slg_vs_rhp":  _extract_split(vsr_c, "SLG"),
        "career_woba_vs_rhp": _extract_split(vsr_c, "wOBA"),
    }

def _compute_chase_rate(df) -> float:
    outside = df.filter(pl.col("zone").cast(pl.Float64, strict=False) >= 11) # Statcast zones 11-14 are outside the strike zone
    swings = outside.filter(
        pl.col("description").is_in(["swinging_strike", "foul", "hit_into_play", 
             "foul_tip", "swinging_strike_blocked"])
    )
    if outside.height == 0:
        return None
    return swings.height / outside.height

def _fix_null_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.select([
        pl.col(c).cast(pl.Float64)
        if df[c].dtype in (pl.Null, pl.Int64, pl.Int32, pl.Int16, pl.Int8)
        else pl.col(c)
        for c in df.columns
    ])

def get_batting_stats_windows(windows):
    fangraphs_cache = {window_name: pl.from_pandas(pb.batting_stats_range(start, end)) for window_name, (start, end) in windows.items()}
    return fangraphs_cache

def get_pitching_stats_window(windows):
    fangraphs_cache = {window_name: pl.from_pandas(pb.pitching_stats_range(start, end)) for window_name, (start, end) in windows.items()}
    return fangraphs_cache

def resolve_id(name: str) -> tuple[int, int, str, str]:
    # returns (mlbam_id, fangraphs_id, stat_type, team_abbr)
    parts      = name.split(":", 2)
    fullname   = parts[0]
    pos        = parts[1]
    team_abbr  = parts[2] if len(parts) > 2 else ""

    first, last = fullname.rsplit(" ", 1)

    if pos.upper() in BATTING_POSITIONS:
        stat_type = "batting"
    elif pos.upper() in PITCHING_POSITIONS:
        stat_type = "pitching"
    else:
        stat_type = "batting"

    data = pb.playerid_lookup(last, first, fuzzy=True)
    mlbam_id     = data.iloc[0]['key_mlbam']
    fangraphs_id = data.iloc[0]['key_fangraphs']
    return (mlbam_id, fangraphs_id, stat_type, team_abbr)

def get_batter_stats(mlbam_id, fangraphs_id, name, season, windows, fangraphs_cache_dict, sprint_df, team_abbr="", min_pa=0) -> pl.DataFrame:
    rows = []
    clean_name = name.split(":")[0].strip()

    for key, w in windows.items():
        start_date = w[0]
        end_date = w[1]

        # Parse Statcast data
        sc_data = pl.from_pandas(pb.statcast_batter(start_date, end_date, player_id = mlbam_id))
        pa_count = sc_data.filter(
            pl.col("woba_denom").cast(pl.Float64, strict=False) == 1
        ).height
        if pa_count > 0 and key == "season" and pa_count < min_pa:
            print(f"  [SKIP] {name} — only {pa_count} PA (min {min_pa})")
            continue

        pa_events = sc_data.filter(pl.col("woba_denom").cast(pl.Float64, strict=False) == 1)
        xwoba = pa_events["estimated_woba_using_speedangle"].mean() if pa_events.height > 0 else None
        xba = pa_events["estimated_ba_using_speedangle"].mean() if pa_events.height > 0 else None
        batted = sc_data.filter(pl.col("type") == "X")
        if batted.height > 0:
            hard_hit_pct = (batted.filter(pl.col("launch_speed").cast(pl.Float64, strict=False) >= 95).height / batted.height)
            barrel_pct = batted.filter(pl.col("launch_speed_angle").cast(pl.Float64, strict=False) == 6).height / batted.height
        else:
            hard_hit_pct = None
            barrel_pct = None
        chase_rate = _compute_chase_rate(sc_data)
        
        # Parse Fangraphs cache over our window and for our player
        fg_data = fangraphs_cache_dict[key]
        player_row = fg_data.filter(pl.col("mlbID").cast(pl.Utf8) == str(int(mlbam_id)))

        # BABIP = (H - HR) / (AB - SO - HR + SF)
        if player_row.height > 0:
            h = player_row["H"].item()
            hr = player_row["HR"].item()
            ab = player_row["AB"].item()
            so = player_row["SO"].item()
            sf = player_row["SF"].item() if "SF" in player_row.columns else 0
            denom = ab - so - hr + sf
            babip = (h - hr) / denom if denom > 0 else None
        else:
            babip = None

        row = {
            "player_id": f"{clean_name.lower().replace(' ', '-')}-001",
            "name": clean_name,
            "team": team_abbr,
            "stat_type": "batting",
            "window": key,
            "r": player_row["R"].item() if player_row.height > 0 else None,
            "hr": player_row["HR"].item() if player_row.height > 0 else None,
            "rbi": player_row["RBI"].item() if player_row.height > 0 else None,
            "sb": player_row["SB"].item() if player_row.height > 0 else None,
            "obp": player_row["OBP"].item() if player_row.height > 0 else None,
            "bb_pct": (player_row["BB"].item() / player_row["PA"].item()) if player_row.height > 0 and player_row["PA"].item() > 0 else None,
            "k_pct": (player_row["SO"].item() / player_row["PA"].item()) if player_row.height > 0 and player_row["PA"].item() > 0 else None,
            "babip": babip,
            "xwoba": xwoba,
            "xba": xba,
            "hard_hit_pct": hard_hit_pct,
            "barrel_pct": barrel_pct,
            "chase_rate": chase_rate,
            "sprint_speed": None
        }
        rows.append(row)

    # Get Season Sprint Speed
    player_sprint = sprint_df.filter(pl.col("player_id").cast(pl.Int64) == int(mlbam_id))
    sprint_speed = (player_sprint["sprint_speed"].item()  if player_sprint.height > 0 else None)

    # Get Season and Career Splits
    splits = get_splits_stats(mlbam_id, season)

    # Patch only the season row — splits don't apply to rolling windows
    for row in rows:
        if row["window"] == "season":
            row.update(splits)
            row["sprint_speed"] = sprint_speed
            break

    return pl.DataFrame(rows)

def get_pitcher_stats(mlbam_id, name, windows, fangraphs_pitching_cache, team_abbr="", min_bf=0) -> pl.DataFrame:
    rows = []
    clean_name = name.split(":")[0].strip()

    for key, w in windows.items():
        start_date = w[0]
        end_date = w[1]

        # Parse Fangraphs Stats
        fg_data = fangraphs_pitching_cache[key]
        player_row = fg_data.filter(pl.col("mlbID").cast(pl.Utf8) == str(int(mlbam_id)))

        # Calculate LOB
        if player_row.height > 0:
            bf  = player_row["BF"].item()
            so  = player_row["SO"].item()
            bb  = player_row["BB"].item()
            hbp = player_row["HBP"].item()
            h   = player_row["H"].item()
            hr  = player_row["HR"].item()
            r   = player_row["R"].item()
            ip  = player_row["IP"].item()

            lob_denom = h + bb + hbp - (1.4 * hr)
            lob_pct = (h + bb + hbp - r) / lob_denom if lob_denom > 0 else None
            fip = ((13*hr + 3*(bb+hbp) - 2*so) / ip + FIP_CONSTANT) if ip > 0 else None
        else:
            lob_pct = None
            fip = None

        # Parse Statcast data
        sc_data = pl.from_pandas(pb.statcast_pitcher(start_date, end_date, player_id = mlbam_id))
        bf_count = sc_data.height  # each row is one pitch; rough proxy is row count
        if bf_count > 0 and key == "season" and bf_count < min_bf:
            print(f"  [SKIP] {name} — only {bf_count} batters faced (min {min_bf} BF)")
            continue
        
        # Velocity — fastballs only for meaningful signal
        fastballs = sc_data.filter(pl.col("pitch_type").is_in(["FF", "SI", "FC"]))
        velocity = fastballs["release_speed"].mean() if fastballs.height > 0 else None

        # Whiff % = swinging strikes / total swings
        swinging_strikes = sc_data.filter(
            pl.col("description").is_in(["swinging_strike", "swinging_strike_blocked"])
        )
        swings = sc_data.filter(
            pl.col("description").is_in([
                "swinging_strike", "swinging_strike_blocked",
                "foul", "foul_tip", "hit_into_play",
                "foul_bunt", "missed_bunt"
            ])
        )
        whiff_pct = swinging_strikes.height / swings.height if swings.height > 0 else None

        # Zone % = pitches in zone 1-9 / total pitches (exclude null zones)
        pitched = sc_data.filter(pl.col("zone").is_not_null())
        in_zone = pitched.filter(pl.col("zone").cast(pl.Float64, strict=False) <= 9)
        zone_pct = in_zone.height / pitched.height if pitched.height > 0 else None
        
        row = {
            "player_id": f"{clean_name.lower().replace(' ', '-')}-001",
            "name": clean_name,
            "team": team_abbr,
            "stat_type": "pitching",
            "window": key,
            "w": player_row["W"].item() if player_row.height > 0 else None,
            "sv": player_row["SV"].item() if player_row.height > 0 else None,
            "k": player_row["SO"].item() if player_row.height > 0 else None,
            "era": player_row["ERA"].item() if player_row.height > 0 else None,
            "whip": player_row["WHIP"].item() if player_row.height > 0 else None,
            "ip": player_row["IP"].item() if player_row.height > 0 else None,
            "k_pct": (player_row["SO"].item() / player_row["BF"].item()) if player_row.height > 0 and player_row["BF"].item() > 0 else None,
            "bb_pct": (player_row["BB"].item() / player_row["BF"].item()) if player_row.height > 0 and player_row["BF"].item() > 0 else None,
            "k_bb_pct":  ((player_row["SO"].item() - player_row["BB"].item()) / player_row["BF"].item()) if player_row.height > 0 and player_row["BF"].item() > 0 else None,
            "babip": player_row["BAbip"].item() if player_row.height > 0 else None,
            "lob_pct":  lob_pct,
            "velocity": velocity,
            "whiff_pct": whiff_pct,
            "zone_pct": zone_pct,
            "fip": fip,
            "xfip": None 
        }
        rows.append(row)
    return pl.DataFrame(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--players", required=True)  # comma-separated names
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--min_pa", type=int, default=0)
    parser.add_argument("--min_bf", type=int, default=0)
    args = parser.parse_args()

    players = [p.strip() for p in args.players.split(",")]
    windows = {
        "season": (f"{args.season}-03-01", date.today().isoformat()),
        "30d": ((date.today() - timedelta(days=30)).isoformat(), date.today().isoformat()),
        "14d": ((date.today() - timedelta(days=14)).isoformat(), date.today().isoformat()),
        "7d": ((date.today() - timedelta(days=7)).isoformat(), date.today().isoformat())
    }

    # Get multi-player, full season data in one call and pass to any get_batter_stats calls
    fangraphs_cache_dict = get_batting_stats_windows(windows)
    sprint_df = pl.from_pandas(pb.statcast_sprint_speed(args.season))

    # Get multi-player, full season data in one call and pass to any get_pitcher_stats calls
    fangraphs_pitching_cache = get_pitching_stats_window(windows)
    
    frames = []
    for name in players:
        mlbam_id, fangraphs_id, stat_type, team_abbr = resolve_id(name)
        if stat_type == "batting":
            df = get_batter_stats(mlbam_id, fangraphs_id, name, args.season, windows, fangraphs_cache_dict, sprint_df, team_abbr=team_abbr, min_pa=args.min_pa)
        else:
            df = get_pitcher_stats(mlbam_id, name, windows, fangraphs_pitching_cache, team_abbr=team_abbr, min_bf=args.min_bf)

        if df.height == 0:
            print(f"  [SKIP] {name.split(':')[0]} — no rows in any window, dropping from output")
            continue

        frames.append(_fix_null_columns(df))

    if not frames:
        print("Warning: no player data collected, writing empty parquet")
        pl.DataFrame().write_parquet(args.output)
        return

    pl.concat(frames, how="diagonal").write_parquet(args.output)

if __name__ == "__main__":
    main()