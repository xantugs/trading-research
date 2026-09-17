# Opening Range Breakout research

A reproducible research project for a simple Opening Range Breakout strategy,
initially on XAUUSD. The goal is to test whether an edge survives costs,
chronological out-of-sample periods, and parameter changes. There is **no
claimed profitable strategy** in this repository.

## Current status

The Python M5-signal/M1-execution baseline is implemented and passes synthetic
behavior checks. The active data source is **FTMO-Demo XAUUSD**. We verified
105 monthly M1 files (3,053,966 bars) from January 2018 through 16 September
2026, plus 200,890,543 native bid/ask ticks from October 2021 onward. One M1
trading day, 1 December 2021, has no broker ticks; it is labelled as a gap.
Every stored file passed read-back validation. No broker-net strategy result is claimed.
The 2025-and-later final holdout is not read by the baseline.

## First run

On Windows with Python 3.11–3.13 and MetaTrader 5 installed:

```powershell
git clone https://github.com/xantugs/orb-backtest.git
cd orb-backtest
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[research-v2,dev]"
```

Log into the intended broker account in MT5, then:

```powershell
python run_all.py broker-probe --symbol XAUUSD --start-year 2021
python run_all.py ftmo-data --with-ticks
python main.py --config configs/xauusd_orb_research.json
```

`ftmo-data` installs the research dependencies, then downloads and verifies
each M1 month; completed months are reused on the next run. `--with-ticks`
also archives the full native bid/ask tick stream by complete UTC day, marking
broker-history gaps instead of inventing quotes. The downloader caches
development and holdout M1 months under
`C:\data\ftmo_xauusd_m1` (outside OneDrive). The one-command research runner
reads **only development-period months**, then writes trades, monthly/yearly
returns, metrics, provenance,
diagnostics, a Markdown report and HTML charts under `outputs/`. It never
places orders. Read the [research workflow](docs/RESEARCH_V2.md) before
interpreting results and the [FTMO data-quality inventory](docs/FTMO_DATA_QUALITY.md)
before using ticks for execution checks.

The starter configuration tests one New York 09:30 session: 30-minute observed
range, M5 candle-close confirmation, first breakout only, next-M1-open entry,
opposite-range stop, 2R target, and 0.5% equity risk. M1 exits use recorded
spread when present, configured slippage, and conservative stop-first ordering
for bars that touch both exit levels. Commission is currently **uncalibrated**;
do not present initial results as broker-net performance. Several 2021 M1 months
have high rates of zero recorded spread; fallback costs there are assumptions.
FTMO's exact Gold
commission and the slippage assumption must be checked against the account's
contract terms and trade history before interpreting returns.

After reviewing the baseline, optional analysis is available with:

```powershell
python main.py --config configs/xauusd_orb_research.json --post-baseline-analysis
```

That produces trade-sequence Monte Carlo, confidence intervals, cost
sensitivity and a configurable illustrative prop-rule replay. It does not
optimize parameters or open the final holdout. The research brief requires a
validated baseline before any optimizer or walk-forward selection is run.

## Checks

```powershell
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m pytest -q
```

## Earlier MT5 and WRDS work

The prior EA and WRDS/QQQ importer remain in `mt5/` and `scripts/`. They are
separate from XAUUSD research. See the [archived MT5/WRDS README](docs/LEGACY_MT5_README.md),
[WRDS runbook](docs/MT5_WRDS.md) and [FTMO workflow](docs/FTMO_WORKFLOW.md).
Market data, account details, generated reports and compiled binaries are not
committed. Source code is MIT licensed; market-data rights remain with their
providers.
