"""
python_orchestrator.py — two-phase orchestration for the Fantasy Baseball GM system.

Usage:
    python3 python_orchestrator.py --mode full --phase a        # Sunday: pull data + evals
    python3 python_orchestrator.py --mode full --phase b        # Sunday: run agents + decisions
    python3 python_orchestrator.py --mode midweek --week 14     # Wednesday: pull data + agents
    python3 python_orchestrator.py --mode adhoc --position OF   # Injury replacement
"""

import asyncio
import subprocess
import sys
import argparse
import re
import json
from datetime import date
from pathlib import Path

import pandas as pd
from anthropic import AsyncAnthropic

from data_client import (
    get_batter_trend_signals,
    get_pitcher_trend_signals,
    get_current_matchup_scores,
    get_opponent_roster_list,
    get_opponent_category_profile,
    get_category_priority,
    get_league_benchmarks,
    get_probable_starters,
    get_schedule,
    get_park_factors,
    get_fa_pitcher_starts,
    get_batter_contribution_scores,
    get_batter_contribution_scores_14d,
    get_pitcher_contribution_scores,
    get_pitcher_contribution_scores_14d,
    get_roster_slot_efficiency,
    get_fa_positions,
)

DATA_DIR = Path("data")
SKILLS_DIR = Path("skills")
MODEL = "claude-sonnet-4-5-20251001"


def load_skill(filename: str) -> str:
    return (SKILLS_DIR / filename).read_text()


def strip_fences(text: str) -> str:
    """Remove markdown code fences Claude sometimes wraps output in."""
    text = text.strip()
    text = re.sub(r"^```[a-z]*\n", "", text)
    text = re.sub(r"\n```$", "", text)
    return text.strip()


async def run_agent(
    client: AsyncAnthropic,
    skill_file: str,
    context: str,
    output_path: str,
) -> str:
    response = await client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=load_skill(skill_file),
        messages=[{"role": "user", "content": context}],
    )
    output = strip_fences(response.content[0].text)
    Path(output_path).write_text(output)
    print(f"  wrote {output_path}")
    return output_path


def split_roster_management_output(json_path: str, today: str):
    """
    Parse the Roster Management JSON output and write the four CSV files
    expected by the GM skill and eval runner.
    """
    data = json.loads(Path(json_path).read_text())

    sections = {
        "roster_batters":  f"data/roster_management_batter_output_{today}.csv",
        "roster_pitchers": f"data/roster_management_pitcher_output_{today}.csv",
        "fa_batters":      f"data/roster_management_batter_fa_output_{today}.csv",
        "fa_pitchers":     f"data/roster_management_pitcher_fa_output_{today}.csv",
    }

    for key, csv_path in sections.items():
        rows = data.get(key, [])
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        print(f"  wrote {csv_path} ({len(rows)} rows)")


def build_roster_mgmt_context() -> str:
    return "\n\n".join([
        "## Roster batter contribution (season)\n"
            + get_batter_contribution_scores("roster").to_csv(index=False),
        "## Roster batter contribution (14d)\n"
            + get_batter_contribution_scores_14d("roster").to_csv(index=False),
        "## Roster pitcher contribution (season)\n"
            + get_pitcher_contribution_scores("roster").to_csv(index=False),
        "## Roster pitcher contribution (14d)\n"
            + get_pitcher_contribution_scores_14d("roster").to_csv(index=False),
        "## Roster slot efficiency\n"
            + get_roster_slot_efficiency("roster").to_csv(index=False),
        "## FA batter contribution (season)\n"
            + get_batter_contribution_scores("fa").to_csv(index=False),
        "## FA batter contribution (14d)\n"
            + get_batter_contribution_scores_14d("fa").to_csv(index=False),
        "## FA pitcher contribution (season)\n"
            + get_pitcher_contribution_scores("fa").to_csv(index=False),
        "## FA pitcher contribution (14d)\n"
            + get_pitcher_contribution_scores_14d("fa").to_csv(index=False),
        "## FA slot efficiency\n"
            + get_roster_slot_efficiency("fa").to_csv(index=False),
    ])


# ---------------------------------------------------------------------------
# Phase A: pull data via Rust binary + run evals (Sunday only)
# ---------------------------------------------------------------------------

def run_phase_a():
    print("[phase a] running Rust binary --mode full")
    subprocess.run(
        ["./target/release/fantasy_ingest", "--mode", "full"],
        check=True,
    )
    print("[phase a] running evals")
    subprocess.run(["python3", "eval_runner.py"], check=True)
    print("[phase a] done — review evals/eval_report_*.json before running phase b")


# ---------------------------------------------------------------------------
# Phase B: run agents + produce decisions JSON (Sunday)
# ---------------------------------------------------------------------------

async def run_full_agents(today: str):
    client = AsyncAnthropic()

    trend_context = "\n\n".join([
        "## Roster batter signals\n"
            + get_batter_trend_signals("roster").to_csv(index=False),
        "## Roster pitcher signals\n"
            + get_pitcher_trend_signals("roster").to_csv(index=False),
        "## Opponent batter signals\n"
            + get_batter_trend_signals("opponent").to_csv(index=False),
        "## Opponent pitcher signals\n"
            + get_pitcher_trend_signals("opponent").to_csv(index=False),
        "## FA batter signals\n"
            + get_batter_trend_signals("fa").to_csv(index=False),
        "## FA pitcher signals\n"
            + get_pitcher_trend_signals("fa").to_csv(index=False),
    ])

    matchup_context = "\n\n".join([
        "## Current matchup scores\n"
            + get_current_matchup_scores().to_csv(index=False),
        "## Opponent category profile\n"
            + get_opponent_category_profile().to_csv(index=False),
        "## League benchmarks\n"
            + get_league_benchmarks().to_csv(index=False),
        "## Category priority\n"
            + get_category_priority().to_csv(index=False),
        "## Opponent roster\n"
            + get_opponent_roster_list().to_csv(index=False),
    ])

    future_context = "\n\n".join([
        "## Probable starters\n" + get_probable_starters().to_csv(index=False),
        "## Schedule\n" + get_schedule().to_csv(index=False),
        "## Park factors\n" + get_park_factors().to_csv(index=False),
        "## FA pitcher starts\n" + get_fa_pitcher_starts().to_csv(index=False),
    ])

    rm_json_path = f"data/roster_management_raw_{today}.json"

    print("[phase b] running parallel agents")
    await asyncio.gather(
        run_agent(client,
            skill_file="Trend Analyzer Skill .md",
            context=trend_context,
            output_path=f"data/trend_analyzer_roster_{today}.csv"),
        run_agent(client,
            skill_file="Matchup Skill.md",
            context=matchup_context,
            output_path=f"data/output_matchup_sunday_{today}.csv"),
        run_agent(client,
            skill_file="Roster Management.md",
            context=build_roster_mgmt_context(),
            output_path=rm_json_path),
        run_agent(client,
            skill_file="Future Predictor.md",
            context=future_context,
            output_path=f"data/future_predictor_{today}.csv"),
    )

    print("[phase b] splitting Roster Management output into CSVs")
    split_roster_management_output(rm_json_path, today)

    print("[phase b] running GM agent")
    gm_context = "\n\n".join([
        "## Matchup output\n"
            + Path(f"data/output_matchup_sunday_{today}.csv").read_text(),
        "## Trend analyzer output\n"
            + Path(f"data/trend_analyzer_roster_{today}.csv").read_text(),
        "## Roster batter output\n"
            + Path(f"data/roster_management_batter_output_{today}.csv").read_text(),
        "## Roster pitcher output\n"
            + Path(f"data/roster_management_pitcher_output_{today}.csv").read_text(),
        "## FA batter output\n"
            + Path(f"data/roster_management_batter_fa_output_{today}.csv").read_text(),
        "## FA pitcher output\n"
            + Path(f"data/roster_management_pitcher_fa_output_{today}.csv").read_text(),
        "## Future predictor output\n"
            + Path(f"data/future_predictor_{today}.csv").read_text(),
    ])
    await run_agent(client,
        skill_file="GM Skill.md",
        context=gm_context,
        output_path=f"decisions_{today}_full.json")

    print(f"[phase b] done — see decisions_{today}_full.json")


# ---------------------------------------------------------------------------
# Midweek: pull data + run agents (no eval phase)
# ---------------------------------------------------------------------------

async def run_midweek_agents(today: str, week: int):
    print("[midweek] running Rust binary --mode midweek")
    subprocess.run(
        ["./target/release/fantasy_ingest", "--mode", "midweek"],
        check=True,
    )

    client = AsyncAnthropic()

    trend_context = "\n\n".join([
        "## Roster batter signals\n"
            + get_batter_trend_signals("roster").to_csv(index=False),
        "## Roster pitcher signals\n"
            + get_pitcher_trend_signals("roster").to_csv(index=False),
        "## Opponent batter signals\n"
            + get_batter_trend_signals("opponent").to_csv(index=False),
        "## Opponent pitcher signals\n"
            + get_pitcher_trend_signals("opponent").to_csv(index=False),
        "## FA batter signals\n"
            + get_batter_trend_signals("fa").to_csv(index=False),
        "## FA pitcher signals\n"
            + get_pitcher_trend_signals("fa").to_csv(index=False),
    ])

    prior_sunday = max(
        DATA_DIR.glob("output_matchup_sunday_*.csv"),
        key=lambda p: p.name,
        default=None,
    )
    matchup_context = "\n\n".join([
        "## Current matchup scores (midweek — counting stats from CSV)\n"
            + get_current_matchup_scores(week=week).to_csv(index=False),
        "## Opponent category profile\n"
            + get_opponent_category_profile().to_csv(index=False),
        "## Category priority\n"
            + get_category_priority().to_csv(index=False),
        "## Prior Sunday matchup output\n"
            + (prior_sunday.read_text() if prior_sunday else "not available"),
    ])

    future_context = "\n\n".join([
        "## Probable starters\n" + get_probable_starters().to_csv(index=False),
        "## Schedule\n" + get_schedule().to_csv(index=False),
        "## Park factors\n" + get_park_factors().to_csv(index=False),
        "## FA pitcher starts\n" + get_fa_pitcher_starts().to_csv(index=False),
    ])

    rm_json_path = f"data/roster_management_raw_{today}.json"

    print("[midweek] running parallel agents")
    await asyncio.gather(
        run_agent(client,
            skill_file="Trend Analyzer Skill .md",
            context=trend_context,
            output_path=f"data/trend_analyzer_roster_{today}.csv"),
        run_agent(client,
            skill_file="Matchup Skill.md",
            context=matchup_context,
            output_path=f"data/output_matchup_wednesday_{today}.csv"),
        run_agent(client,
            skill_file="Roster Management.md",
            context=build_roster_mgmt_context(),
            output_path=rm_json_path),
        run_agent(client,
            skill_file="Future Predictor.md",
            context=future_context,
            output_path=f"data/future_predictor_{today}.csv"),
    )

    print("[midweek] splitting Roster Management output into CSVs")
    split_roster_management_output(rm_json_path, today)

    print("[midweek] running GM agent")
    gm_context = "\n\n".join([
        "## Matchup output (wednesday)\n"
            + Path(f"data/output_matchup_wednesday_{today}.csv").read_text(),
        "## Trend analyzer output\n"
            + Path(f"data/trend_analyzer_roster_{today}.csv").read_text(),
        "## Roster batter output\n"
            + Path(f"data/roster_management_batter_output_{today}.csv").read_text(),
        "## Roster pitcher output\n"
            + Path(f"data/roster_management_pitcher_output_{today}.csv").read_text(),
        "## FA batter output\n"
            + Path(f"data/roster_management_batter_fa_output_{today}.csv").read_text(),
        "## FA pitcher output\n"
            + Path(f"data/roster_management_pitcher_fa_output_{today}.csv").read_text(),
        "## Future predictor output\n"
            + Path(f"data/future_predictor_{today}.csv").read_text(),
    ])
    await run_agent(client,
        skill_file="GM Skill.md",
        context=gm_context,
        output_path=f"decisions_{today}_midweek.json")

    print(f"[midweek] done — see decisions_{today}_midweek.json")


# ---------------------------------------------------------------------------
# Adhoc: pull FA data + GM injury replacement recommendations
# ---------------------------------------------------------------------------

async def run_adhoc(today: str, position: "str | None"):
    print("[adhoc] running Rust binary --mode adhoc")
    rust_cmd = ["./target/release/fantasy_ingest", "--mode", "adhoc"]
    if position:
        rust_cmd += ["--position", position]
    subprocess.run(rust_cmd, check=True)

    client = AsyncAnthropic()

    fa_positions = get_fa_positions()
    fa_positions_text = "\n".join(
        f"{pid}: {', '.join(positions)}"
        for pid, positions in fa_positions.items()
    )

    adhoc_context = "\n\n".join([
        f"## Position needed: {position or 'any'}",
        "## FA batter contribution (14d)\n"
            + get_batter_contribution_scores_14d("fa").to_csv(index=False),
        "## FA pitcher contribution (14d)\n"
            + get_pitcher_contribution_scores_14d("fa").to_csv(index=False),
        "## FA eligible positions\n" + fa_positions_text,
    ])

    print("[adhoc] running GM adhoc agent")
    await run_agent(client,
        skill_file="GM Skill.md",
        context=adhoc_context,
        output_path=f"decisions_adhoc_{today}.json")

    print(f"[adhoc] done — see decisions_adhoc_{today}.json")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fantasy Baseball GM orchestrator")
    parser.add_argument(
        "--mode", choices=["full", "midweek", "adhoc"], required=True,
    )
    parser.add_argument(
        "--phase", choices=["a", "b"], default=None,
        help="full mode only: 'a' = pull data + evals, 'b' = run agents",
    )
    parser.add_argument(
        "--week", type=int, default=None,
        help="current league week number (required for midweek)",
    )
    parser.add_argument(
        "--position", type=str, default=None,
        help="adhoc mode: position slot to fill (e.g. OF, SS, P)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    today = date.today().strftime("%Y%m%d")

    if args.mode == "full":
        if args.phase is None:
            print("Error: --mode full requires --phase a or --phase b")
            sys.exit(1)
        if args.phase == "a":
            run_phase_a()
        else:
            asyncio.run(run_full_agents(today))

    elif args.mode == "midweek":
        if args.week is None:
            print("Error: --mode midweek requires --week <number>")
            sys.exit(1)
        asyncio.run(run_midweek_agents(today, args.week))

    elif args.mode == "adhoc":
        asyncio.run(run_adhoc(today, args.position))


if __name__ == "__main__":
    main()
