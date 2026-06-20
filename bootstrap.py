# bootstrap.py — run once to seed data from manual CSVs
import polars as pl
from datetime import date
import os

today = date.today().strftime("%Y%m%d")
os.makedirs("data", exist_ok=True)

# Read your manually exported CSVs, write as Parquet
roster = pl.read_csv("bootstrap/my_roster.csv")
roster.write_parquet(f"data/rosters_{today}.parquet")

stats = pl.read_csv("bootstrap/my_stats.csv")
stats.write_parquet(f"data/stats_{today}.parquet")

matchup_state = pl.read_csv("bootstrap/matchup_state.csv")
matchup_state.write_parquet(f"data/matchup_state_{today}.parquet")

opponent_roster = pl.read_csv("bootstrap/opponent_roster.csv")
opponent_roster.write_parquet(f"data/opponent_roster_{today}.parquet")

opponent_history = pl.read_csv("bootstrap/opponent_history.csv")
opponent_history.write_parquet(f"data/opponent_history_{today}.parquet")