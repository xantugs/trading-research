# Changelog

## 1.7.1 — 2026-09-11

- Replaced the full-archive CSV output with a compact importer format: a 32-byte
  daily header followed by 12-byte records containing milliseconds since
  midnight and integer bid/ask price points.
- Kept every executable NBBO price change and original ordering for changes that
  share an MT5 millisecond; discarded nanosecond values that MT5 cannot store.
- Added binary structural/hash verification, a verified CSV migration utility,
  two-worker monthly conversion and deletion only after exact day-set checks.
- Preserved Parquet row order as the documented fallback for the rare distinct
  NBBO states that share a full WRDS timestamp without a quote sequence column,
  and recorded those transitions in each manifest.
- Updated and compiled `Import_WRDS_Ticks.mq5` v1.10 with direct binary support;
  both MQL5 programs compile with zero errors and zero warnings.
- Converted and independently hash-verified all 1,259 days: 632,204,742 ticks in
  7.065 GiB instead of the 22.85 GiB CSV projection, with zero verifier problems.

## 1.7.0 — 2026-09-10

- Removed the Streamlit application, its UI tests, configuration and dependency.
  The repository now opens no web service; MT5 is the only active backtest UI.
- Made MT5 the active strategy backtester and Python a WRDS cleaning and
  verification utility; the older Streamlit and Python execution paths remain
  available only as historical research tools.
- Added direct conversion of QQQ complete-NBBO Parquet to MT5 custom-symbol tick
  CSVs. The converter covers the full `00:00 <= time < next-day 00:00` New York
  source day without a session filter.
- Removed invalid and crossed markets plus consecutive unchanged bid/ask states,
  while preserving every executable price change and source order when multiple
  changes share an MT5 millisecond.
- Added per-day manifests and a streaming verifier for schema, timestamps,
  prices, counts, file size and SHA-256.
- Added `Import_WRDS_Ticks.mq5`, which configures `QQQ.WRDS`, validates and
  replaces one source day at a time, and accepts an exact file or wildcard batch.
- Added a one-command PowerShell setup that bootstraps the WRDS dependency,
  compiles and installs both MQL5 binaries, converts a date interval, and verifies
  all staged files.
- Changed EA defaults to the custom symbol's New York clock: 09:30 range, 12:00
  cutoff and 15:45 EOD. Disabled the old quote-volume diagnostic by default.
- Compiled EA v1.50 and the importer locally with zero errors and zero warnings;
  converted and verified a real 2018-01-02 WRDS sample.

## 1.6.0 — 2026-09-10

- Added strict, non-overlapping `m1` and `tick` runner modes. M1 reads only the
  external WRDS M1 root; tick reads only the raw WRDS tree and derives its own
  M1 and signal candles.
- Fixed the quote decode for the dtype WRDS Parquet actually produces. A Postgres
  `time` becomes Arrow **time64**, which pandas surfaces as *object* dtype holding
  `datetime.time` - not timedelta64. The typed shortcut now covers timedelta64,
  Arrow time32/time64 and pandas ArrowDtype, verified by a Parquet round-trip test
  per type; text still takes the ASCII path. This also halves peak memory on a
  large day (780 MiB to 397 MiB on 7.3M quotes), which is what killed the 2021
  replay with `Unable to allocate ... shape (7299503,)`.
- Sped the NBBO quote decode up roughly **9x** (about 9,550 ms per day to 1,085
  on a synthetic 900k-row day). `time_m` arrives from Parquet as timedelta64, and
  the ASCII fast parser was rendering it to a string per row first - a per-element
  Python formatter that cost around twenty times the parse it fed. Typed columns
  now go straight from nanoseconds to timestamps; the ASCII path is unchanged for
  string sources, and a test asserts the two agree row for row. A prefetch thread
  pool was tried and removed: 1.11x at four workers, slower at eight.
- Made an execution the quote data cannot support **recorded rather than fatal**.
  A fixed 15:45 `eod_close` lands hours after the 13:00 bell on the ~15 half-days
  in 2018-2022, where the next NBBO update can be over a minute away; that used to
  abort the entire replay partway through. Such signals are now skipped, counted
  by failure code with their dates in `quality.tick_execution.unreplayable`, and
  summarised on stdout. `--tick-strict` restores fail-fast for debugging. Nothing
  is ever filled at a stale quote either way.
- Widened the default M1 window to **04:00-20:00 New York**, every minute US
  equities quote, instead of 07:30-16:01. Pre-market and after-hours events are
  now kept rather than discarded at build time. `--session-start` /
  `--session-end` and `ORB_SESSION_START` / `ORB_SESSION_END` override it, both
  builders share the same defaults, and `wrds_submit_years.sh` passes the window
  explicitly so the WRDS Cloud export and the local rebuild cannot drift.
  **M1 years exported under an older window must be rebuilt**: an 08:30 export
  carries roughly 60 fewer bars per day than a 07:30 one.
- Kept `python run_all.py` with no arguments on the fast M1 mode so it cannot
  unexpectedly begin a multi-hour tick replay. `--mode tick` and `--mode both`
  remain explicit historical-research choices with separate input roots.
- Defaulted M1 mode to `[2018-01-01, 2023-01-01)`, the span the raw tick archive
  covers, so an M1 run and a tick run of the same settings describe the same
  trading days. `--from`/`--to`, `--full-history` and the `ORB_WRDS_M1_START` /
  `ORB_WRDS_M1_END` environment variables override it. Tick mode stays governed
  by its own root so a wider archive is picked up automatically.
- Added a parity-tested local raw-event builder matching the WRDS Cloud M1
  definition for best-bid OHLC, eligible trade/share volume, quote counts and
  p50/p95 spread statistics.
- Added ordered complete-NBBO execution for close-confirmed entries and fixed
  stop, target, and EOD exits, with executable-side fills and strict quote-delay
  checks.
- Recorded the local QQQ inventory: M1 for 2012–2024 and 2,518 raw Parquet files
  covering 1,259 trading days in 2018–2022.
- Excluded five incomplete opening-range dates without filling prices. Three are
  2020 market-wide circuit-breaker halts; the other two are documented valid-NBBO
  source gaps.
- Made QQQ/WRDS the primary research workflow while retaining CFD and synthetic
  inputs as explicit auxiliary tools.

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
