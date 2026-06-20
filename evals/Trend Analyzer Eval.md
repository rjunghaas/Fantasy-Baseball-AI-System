# Trend Analyzer Eval

## Purpose

This eval verifies that the Trend Analyzer agent (1) correctly reads pre-computed signals from data_client.py without hallucinating statistics, and (2) correctly applies the decision rules from the skill to assign action_flags, recommendation_strength, and notes. As weekly Parquet snapshots accumulate, a second eval layer measures whether the trend signals predicted actual player performance trajectories.

---

## Eval Types Covered

**Primary (run every week): Retrieval Eval**
Did the agent cite actual Parquet stats vs hallucinated numbers? This is verifiable immediately after every run.

**Secondary (run end of season): Outcome Eval**
Did trend signals predict actual player trajectories? Did `positive_regression` players bounce back? Did `negative_regression` players decline? This requires at least 2–3 weeks of outcomes to score meaningfully.

---

## Inputs

- `data/stats_YYYYMMDD.parquet` — most recent stats file (season and 14d windows)
- `data/rosters_YYYYMMDD.parquet` — most recent roster file (includes `no_drop` flag)
- Agent output: the ranked table produced by the Trend Analyzer at the end of each session

---

## Ground Truth

**Retrieval eval:** Ground truth is the raw Parquet data. For each player row in the agent output, the `key_stat` values must exactly match the corresponding fields in the Parquet file (within floating-point rounding to 3 decimal places).

**Outcome eval:** Ground truth is actual player stats in the following week's Parquet snapshot. A `positive_regression` signal is confirmed if the player's OBP or xwOBA improves week-over-week. A `negative_regression` signal is confirmed if OBP or xwOBA declines. A `cold` signal is confirmed if the slump persists into the next snapshot. A `hot` signal is confirmed if OBP remains elevated or returns to baseline (both are acceptable — "hot" does not mean "due for a crash").

---

## Scoring Method

### Retrieval Eval (per player row)

Score each row in the agent's output table:

| Result | Criteria |
|--------|----------|
| **Pass** | All values in `key_stat` match Parquet source within ±0.001; `trend_signal` matches the CASE logic; `action_flag` matches the decision rule; `no_drop` override applied correctly where applicable |
| **Partial** | Signal is correct but one `key_stat` value is off by more than ±0.001 (rounding error vs hallucination) |
| **Fail** | Signal does not match CASE logic, OR `key_stat` value is wrong by more than ±0.005, OR `action_flag` does not match the decision rule for that signal and pull count, OR insufficient_data override not applied to all-zero 14d rows |

Score = passing rows / total rows

### Outcome Eval (per flagged player, scored the following week)

Only score players with a non-neutral signal.

| Result | Criteria |
|--------|----------|
| **Confirmed** | Signal direction was correct (e.g. positive_regression player's 14d OBP or xwOBA improved in next snapshot) |
| **Inconclusive** | Insufficient data (player injured, not enough PA, only one week elapsed) |
| **Wrong** | Signal direction was incorrect |

Score = confirmed / (confirmed + wrong). Inconclusives excluded from denominator.

---

## Pass/Fail Threshold

**Retrieval eval:** 100% pass rate required. Any hallucinated statistic is a hard failure — this is a data integrity check, not a grading curve.

**Outcome eval:** Target ≥ 60% signal confirmation rate over a rolling 4-week window. This is the expected hit rate for regression-to-mean signals in a half-season sample. Below 50% over 4 weeks is a signal to revisit the threshold values in the decision rules.

---

## Known Limitations

**Retrieval eval:**
- Cannot detect hallucinations in the `notes` field (prose is unconstrained)
- Does not catch cases where the agent skipped a player entirely
- Does not verify ordering of the output table

**Outcome eval:**
- Requires 7–10 days of elapsed time before a signal can be confirmed or denied
- Small sample sizes (1–2 weeks) make calibration noisy
- A `negative_regression` player may still produce stats this week even if the underlying signal is correct — BABIP regresses over 3–4 weeks, not 7 days
- Does not account for injury — if a flagged player gets hurt, the outcome is inconclusive, not a wrong signal

**Structural gaps in current signal rules (flagged during bootstrap run on 2026-06-14):**
- `velocity_14d` is missing from bootstrap data — velocity_drop signal cannot fire until Phase 1 supplies it
- Kyle Harrison (era_14d=10.13 vs era_season=2.72) and Michael King (era_14d=5.79 vs era_season=3.46) have alarming recent ERA blowups but receive `neutral` because their FIP > ERA seasonally (meaning season ERA was already lucky, not that recent ERA is inflated by luck). The current signal rules have no way to flag a pitcher whose recent performance is bad AND whose baseline was already suspect. Consider adding a rule: `era_14d > X.XX AND fip_season > era_season + 0.50 → bench_today` as a "regression confirmed" signal.

---

## How to Run

### Retrieval Eval (every week)

1. After each Trend Analyzer session, save the agent's output table to `evals/eval_results/retrieval_YYYYMMDD.csv` with columns: `player_name, key_stat_reported, key_stat_actual, signal_correct, action_flag_correct, pass_fail`
2. Query `data/stats_YYYYMMDD.parquet` directly via DuckDB to pull the ground-truth values for every player in the output
3. Compare each reported `key_stat` value against the Parquet source
4. Check that `trend_signal` matches the CASE logic given the reported stat values
5. Check that `action_flag` and `recommendation_strength` match the decision rule for that signal and pull count
6. Verify all-zero 14d rows are flagged `insufficient_data`
7. Record pass/fail per row and compute overall score

**DuckDB query to pull ground truth for one player:**
```python
import duckdb, glob
f = sorted(glob.glob("data/stats_*.parquet"))[-1]
con = duckdb.connect()
con.execute(f"""
    SELECT player_id, name, window, babip, xwoba, obp, era, fip, velocity
    FROM read_parquet('{f}')
    WHERE name = 'JJ Bleday'
    ORDER BY window
""").fetchdf()
```

### Outcome Eval (following week)

1. On the following Sunday, note the player's 14d stats in the new Parquet snapshot
2. For `positive_regression` players: was `obp_14d` or `xwoba_14d` higher than the prior snapshot?
3. For `negative_regression` players: was `obp_14d` or `xwoba_14d` lower?
4. For `cold` players: did the cold streak persist (obp_14d still below season - 0.040)?
5. For `era_inflation` pitchers: did `era_14d` come back down toward `era_season`?
6. Record confirmed / inconclusive / wrong in `evals/outcomes_log.csv`

---

## Example (Bootstrap Run — 2026-06-14)

### Retrieval Eval

**Input:** stats_20260614.parquet + rosters_20260614.parquet

**Agent output (selected rows):**

| player_name | trend_signal | action_flag | key_stat_reported | key_stat_actual (Parquet) | pass_fail |
|---|---|---|---|---|---|
| JJ Bleday | positive_regression | hold | babip_14d=0.243, xwoba_season=0.390 | babip(14d)=0.243, xwoba(season)=0.390 | ✅ PASS |
| Konnor Griffin | negative_regression | hold | babip_14d=0.667, xwoba_season=0.303 | babip(14d)=0.667, xwoba(season)=0.303 | ✅ PASS |
| Yoshinobu Yamamoto | era_deflation_risk | hold | era_14d=0.68, era_season=2.68, fip_season=3.43 | era(14d)=0.68, era(season)=2.68, fip(season)=3.43 | ✅ PASS |
| Mason Miller | era_inflation | hold | era_14d=2.45, era_season=0.94, fip_season=0.46 | era(14d)=2.45, era(season)=0.94, fip(season)=0.46 | ✅ PASS |
| Munetaka Murakami | insufficient_data | hold | null | all 14d fields = 0 (IL10) | ✅ PASS |

**Bootstrap retrieval score: 26/26 — 100% PASS**

Signal assignments verified programmatically against CASE logic. No hallucinated statistics detected. `no_drop` override not triggered (no drop_candidates in single-snapshot bootstrap). All-zero 14d rows correctly flagged as insufficient_data.

### Outcome Eval

Pending — requires next weekly snapshot (target: 2026-06-22). Players to track:
- **Confirm positive_regression bouncing back:** JJ Bleday, Josh Naylor
- **Confirm negative_regression declining:** Konnor Griffin, Cedanne Rafaela
- **Confirm cold streaks:** CJ Abrams, Salvador Perez
- **Monitor era_inflation correction:** Mason Miller, Trey Yesavage
- **Monitor era_deflation materializing:** Yoshinobu Yamamoto

---

## Open Items

- [ ] Add `velocity_14d` to bootstrap CSV once Phase 1 data layer is available; re-run to check velocity_drop signal
- [ ] Decide whether to add a "FIP > ERA + high recent ERA" rule to catch Harrison/King scenarios
- [ ] Automate retrieval eval as a Python script in `evals/eval_runner.py`
