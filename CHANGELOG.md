# Changelog

## 1.4.0 — 2026-09-09

- Added Python experiment switches for average-M1 range boxes, multi-candle and
  same-colour breakout confirmation, midpoint stops, prior-window range-ratio
  rejection and a lagged normalized EMA/SMA slope veto.
- Exposed the new rules in the dark Streamlit workspace and added a documented
  `configs/discussion.json` research starting point.
- Made the average-range multiplier a fine-grained free numeric input and added
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
