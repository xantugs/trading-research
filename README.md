# ORB Backtest

A reproducible Opening Range Breakout research project with a MetaTrader 5 EA,
a Python bar simulator, Dukascopy and MT5 data tools, trade reconciliation, and
a Streamlit interface for someone who does not code.

**Project version: 1.3.0.** The EA compiles locally with zero errors and warnings.
Python scenarios and the app are tested. Broker smoke testing and real-data
reconciliation are still required. This repository does not establish a trading edge.

## Start with one command

Install **Python 3.11–3.13**, open a terminal in this folder, then run:

```sh
python run_all.py
```

The launcher creates a local `.venv`, installs the project, generates a seeded
120-day synthetic demo, runs the backtest, writes a report bundle under
`outputs/`, and serves the app at [localhost:8501](http://localhost:8501).
Open that address; the first result loads automatically. Ctrl+C stops it.
First setup needs internet; installed demo runs need no market-data connection.
The launcher works from another working directory when invoked by full path.

| Task | Command |
| --- | --- |
| Test the whole Python project and make a demo report | `python run_all.py --check --no-app` |
| Compile the EA too (Windows with MT5 installed) | `python run_all.py --check --compile-mt5 --no-app` |
| Use an existing Python environment without installs | `python run_all.py --no-install --no-app` |
| Change settings | `python run_all.py --set tp_ratio=2 --set risk_pct=1 --no-app` |
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
  entry, initial stop, target and exit markers.
- **Compare:** name and save up to eight runs, then overlay normalized returns
  by calendar time or closed-trade number and compare their assumptions.
- **Diagnostics:** inspect opening-range volume, daily flags and exact settings.

Explorer filters leave the full-run metrics unchanged. **Save run** and
**Export settings** capture the last executed setup. Saved runs stay in the
current browser session; export the comparison and settings before closing it.
Preset changes preserve contract, cost and risk inputs, but reset strategy
filters, management and the end-of-day setting.

## Dukascopy now

Start with one month, then extend the same command:

```sh
python run_all.py --source dukascopy --symbol USATECHIDXUSD --from 2025-01-01 --to 2025-02-01 --no-app
```

For a longer research dataset:

```sh
python run_all.py --source dukascopy --symbol USATECHIDXUSD --from 2022-01-01 --to 2026-09-01 --no-app
```

Omit `--no-app` to open the prepared dataset in the app. Downloads use four
workers, retry transient failures, and cache successful compressed artifacts in
`.cache/dukascopy/`. Re-running can resume through that cache. No account is needed.
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

**Verification status:** five historical days were requested in this session,
but the Dukascopy host timed out from this machine. Binary decoding, caching,
retry behavior, end-exclusive dates and DST conversion are covered by offline
tests. A successful live download remains to be checked; failures never fall
back to synthetic prices. Keep TLS verification enabled.

## Settings and outputs

Edit [configs/baseline.json](configs/baseline.json), pass `--config FILE`, or use
repeated `--set key=value` arguments. Booleans are `true/false`; an optional
close time can be disabled with `--set eod_close=null`.

The supplied research config uses 16:30 / 19:00, 1.5R target, range-multiple stop,
2% equity sizing, a **1.0 price-unit constant spread assumption**, and EOD at
22:45. The EA's inherited default has EOD **off**; enable `InpCloseEod` when
comparing to this config. Contract value, lot rules and costs are placeholders
until set from the instrument/account you are studying.

Each result folder contains:

- `report.html`: self-contained interactive balance and drawdown charts, stats,
  diagnostics and a trade preview; it opens without a CDN.
- `trades.csv`, `days.csv`, `balance.csv` and grouped summaries.
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
minute, entry/SL/TP/exit prices, exit reason and R. `--strict` returns exit code
2 for mismatches. EA v1.30 headers include ATR, volume, contract and initial
balance settings; old v1.20 logs may require `--set` overrides. Per-tick
management should be disabled for the first comparison. Exit time is retained
for inspection but is not a strict check, because the Python time is a bar open.

For an integrated local-data run add `--ea-log PATH` to
`run_all.py --source csv --bars ... --no-app`.

## What the engine implements

Opening range = one completed signal candle. A close-confirmed breakout enters
at the next signal candle's open when the prior close crossed the range.
Immediate mode uses the first M1 crossing. Each direction has one attempt per
strategy-clock day; a trend veto consumes the attempt.

For range width W, long SL = high − W × SL ratio; short SL = low + W × SL ratio.
A ratio of 1 puts the stop on the far edge. TP = entry ± initial risk × TP ratio.
The engine also supports daily ATR and volume filters, higher-timeframe EMA/SMA,
EOD close, breakeven and trailing. [Exact model and limitations](docs/MODEL.md).

## Project layout

```text
orb/                    engine, validation, clocks, configuration, reporting
mt5/                    EA source (compiled files stay local)
configs/                versioned experiment defaults
tests/                  engine, provider, report, CLI and app scenarios
scripts/compile_mt5.ps1  repeatable MetaEditor compilation
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

WRDS is a planned provider after account approval and dataset entitlements;
nothing connects to WRDS yet. [Roadmap](docs/ROADMAP.md) ·
[Changelog](CHANGELOG.md) · [Provenance](docs/PROVENANCE.md).
No open-source license has been selected; provider data rights are separate.
