# Fantasy Baseball Agentic AI System — Project Skill

## Purpose

This skill gives Claude Code full context to assist with building a fantasy baseball agentic AI system. The project has two phases:

- **Phase 1 (current):** Rust data ingestion binary + Parquet storage + DuckDB query interface
- **Phase 2 (future):** Claude Cowork agents that query the data layer and produce weekly recommendations

Read this entire file before writing any code or making architectural suggestions.

---

## What This System Does

The owner plays in a Yahoo Fantasy Baseball league using a **5x5 head-to-head categories format**. Each week (Monday–Sunday) they face one opponent. Whoever leads each category at Sunday night wins that category point. Most category points wins the matchup.

**Scoring categories:**
- Batting: Runs, Home Runs, RBIs, Stolen Bases, OBP
- Pitching: Wins, Saves, Strikeouts, ERA, WHIP

**Constraints:**
- 7 transactions per week (pick up free agent / drop player)
- 3 Injured List (IL) slots for real-life IL players
- Yahoo mobile app sends push notifications when a player hits the IL

**Decision rhythm:**
- Sunday night: full weekly planning session
- Wednesday: mid-week check-in, update on current category scores
- Ad hoc: triggered by Yahoo IL push notification

**Transaction mental model:** Owner thinks of Mon–Thu as first half, Fri–Sun as second half. The GM agent helps manage transaction budget across this split.

---

## Architecture Overview

```
[Owner]
  │ provides score updates, transaction count, IL alerts
  ▼
[GM / Orchestrator Agent]  ← Claude Cowork (Phase 2)
  │ synthesizes sub-agent outputs, holds punt logic, makes final call
  ├── Matchup Strategy Agent   (opponent roster, category gaps, punt decisions)
  ├── Roster Construction Agent (slot efficiency, waiver priority)
  ├── Trend Analyzer Agent      (7/14/30-day rolling performance signals)
  └── Future Predictor Agent    (probable starter matchups, park factors, platoon splits)
         │
         │ SQL queries (in-process, on-demand)
         ▼
[DuckDB] ← query interface only, no persistent DuckDB file
         │ reads Parquet files directly — Parquet is the source of truth
         ▼
[Parquet files] ← written by Rust ingestion binary, timestamped per run
         │
         ▼
[Rust ingestion binary]  ← THIS IS PHASE 1
  --mode full     (Sunday night — all sources)
  --mode midweek  (Wednesday — all sources)
  --mode adhoc    (after IL alert — Yahoo + MLB starters + Rotoworld only)
         │
         ├── Yahoo Fantasy Sports API (via YFPY OAuth2 — rosters, matchup, FA pool, IL status)
         ├── pybaseball (via thin Python shim — stats, advanced metrics, platoon splits)
         ├── MLB Stats API (free, no auth — probable starters)
         └── Rotoworld (scrape — injury news, last 24–72hrs depending on mode)
```

**Key architectural decisions:**
- DuckDB is the **query interface**, not a database. It reads Parquet files directly. There is no `.duckdb` file to manage.
- Parquet files are the **source of truth** and are timestamped (e.g. `stats_20260601.parquet`). This gives free historical snapshots.
- The Rust binary is the **only writer**. Agents only read.
- pybaseball is a Python library and cannot be called from Rust directly. Use a thin Python shim that outputs newline-delimited JSON to stdout; the Rust binary shells out to it.
- The system is **dormant** between runs. No server, no daemon, no scheduled jobs.

---

## Phase 1: Rust Ingestion Binary

### Goals for Phase 1

1. Build the Rust ingestion binary with all three `--mode` variants
2. Write clean, timestamped Parquet files for all data sources
3. Validate that DuckDB can query across the Parquet files correctly
4. Keep the Python shim as thin as possible — JSON output only

### Rust project structure

```
fantasy_ingest/
├── Cargo.toml
├── src/
│   ├── main.rs          # --mode flag, orchestrates fetch pipeline
│   ├── yahoo.rs         # Yahoo API OAuth2 + roster/matchup/FA fetches
│   ├── mlb.rs           # MLB Stats API probable starters
│   ├── news.rs          # Rotoworld scrape
│   ├── shim.rs          # shells out to Python shim, parses JSON
│   ├── parquet.rs       # writes DataFrames to timestamped Parquet files
│   └── models.rs        # all shared structs with serde derives
├── shim/
│   └── pybaseball_pull.py  # Python shim — outputs NDJSON to stdout
└── data/                # Parquet files land here
    ├── stats_20260601.parquet
    ├── rosters_20260601.parquet
    └── ...
```

### Cargo.toml dependencies

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
reqwest = { version = "0.12", features = ["json", "cookies"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
polars = { version = "0.40", features = ["parquet", "lazy"] }
anyhow = "1"
clap = { version = "4", features = ["derive"] }
chrono = { version = "0.4", features = ["serde"] }
```

### Data structs (models.rs)

```rust
use serde::{Deserialize, Serialize};
use chrono::NaiveDate;

#[derive(Debug, Serialize, Deserialize)]
pub struct Player {
    pub player_id: String,
    pub name: String,
    pub team: String,
    pub position: String,
    pub status: String,      // Active, IL-10, IL-60, DTD, etc.
    pub handedness: String,  // B/L/R (batter) or L/R (pitcher)
}

#[derive(Debug, Serialize, Deserialize)]
pub struct RosterEntry {
    pub player_id: String,
    pub roster_slot: String,  // C, 1B, OF, SP, RP, BN, IL
    pub team: String,         // "mine" | "opponent" | "FA"
    pub as_of: NaiveDate,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StatLine {
    pub player_id: String,
    pub window: String,     // "7d" | "14d" | "30d" | "season" | "career"
    pub stat_type: String,  // "batting" | "pitching"
    // Batting scoring categories
    pub runs: Option<f64>,
    pub hr: Option<f64>,
    pub rbi: Option<f64>,
    pub sb: Option<f64>,
    pub obp: Option<f64>,
    // Pitching scoring categories
    pub wins: Option<f64>,
    pub saves: Option<f64>,
    pub strikeouts: Option<f64>,
    pub era: Option<f64>,
    pub whip: Option<f64>,
    // Advanced batting
    pub xba: Option<f64>,
    pub xslg: Option<f64>,
    pub xwoba: Option<f64>,
    pub hard_hit_pct: Option<f64>,
    pub barrel_pct: Option<f64>,
    pub sprint_speed: Option<f64>,
    pub bb_pct: Option<f64>,
    pub k_pct: Option<f64>,
    pub babip: Option<f64>,
    pub chase_rate: Option<f64>,
    // Advanced pitching
    pub fip: Option<f64>,
    pub xfip: Option<f64>,
    pub siera: Option<f64>,
    pub lob_pct: Option<f64>,
    pub hr_fb_ratio: Option<f64>,
    pub k_minus_bb: Option<f64>,
    pub velocity: Option<f64>,
    pub as_of: NaiveDate,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct PlatoonSplit {
    pub player_id: String,
    pub window: String,     // "career" | "season"
    pub vs_hand: String,    // "L" | "R"
    pub pa: Option<i32>,
    pub obp: Option<f64>,
    pub slg: Option<f64>,
    pub woba: Option<f64>,
    pub as_of: NaiveDate,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ProbableStarter {
    pub pitcher_id: String,
    pub pitcher_name: String,
    pub game_date: NaiveDate,
    pub home_away: String,    // "home" | "away"
    pub opponent_team: String,
    pub park: String,
    pub as_of: NaiveDate,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct ParkFactor {
    pub park_name: String,
    pub team: String,
    pub runs_factor: f64,
    pub hr_factor: f64,
    pub hits_factor: f64,
    pub season: i32,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct NewsItem {
    pub player_id: Option<String>,
    pub player_name: String,
    pub headline: String,
    pub body: String,
    pub source: String,
    pub published_at: String,
    pub fetched_at: NaiveDate,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct MatchupState {
    pub week: i32,
    pub opponent_team_name: String,
    pub transactions_used: i32,
    pub transactions_remaining: i32,
    pub category_scores: serde_json::Value,  // {R: {mine: 45, opp: 38}, HR: {...}, ...}
    pub as_of: NaiveDate,
}
```

### Parquet file naming convention

```
data/
  players_YYYYMMDD.parquet
  rosters_YYYYMMDD.parquet
  stats_YYYYMMDD.parquet
  platoon_splits_YYYYMMDD.parquet
  probable_starters_YYYYMMDD.parquet
  park_factors_YYYY.parquet          (annual — overwrite, no timestamp needed)
  news_YYYYMMDD.parquet
  matchup_state_YYYYMMDD.parquet
  last_refresh.json                  (mode + timestamp, always overwritten)
```

Agents always query the most recent file per type. Use a helper that globs `stats_*.parquet` and takes the lexicographically last filename.

### Mode behavior

| Mode | Sources | Typical trigger |
|------|---------|----------------|
| `full` | All sources, all windows | Sunday night before weekly planning |
| `midweek` | All sources, all windows | Wednesday check-in |
| `adhoc` | Yahoo roster + FA pool, MLB probable starters, Rotoworld last 24hr | After Yahoo IL push notification |

### Python shim (shim/pybaseball_pull.py)

The shim is called by the Rust binary via `std::process::Command`. It accepts `--mode` and `--players` (comma-separated player names or IDs) and outputs newline-delimited JSON to stdout. One JSON object per line. Stderr for errors only.

```python
#!/usr/bin/env python3
"""
Thin pybaseball shim. Called by Rust binary. Outputs NDJSON to stdout.
Usage: python pybaseball_pull.py --mode full --players "Mike Trout,Shohei Ohtani"
"""
import argparse, json, sys
import pybaseball as pb

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["full", "midweek", "adhoc"], required=True)
    parser.add_argument("--players", required=True)
    args = parser.parse_args()

    pb.cache.enable()
    player_names = [p.strip() for p in args.players.split(",")]

    for name in player_names:
        try:
            # Fetch stats per player — adapt queries as needed
            # Output one JSON object per stat row
            row = {"player_name": name, "window": "season", ...}
            print(json.dumps(row))
            sys.stdout.flush()
        except Exception as e:
            print(f"ERROR: {name}: {e}", file=sys.stderr)

if __name__ == "__main__":
    main()
```

Keep the shim thin. Business logic lives in Rust. The shim's only job is to call pybaseball and serialize results.

### DuckDB query patterns (for reference and agent use)

DuckDB is called in-process from Python (agent side, Phase 2) or can be used via CLI for validation:

```python
import duckdb

# Always point at most recent file
import glob, os

def latest(prefix: str) -> str:
    files = sorted(glob.glob(f"data/{prefix}_*.parquet"))
    return files[-1]

con = duckdb.connect()  # in-process, no file

# Example: players due for positive regression
con.execute(f"""
    SELECT s.player_id, p.name, s.babip, s.xwoba, s.hard_hit_pct
    FROM read_parquet('{latest("stats")}') s
    JOIN read_parquet('{latest("players")}') p ON s.player_id = p.player_id
    WHERE s.window = '14d'
      AND s.stat_type = 'batting'
      AND s.babip > 0.380
      AND s.xwoba > 0.340
    ORDER BY s.xwoba DESC
""")

# Example: this week's SP streaming candidates
con.execute(f"""
    SELECT ps.pitcher_name, ps.game_date, ps.opponent_team, pf.hr_factor
    FROM read_parquet('{latest("probable_starters")}') ps
    LEFT JOIN read_parquet('data/park_factors_2026.parquet') pf
      ON ps.park = pf.park_name
    WHERE ps.game_date BETWEEN current_date AND current_date + 7
    ORDER BY pf.hr_factor ASC, ps.game_date
""")
```

DuckDB's `read_parquet()` scans the file directly — no import, no persistent DuckDB state needed.

---

## Data Sources

### Yahoo Fantasy Sports API
- **Auth:** OAuth 2.0 via developer.yahoo.com app (Consumer Key + Consumer Secret)
- **Python wrapper:** YFPY (`pip install yfpy`) — also usable as reference for Rust implementation
- **Key data:** current roster, opponent roster, FA pool, IL status, transaction count, matchup scores
- **Note:** Yahoo's IL status has an overnight lag — not suitable for real-time injury detection. Owner relies on Yahoo mobile push notifications for that.

### pybaseball (Python shim)
- **Install:** `pip install pybaseball`
- **Key functions:** `batting_stats()`, `pitching_stats()`, `statcast_batter()`, `statcast_pitcher()`
- **Stat windows to pull:** season YTD, last 30 days, last 14 days, last 7 days
- **Platoon splits:** career and season YTD vs LHP/RHP
- **Name normalization:** player names must be normalized before joining across sources (e.g. "Mike Trout" vs "Michael Trout"). Build a canonical name → player_id map early.

### MLB Stats API
- **Base URL:** `https://statsapi.mlb.com/api/v1/`
- **No auth required**
- **Probable starters endpoint:** `schedule?sportId=1&date=YYYY-MM-DD&hydrate=probablePitcher`
- **Pull range:** next 7 days
- **Update frequency:** twice daily in ideal system; in this architecture, updated on each binary run

### Rotoworld / NBC Sports injury feed
- **Source:** Scrape `https://www.nbcsports.com/fantasy/baseball/player-news`
- **Mode behavior:** adhoc pulls last 24hr; full/midweek pulls last 72hr
- **Key fields:** player name, headline, body text, timestamp
- **Match to player_id** using name normalization map

### Park Factors
- **Source:** FanGraphs park factors table (pulled once per season)
- **Fields needed:** runs factor, HR factor, hits factor — per park
- **Storage:** `park_factors_2026.parquet` — static, no timestamp rotation needed

---

## Advanced Stats Reference

### Batting — what each stat tells the agents

| Stat | What it signals | Interval |
|------|----------------|----------|
| xBA, xSLG, xwOBA | Expected production; gap vs actual = regression candidate | Season |
| Hard Hit %, Barrel % | Leading indicator for power; stable over 14d+ | Season |
| Sprint Speed | Stolen base sustainability | Season |
| BB%, K% | OBP sustainability; plate discipline | Season, 30d |
| BABIP | Luck indicator; >.350 = likely regression, <.250 = likely positive regression | 14d, 30d |
| Chase Rate, Whiff Rate | Early mechanical breakdown warning | 14d |

### Pitching — what each stat tells the agents

| Stat | What it signals | Interval |
|------|----------------|----------|
| FIP, xFIP | ERA predictor independent of defense | Season |
| SIERA | Best ERA estimator; accounts for GB/FB profile | Season |
| LOB% | Strand rate; >85% = ERA inflation coming | 30d |
| HR/FB ratio | HR suppression luck; regresses to ~11% | Season |
| K%, BB%, K-BB% | True stuff quality | Season, 30d |
| Velocity | Physical health signal; declining velo = early warning | 14d |

### Platoon splits — how agents use them

- Pull **career** as primary signal (larger sample)
- Pull **season YTD** as secondary check
- Combine with `probable_starters` to identify favorable/unfavorable matchups
- Key join: starter handedness → batter platoon split vs that hand

---

## Phase 2 Preview (Agents — Claude Cowork)

Phase 2 is not the current focus but informs data requirements. Each agent will:
1. Connect to DuckDB in-process
2. Query the most recent Parquet files
3. Return structured analysis to the GM agent

**GM Agent inputs (from owner):**
- Current category scores (Wed/Thu check-in)
- Transactions used this week
- Any IL alerts received since last refresh

**GM Agent outputs:**
- Weekly strategy statement (which categories to target, which to punt)
- Prioritized transaction recommendations with rationale
- Red flags (slumping players, IL risks)

**Agent → data source mapping:**

| Agent | Parquet files queried |
|-------|-----------------------|
| Matchup Strategy | rosters, stats (season), matchup_state |
| Roster Construction | rosters, stats (14d, season), players, news |
| Trend Analyzer | stats (7d, 14d, 30d, season), news |
| Future Predictor | probable_starters, park_factors, platoon_splits, stats (season) |
| GM / Orchestrator | matchup_state + all sub-agent outputs |

---

## Implementation Guidance for Claude Code

### Phase 1 task order

1. **Scaffold the Rust project** — `cargo new`, Cargo.toml dependencies, `models.rs` structs, `--mode` flag via clap
2. **MLB Stats API** — simplest fetch; no auth; validates async HTTP + serde pipeline end-to-end
3. **Python shim** — get pybaseball pulling stats and outputting NDJSON; validate output format
4. **Shim integration in Rust** — `std::process::Command`, parse NDJSON into `StatLine` structs
5. **Yahoo OAuth2** — hardest piece; store tokens in `~/.fantasy_ingest/tokens.json`; handle refresh
6. **Rotoworld scrape** — HTML parse with `scraper` crate; match player names to IDs
7. **Parquet writes** — use `polars` DataFrame API; one file per data type per run
8. **DuckDB validation** — use DuckDB CLI or Python to run sample queries against written files
9. **Mode gating** — wire `--mode` to control which fetches run
10. **last_refresh.json** — stamp mode + timestamp on every successful run

### Error handling principles
- Use `anyhow` for all error propagation in the binary
- Individual player fetch failures should log and continue, not abort the run
- If a source fails entirely (e.g. Yahoo auth expired), log clearly and continue with other sources
- Always write `last_refresh.json` even on partial failure, with a `partial: true` flag

### Name normalization
Player names vary across sources. Build a `name_map.json` (or a Parquet lookup table) early:
```json
{"mike trout": "trout-mike-001", "michael trout": "trout-mike-001"}
```
Normalize all names to lowercase, stripped of accents, before lookup. This is critical for joining across Yahoo, pybaseball, and MLB Stats API.

### Testing approach
- Unit test each fetch function with recorded API responses (save raw JSON/HTML fixtures)
- Integration test the full pipeline in `--mode adhoc` first (smallest scope)
- Use DuckDB CLI to manually validate Parquet output before building agent queries

---

## Token Optimization for Claude Cowork Agents

Agent context size is a first-class constraint. The Rust binary is responsible for pre-processing data so agents receive narrow, pre-aggregated inputs — not raw stat dumps.

### Additional output files the Rust binary must produce

| File | Purpose |
|------|---------|
| `signals_YYYYMMDD.parquet` | Pre-computed per-player signals: babip_14d, xwoba_season, babip_vs_xwoba_delta, trending_up flag, starts_this_week, favorable_matchup_pct. Agents read this instead of computing from raw stats. |
| `fa_targets_YYYYMMDD.parquet` | Top 20-30 free agents pre-ranked by owned%, recent performance, and positional need. Agents never receive the full FA pool. |
| `summary_YYYYMMDD.json` | Human+agent-readable snapshot: current category standings, transactions remaining, your roster with one-line status per player, top 10 FA targets. This is the GM agent's starting context — keeps its prompt small. |
| `decisions_YYYYMMDD.json` | Written by the GM agent (not the Rust binary) after each session. Captures recommendations for eval purposes — see Evals section below. |

### Agent context rules (enforced in Phase 2 prompt design)

- Each sub-agent receives **only the data slice relevant to its job**:
  - Matchup Strategy: your roster + opponent roster + category scores only
  - Trend Analyzer: your roster + fa_targets only
  - Future Predictor: probable starters + park factors + platoon splits only
  - Roster Construction: your roster + players + news only
- **GM orchestrator receives sub-agent outputs only** — never raw Parquet data
- All agents read from `signals` and `fa_targets` before falling back to raw stat files

### Park factors
- Values sourced from FanGraphs (Baseball Savant pull) as a manually maintained CSV (`data/park_factors_2026.csv`)
- Stored as decimal multipliers (FanGraphs index ÷ 100): e.g. 125 → 1.25
- Sutter Health Park (Athletics / SAC) intentionally excluded — insufficient MLB data. DuckDB LEFT JOIN handles missing rows as NULL; agents treat NULL park factor as "no adjustment available"
- Updated once per season by dropping a new CSV and re-running the binary

---

## AI Evals

Fantasy baseball provides **ground truth every Sunday night** (category win/loss), making it unusually well-suited for outcome-based LLM evals. Evals are built into the project from day one.

### Eval types to implement

| Eval Type | What it measures | Method |
|-----------|-----------------|--------|
| **Outcome evals** | Did recommended pickups help win categories that week? | Log recommendations → compare to Sunday result |
| **Calibration evals** | When agent says "strong add," does it win more often than "speculative add"? | Track confidence label vs outcome over season |
| **Consistency evals** | Same recommendation regardless of input ordering or prompt phrasing? | Run identical inputs 3x, diff outputs |
| **Retrieval evals** | Did agent cite actual Parquet stats vs hallucinated numbers? | Spot-check cited figures against source files |
| **Counterfactual evals** | What would have happened following last week's recommendation exactly? | Simulate resulting roster, score against actual outcome |

### decisions_YYYYMMDD.json schema

Written by the GM agent at the end of every session. This is the primary eval artifact.

```json
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
  "transactions_budget": {"first_half": 4, "second_half": 3},
  "confidence": "high",
  "rationale_summary": "one paragraph"
}
```

### Eval script (to be built with Claude Code assistance at end of season)

An `evals/` directory will hold:
- `eval_runner.py` — compares `decisions_*.json` logs to actual weekly outcomes
- `outcomes_log.csv` — manually maintained: week, category wins/losses, actual transactions made
- `eval_results/` — scored outputs per week, aggregated season summary

The eval script will produce: per-recommendation accuracy, confidence calibration curves, and season-level agent performance summary. Claude Code will assist writing this at end of season.

### Project structure additions

```
fantasy_ingest/
├── evals/
│   ├── eval_runner.py        # to be written end of season
│   ├── outcomes_log.csv      # manually maintained weekly
│   └── eval_results/         # scored outputs land here
└── data/
    └── decisions_YYYYMMDD.json   # written by GM agent each session
```

---

## Key Decisions Already Made (Do Not Revisit)

- **DuckDB as query interface** (not database): Parquet files are source of truth; no `.duckdb` file
- **Rust for ingestion binary**: learning project; pybaseball via Python shim is accepted tradeoff
- **SQLite is not used**: replaced by Parquet + DuckDB
- **No scheduler**: manual runs only (Sun, Wed, adhoc)
- **No Docker**: plain Python venv + Rust toolchain
- **No server**: system is dormant between runs
- **Yahoo mobile push notifications** handle real-time IL alerts; system does not need to poll for injuries
- **Park factors via CSV**: manually maintained once per season; ingested by Rust binary into Parquet
- **Sutter Health Park excluded** from park factors: insufficient MLB data; treated as NULL in queries
- **Pre-aggregated signals file**: Rust binary computes agent-ready signals; agents do not do raw stat math
- **FA pool pre-filtered**: top 20-30 targets only written to `fa_targets` file; agents never see full pool
- **Evals built in from day one**: `decisions_YYYYMMDD.json` captured every session; eval script written end of season with Claude Code assistance
- **20 IP weekly minimum**: League rule — if total team IP falls below 20 by Sunday night, all pitching categories are forfeited. The Matchup and GM agents must track IP-to-date mid-week and flag if the team is at risk of not hitting the threshold. In the final Rust binary version, IP will be pulled from Yahoo and included in `matchup_state`. For bootstrap, this constraint is noted but not enforced. The GM agent should treat IP pace as a higher priority than individual pitcher ERA/WHIP when the team is below ~14 IP by Wednesday.
- **Handedness notation**: `B` (switch hitter) used in `my_roster.csv`; `S` used in `opponent_roster.csv`. Standardize to `S` across both files when the Rust binary takes over — it will pull handedness directly from Yahoo/MLB API.

## Phase 2 Enhancement: League-Wide Dominance Benchmarking (Matchup Agent)

Currently the Matchup Agent's `get_opponent_tendencies()` only measures **volatility** (consistency: high/medium/low) for a single opponent — it does not measure **dominance** relative to the rest of the league.

The distinction matters: a category can have low volatility (the opponent is consistent week to week) while still being a weak category for them relative to the league. Example: Connor averages 1 SB/week with low volatility — he's *consistent*, but if the league average is 4 SB/week, he's not *dominant*, and the category is still attackable despite the low volatility rating.

Once the Rust binary is pulling weekly historical data for **all teams** in the league (not just the current opponent), add a second dimension to the tendencies output:
- `dominance_rating`: high/medium/low — opponent's average in this category relative to league-wide average for the same category
- The Sunday `category_rating` should become a 2x2 combination of volatility + dominance (e.g. "low volatility + low dominance" = confidently attackable; "low volatility + high dominance" = hard to beat)

This requires bootstrapping all other teams' historical data, which is a heavier lift than the single-opponent bootstrap — deferred until the Rust binary automates the pull.
