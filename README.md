# ORB Backtest

A reproducible Opening Range Breakout research project with a MetaTrader 5 EA,
a Python bar simulator, Dukascopy, WRDS TAQ and MT5 data tools, trade reconciliation, and
a Streamlit interface for someone who does not code.

**Project version: 1.5.0.** EA v1.40 compiles locally with zero errors and warnings.
Python scenarios and the app are tested. Broker smoke testing and real-data
reconciliation are still required. This repository does not establish a trading edge.

## Start with one command

Install **Python 3.11–3.13**, open a terminal in this folder, then run:

```sh
python run_all.py
```

The launcher creates a local `.venv`, installs the project, uses
`data/USTEC_M1.csv` when that local file exists, runs the backtest, writes a
report bundle under `outputs/`, and serves the app at
[localhost:8501](http://localhost:8501). A clean checkout without local USTEC
data falls back to a seeded 120-day synthetic software demo.
Open that address; the first result loads automatically. Ctrl+C stops it.
First setup needs internet; installed demo runs need no market-data connection.
The launcher works from another working directory when invoked by full path.

| Task | Command |
| --- | --- |
| Run the synthetic software demo explicitly | `python run_all.py --source demo` |
| Test the whole Python project and make a demo report | `python run_all.py --check --no-app` |
| Compile the EA too (Windows with MT5 installed) | `python run_all.py --check --compile-mt5 --no-app` |
| Use an existing Python environment without installs | `python run_all.py --no-install --no-app` |
| Change settings | `python run_all.py --set tp_ratio=2 --set risk_pct=1 --no-app` |
| Prepare the full Dukascopy dataset only | `python run_all.py --source dukascopy --data-only` |
| Prepare Dukascopy data, backtest and open the app | `python run_all.py --source dukascopy` |
| Merge downloaded WRDS years and run | `python run_all.py --source wrds --bars data/wrds/years --no-app` |
| Run local M1 bars already in the strategy clock | `python run_all.py --source csv --bars data/NAS100_M1.csv --no-app` |
| Start only the app after setup | `.venv/Scripts/python -m streamlit run app.py` (Windows) |

On macOS/Linux use `.venv/bin/python` for direct commands. No Node.js is needed.
`--output PATH` selects a **new** result directory. Existing directories are
refused so an experiment cannot overwrite an earlier result.

## Explore in ORB Lab

The app opens in dark mode, with common controls in the sidebar and advanced
timing, filters, stop management and contract settings in collapsible panels.
Change a setup and press **Run backtest**, or enable **Auto-run changes** for
automatic recalculation. Pending changes are labeled until executed.

- **Overview:** switch the performance chart between balance, percentage return
  and gross R; drag to zoom and explore monthly and grouped results.
- **Trades:** filter by side, exit reason and entry date, export the filtered
  table, then inspect an individual trade on a candlestick chart with range,
  entry, initial stop, target, exit and rejected-signal markers. Switch among
  M1/M5/signal candles, read the decision timeline, and optionally overlay one
  WRDS quote or trade tick file.
- **Validation:** compare chronological development/holdout samples, bootstrap
  trade sequences, inspect years and volatility regimes, run an expanding
  yearly target-R walk-forward, and test a local target/stop parameter grid.
- **Compare:** name and save up to eight runs, then overlay normalized returns
  by calendar time or closed-trade number and compare their assumptions.
- **Algorithm:** read the active rules beside the real MQL5 source, inspect the
  matching MT5 inputs, see any parity gaps, and download the compilable EA.
- **Data quality:** inspect timestamp gaps, estimated missing rows, invalid OHLC,
  daily session coverage, range-candle completeness and recorded spread.
- **Diagnostics:** inspect opening-range volume, daily flags and exact settings.

Explorer filters leave the full-run metrics unchanged. **Save run** and
**Export settings** capture the last executed setup. Saved runs stay in the
current browser session; export the comparison and settings before closing it.
Preset changes preserve contract, cost and risk inputs, but reset strategy
filters, management and the end-of-day setting.

## QQQ on WRDS TAQ

QQQ is the target research instrument once the compact WRDS years are local.
Keep the licensed files outside Git and run:

```sh
python run_all.py --source wrds --bars data/wrds/years --no-app
```

WRDS mode automatically selects [configs/qqq.json](configs/qqq.json). The
profile uses recorded M1 NBBO spread, whole shares, $1 P/L per $1 share move,
a declared $0.005/share/side commission assumption, and a 1× gross-notional
cap. Change commission and leverage to match the eventual broker. The recorded
spread is a conservative full-minute p95 proxy; raw quote replay remains needed
for exact fills and SL/TP ordering.

## Dukascopy now

The real-data command needs no dates:

```sh
python run_all.py --source dukascopy
```

It installs the same isolated dependencies as the demo, downloads
`USATECHIDXUSD` M1 bid candles from **2022-01-01 through the latest completed
UTC day**, validates the bars, runs the backtest and opens the app. Use
`--data-only` to stop once the files are ready. Override the research window
when a smaller first download is useful:

```sh
python run_all.py --source dukascopy --from 2025-01-01 --to 2025-02-01 --data-only
```

Downloads use four workers, retry transient failures, and cache successful
compressed artifacts in `.cache/dukascopy/`. Re-running resumes through that
cache. A completed dataset is reused only after its M1, M15 and MT5-file hashes
match the metadata; `--refresh-data` forces a rebuild. No account is needed.
The source is Dukascopy's **US 100 Tech Index CFD**, not NQ futures or an exchange
index tape. [Dukascopy instrument list](https://www.dukascopy.com/europe/english/cfd/range-of-markets/).

Dates are **UTC, start inclusive and end exclusive**. The default clock,
`--tz ny+7`, means New York wall time plus seven hours: 09:30 NY becomes 16:30
in winter and summer. It is a research clock convention, not a claim about your
broker. `--tz utc` and fixed offsets such as `--tz +3` are available; change
strategy times accordingly. See [data and clock details](docs/DATA.md).

The downloader writes M1, aggregated M15, an MT5 custom-symbol import file, and
a metadata JSON. `--ticks` also archives hourly tick files; **the Python engine
still resolves the path on M1**, so this flag alone does not create a tick backtest.

**Verification status:** a two-day real download and direct endpoint checks were
attempted in this session. DNS resolves, but this network cannot establish a
TCP/443 connection to Dukascopy's data host; the official web export routes also
timed out. Binary decoding, cache resume, completed-dataset hash verification,
end-exclusive dates and DST conversion are covered by offline tests. A successful
live download remains to be checked. Failures preserve cached BI5 files and never
fall back to synthetic prices. Try another network, or export M1 CSV from
Dukascopy and pass it with `--source csv --bars FILE`. Keep TLS verification enabled.

## Settings and outputs

Edit [configs/baseline.json](configs/baseline.json), pass `--config FILE`, or use
repeated `--set key=value` arguments. Booleans are `true/false`; an optional
close time can be disabled with `--set eod_close=null`.

`configs/discussion.json` captures the current research hypothesis: the median
normalized opening range from the prior 20 sessions, three same-colour
confirmation candles, midpoint stop, one-quarter/two-hour range veto and a
lagged H1 EMA slope threshold. Treat every number as a test parameter. The
range multiplier accepts any positive decimal; compare chosen values in
separately saved runs.

The supplied research config uses 16:30 / 19:00, 1.5R target, range-multiple stop,
2% equity sizing, a **1.0 price-unit constant spread assumption**, and EOD at
22:45. The EA's inherited default has EOD **off**; enable `InpCloseEod` when
comparing to this config. Contract value, lot rules and costs are placeholders
until set from the instrument/account you are studying.

Each result folder contains:

- `report.html`: self-contained interactive balance and drawdown charts, stats,
  diagnostics and a trade preview; it opens without a CDN.
- `trades.csv`, `events.csv`, `days.csv`, `balance.csv` and grouped summaries.
- `params.json`, `stats.json`, `data_quality.json`.
- `manifest.json`: source and code hashes, package versions, Git revision and
  dirty status, clock, execution assumptions and limitations.

R statistics are before commission; money profit includes per-side commission.
The balance curve and its drawdown show closed trades, not intratrade equity.
Undefined/infinite JSON statistics are written as `null`.

## MetaTrader and reconciliation

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/compile_mt5.ps1
```

The compiler helper finds the installed standard library and writes
`mt5/ORB_Backtest.ex5` plus `mt5/compile.log`. Optional `-MetaEditor` and
`-Mql5Root` arguments support other installations. It compiles only.

Follow the [MT5 smoke-test checklist](docs/MT5.md) to install in the tester and
hand-check ten trades. For broker data, install the optional terminal bridge:

```sh
.venv/Scripts/python -m pip install -e ".[mt5]"
.venv/Scripts/python fetch_mt5.py --symbol NAS100 --from 2025-01-01 --to 2025-02-01 --tf M1 M15 --tz ny+7
.venv/Scripts/python reconcile.py --ea-log path/to/ORB_trades.csv --bars data/NAS100_M15.csv --m1 data/NAS100_M1.csv --spread 1.0 --strict --report outputs/reconcile.csv
```

Use the **same instrument, clock, dates and broker data** on both sides.
Reconciliation matches date/direction, rejects duplicate keys, and checks entry
minute, entry/SL/TP/exit prices, exit reason and R. EA logs also retain the
requested entry, actual fill and signed slippage in points. `--strict` returns
exit code 2 for mismatches. EA v1.40 headers include range, confirmation, stop,
trend, guard, ATR, volume, contract and initial-balance settings. Old logs may
require `--set` overrides. Per-tick
management should be disabled for the first comparison. Exit time is retained
for inspection but is not a strict check, because the Python time is a bar open.

For an integrated local-data run add `--ea-log PATH` to
`run_all.py --source csv --bars ... --no-app`.

## What the engine implements

The range can be the completed opening candle, a forecast based on a percentile
of prior sessions' matching normalized opening ranges, or that same forecast
adjusted by current premarket realized volatility. Forecast boxes are centred
on today's opening price. A close-confirmed breakout can require several
consecutive closes beyond the box and matching candle colour, then enters at
the next signal candle's open.
Immediate mode uses the first M1 crossing. The first confirmed breakout uses
the strategy-clock day's only attempt, regardless of direction; a trend veto
also consumes that attempt.

Stops can use a range multiple or the H/L midpoint. The engine also supports a
full-window range proportion veto, daily ATR and volume filters, higher-timeframe
EMA/SMA with a normalized slope veto, EOD close, breakeven and trailing. EA
v1.40 implements the same selected strategy rules and adds live spread,
stop-distance, margin and restart guards. [Canonical rules](docs/RULES.md) ·
[Exact model and limitations](docs/MODEL.md).

## Project layout

```text
orb/                    engine, validation, clocks, configuration, reporting
mt5/                    EA source (compiled files stay local)
configs/                versioned experiment defaults
tests/                  engine, provider, report, CLI and app scenarios
scripts/compile_mt5.ps1  repeatable MetaEditor compilation
scripts/wrds_*.py/.sh    WRDS raw and cloud M1 extraction
fetch_dukascopy.py       cached public-feed download
fetch_mt5.py             optional Windows terminal bridge
reconcile.py             EA/Python comparison
run_all.py               setup and experiment pipeline
app.py                   Streamlit interface
docs/                    data, model, MT5, roadmap, provenance and GitHub notes
```

Market data, reports, caches, virtual environments, credentials and compiled
EAs are Git-ignored. Synthetic data is regenerated from a seed rather than
committed. A normal source checkout is small.

## Development and GitHub

```sh
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[app,dev]"
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m ruff check .
.venv/Scripts/python -m build
```

GitHub Actions runs lint, tests and a demo pipeline on Windows/Linux and Python
3.11/3.13, with a separate package build. Network market downloads and MT5
terminal tests are deliberately manual. [Publishing instructions](docs/GITHUB.md).

WRDS Cloud extraction is documented in [the WRDS workflow](docs/WRDS.md).
[Roadmap](docs/ROADMAP.md) ·
[Changelog](CHANGELOG.md) · [Provenance](docs/PROVENANCE.md).
Source code is MIT-licensed; provider market-data rights remain separate.
