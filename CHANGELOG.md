# Changelog

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
