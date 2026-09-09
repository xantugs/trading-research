# Changelog

## 1.5.0 — 2026-09-10

- Added a decision-event ledger, daily-lock and rejected-signal chart markers,
  MFE/MAE trade diagnostics and an exported `events.csv` audit trail.
- Added M1/M5/signal-timeframe trade inspection plus optional WRDS TAQ quote or
  trade tick overlays with basic tick-quality checks.
- Added chronological holdout, trade-sequence bootstrap, annual/regime tables,
  a parameter stability heatmap and expanding yearly target-R walk-forward.
- Brought EA v1.40 to strategy parity for historical/adaptive range forecasts,
  multi-candle colour confirmation, midpoint stops, prior-window range veto and
  lagged MA slope rejection.
- Added EA spread, minimum-stop and margin guards, explicit live-entry gating,
  daily-lock restart persistence, range recovery, and fill-slippage logging.
- Added a canonical rule contract and expanded EA-header reconciliation for all
  new strategy and execution settings.
- Added a QQQ equity profile with recorded TAQ spread, whole-share commission,
  actual-risk diagnostics and a dynamic gross-notional cap.
- Flagged every unresolved same-bar SL/TP touch for later quote replay, and
  preserved TAQ nanoseconds plus the explicit +7-hour overlay conversion.
- Added Parquet transfer verification, yearly-tar runbook and WRDS job scripts
  for the completed 2018–2022 raw QQQ archive.

## 1.4.0 — 2026-09-09

- Added three focused Python range choices: the observed opening candle, a
  prior-session opening-range percentile, and the same forecast adjusted by
  premarket realized volatility. Removed the discarded average-M1 box.
- Added multi-candle and same-colour breakout confirmation, midpoint stops,
  prior-window range-ratio rejection and a lagged normalized EMA/SMA slope veto.
- Changed daily trade state to accept only the first confirmed breakout,
  regardless of direction, in both Python and the MT5 EA.
- Added an Algorithm workspace tab with active-rule explanations, MT5 parity
  checks, translated EA inputs, focused/full MQL5 views and source download.
- Enlarged trade candlestick inspection and added an adjustable before/after
  candle context so entries can be judged against more surrounding price action.
- Exposed the new rules in the dark Streamlit workspace and added a documented
  `configs/discussion.json` research starting point.
- Made the forecast-range multiplier a fine-grained free numeric input and added
  labeled range bounds, construction and width to trade inspection.
- Added WRDS NYSE TAQ daily raw extraction, cloud-side QQQ M1 aggregation,
  legacy-schema detection, SGE yearly jobs and durable progress bars.
- Added local WRDS gzip and directory input to `run_all.py`; yearly inputs are
  merged with overlap rejection and remain outside Git.
- Changed WRDS extraction coverage to 07:30 New York so the proposed two-hour
  pre-range filter has complete history. Added direct gzip loading.
- Kept the experimental switches explicitly Python-only pending EA parity.

## 1.3.1 — 2026-09-08

- Redesigned Streamlit as a dark research workspace with responsive summary
  cards, collapsible advanced controls and Overview/Trades/Compare/Diagnostics.
- Added automatic first-run results and optional automatic recalculation,
  filtered trade exports and individual candlestick trade inspection.
- Added named session snapshots, normalized return overlays and assumption
  comparisons; pending settings cannot relabel or alter a saved result.
- Added UI interaction regression coverage for manual/automatic runs, immutable
  comparisons and empty trade filters. Raised Streamlit minimum to 1.50.
- Made `run_all.py --source dukascopy` a complete 2022-to-latest workflow and
  added a dependency-aware `--data-only` preparation mode.
- Added verified reuse of completed Dukascopy M1/M15/MT5 datasets, per-artifact
  hashes and an explicit `--refresh-data` override. Network failures retain the
  raw cache and include a local CSV fallback command.

## 1.3.0 — 2026-09-08

- Added a standalone Python project, run_all.py, editable installation,
  experiment configuration, compiler helper and GitHub Actions.
- Added source/data hashes, result manifests, strict JSON stats, CSV summaries
  and self-contained HTML reports.
- Reworked Dukascopy downloads with cache/retries, inclusive-start/exclusive-end
  dates including Sundays, strict BI5 decoding and explicit spread assumptions.
- Fixed UTC-to-New-York wall-time shifting across DST; required an explicit
  clock conversion for the MT5 Python API.
- Added strict bar/parameter validation, incomplete opening-range diagnostics,
  a stable empty-trade schema and correct counts of days actually traded.
- Corrected Python ATR to match MetaQuotes' simple ATR and fixed period-one SMA.
- Fixed Streamlit's default trade-log selection, disk-written uploads and stale
  result labeling; exposed contract and filter lookbacks.
- Extended EA headers with missing ATR/volume/contract/balance fields. Version
  1.30 compiles locally with zero errors/warnings.
- Added strict reconciliation failure codes, R tolerance, typed overrides,
  unsupported-timeframe/MA checks and duplicate trade-key rejection.
- Preserved explicit limitations and a WRDS integration roadmap.

## 1.2.0 — supplied baseline

EA, Python twin, 17 engine scenarios, MT5 fetcher, reconciliation, Streamlit
interface and synthetic data. The newer supplied ZIP also included the initial
Dukascopy downloader. Real-broker reconciliation had not been performed.
