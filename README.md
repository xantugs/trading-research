# ORB Backtest for MetaTrader 5

This project converts licensed WRDS NYSE TAQ complete-NBBO quotes for **QQQ**
into verified MetaTrader 5 custom-symbol ticks, then runs the Opening Range
Breakout strategy in MT5. Python is used only to clean and verify data in the
active workflow; MT5 is the strategy backtester.

**Project version 1.7.0 · EA v1.50.** Both MQL5 programs compile locally with
zero errors and zero warnings. A real WRDS day has passed conversion and
independent file verification. Running the importer and tester inside MT5 is
the remaining manual smoke test. This repository does not establish a trading
edge.

## One-command setup

Requirements: Windows, Python 3.11–3.13, MetaTrader 5, and the local WRDS tree
at `C:\data\wrds_ticks`.

Open PowerShell in this folder and prepare one day first:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_mt5_wrds.ps1 `
  -From 2018-01-02 -To 2018-01-03
```

The command creates `.venv` if needed, installs the Parquet dependency,
compiles both MQL5 programs, installs the binaries in the detected MT5 data
folder, converts every available source record in the requested New York date
window, writes the daily files to MT5 `Common\Files`, and verifies their schema,
ordering, size and SHA-256 hashes. `-To` is exclusive.

For a month, use an end-exclusive range:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup_mt5_wrds.ps1 `
  -From 2018-01-01 -To 2018-02-01
```

Existing output is protected. Add `-Overwrite` only when intentionally rebuilding
the same days. Long ranges are accepted by the setup command, but monthly batches
are easier to inspect and delete after import.

## Import and test in MT5

1. Open MT5 and refresh **Navigator**.
2. Under **Scripts**, run `Import_WRDS_Ticks`.
3. Leave `InpFileName` as one exact daily file for the first test, or enter
   `QQQ_*_ticks.csv` to import every prepared day. The script creates `QQQ.WRDS`.
4. Open **Strategy Tester**, select `ORB_Backtest` and symbol `QQQ.WRDS`.
5. Use **Every tick based on real ticks**, M15, and the imported date range.
6. Keep the EA defaults for the first run: range 09:30, cutoff 12:00 and optional
   EOD time 15:45. These are New York wall times in the custom-symbol history.
7. Enable visual mode and hand-check the opening range, first confirmed breakout,
   entry, initial stop, target and exit on at least ten trades.

The importer processes one daily file at a time and replaces that symbol day,
so rerunning an import does not append duplicate ticks. Once MT5 shows the day
under `QQQ.WRDS`, its generated CSV can be deleted from `Common\Files`; retain
the source Parquet until the MT5 smoke test is complete.

## Data contract

- Source: QQQ complete NBBO quote events in WRDS NYSE TAQ Daily Product.
- Source clock: America/New_York wall time.
- Window: `00:00:00.000 <= timestamp < next-day 00:00:00.000`.
- Output: one tab-delimited MT5 tick CSV and JSON manifest per trading day.
- Tick fields: bid and ask from NBBO; last and volume are zero because this is a
  quote feed.
- Cleaning: invalid and crossed quotes are removed; consecutive records with the
  same bid and ask are removed because they change metadata rather than the
  executable price path.
- Precision: MT5 timestamps are milliseconds. All price changes are retained,
  including multiple changes within one millisecond, in original nanosecond order.
- Custom symbol: one lot equals one QQQ share; two price digits; point and tick
  size `$0.01`; tick value `$0.01`; whole-share volume steps; USD P/L.

The converter applies no session filter. A file may begin near 04:00 and end near
20:00 because QQQ has no NBBO quote records during the rest of that calendar day;
those empty periods are not discarded data. The EA volume-spike warning defaults
to disabled because metadata-only quote repeats are deliberately removed.

TAQ is consolidated top-of-book data. It does not contain full depth, queue
position, broker commissions, routing delay, partial fills or market impact.
MT5 results are gross until costs are added through a tested mechanism.

## Direct commands

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[wrds]"

.venv\Scripts\python scripts\wrds_to_mt5_ticks.py C:\data\wrds_ticks `
  --from 2018-01-02 --to 2018-01-03 `
  --output-dir C:\data\mt5_qqq_ticks

.venv\Scripts\python scripts\verify_mt5_ticks.py C:\data\mt5_qqq_ticks

powershell -NoProfile -ExecutionPolicy Bypass -File scripts\compile_mt5.ps1

.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
```

The converter limits direct commands to 31 calendar days unless
`--allow-large-range` is supplied. It shows a progress bar and refuses to
overwrite existing output unless `--overwrite` is supplied.

## Strategy state

The EA supports observed or forecast opening ranges, multi-candle same-colour
confirmation, the first valid breakout only per day, midpoint or range-multiple
stops, risk-based sizing, range/ATR/trend filters, cutoff and EOD handling,
breakeven, trailing and CSV diagnostics. For the initial data validation, use
the simplest fixed setup and disable optional management and filters; add them
back one at a time after trades match the rules by inspection.

The previous Python engine, Streamlit app, M1 pipeline, Dukascopy utilities and
reconciliation tools remain in the repository as historical research tooling.
They are not part of the active WRDS-to-MT5 workflow and do not supply or alter
the imported custom-symbol ticks.

## Repository layout

```text
mt5/ORB_Backtest.mq5          strategy EA
mt5/Import_WRDS_Ticks.mq5     custom-symbol tick importer
scripts/setup_mt5_wrds.ps1    setup, compile, install, convert and verify
scripts/wrds_to_mt5_ticks.py  WRDS Parquet to MT5 tick conversion
scripts/verify_mt5_ticks.py   streaming output and manifest verification
scripts/compile_mt5.ps1       strict MetaEditor compilation
tests/                        automated Python tests
docs/MT5_WRDS.md              detailed runbook and limitations
```

Market data, generated CSVs, compiled binaries, virtual environments and WRDS
credentials stay outside Git. Source code is MIT licensed; provider data rights
remain separate. See [the MT5/WRDS runbook](docs/MT5_WRDS.md),
[WRDS notes](docs/WRDS.md), [roadmap](docs/ROADMAP.md), and
[changelog](CHANGELOG.md).
