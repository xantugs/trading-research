# XAUUSD ORB research, version 2

This is the active research path. The older MetaTrader EA, WRDS/QQQ importer and
Python twin remain in the repository as historical work. Version 2 starts with
a deliberately plain, independently implemented Python baseline. Its purpose
is to test whether the opening-range rule survives unseen dates and trading
costs; it does not target a specified monthly return.

## Status

The baseline code and synthetic verification are ready. **A real FTMO-Demo
baseline has not been run.** On 17 September 2026, the connected terminal
reported `FTMO Global Markets Ltd / FTMO-Demo` with `XAUUSD`: 100 oz per lot,
0.01 minimum/step, 0.01 price point and USD account currency. Native M1 bars
were downloaded in 105 UTC-month files from 2 January 2018 through 16 September
2026 (3,053,966 bars; 69.2 MB of Parquet). All files passed read-back validation;
24 weekdays without M1 bars are flagged as market closures to review.
FTMO real ticks were archived from 4 October 2021 through 16 September 2026:
200,890,543 bid/ask records in 1,278 nonempty UTC-day files (1.68 GB), all
read back successfully. The tick verifier found one M1 trading day with no
broker ticks, 1 December 2021, which remains an explicit gap. The exact server
is required by the configuration; a different login is rejected.

The 2025-and-later holdout is sealed by the configuration. No data from that
period is loaded into the baseline. The development interval starts in January
2018 and ends before 2025; native tick history starts in October 2021. The
2025-and-later data is stored for final testing but has not been used to choose
strategy settings. The first-day probe is a sample, not a retention guarantee.

## First run

Use Windows, Python 3.11–3.13 and a MetaTrader 5 terminal logged into the
intended FTMO-Demo account. From the repository root:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[research-v2,dev]"
python run_all.py broker-probe --symbol XAUUSD --start-year 2021
python run_all.py ftmo-data --with-ticks
python main.py --config configs/xauusd_orb_research.json
```

The probe prints the company, server, exact symbol and approximate first
real-tick date. Compare it with the account you intend to trade. Edit
`expected_server`, `start`, `development_end`, and `holdout_start` in the JSON
config only after you have confirmed the available history. If multiple MT5
installations are open, pass `--terminal-path` to the intended
`terminal64.exe`. Do not share your login credentials.

The downloader gets M1 bars by month from the exact configured MT5 server,
stores Parquet files under `C:\data\ftmo_xauusd_m1` (outside OneDrive), and
saves source metadata. With `--with-ticks`, it also archives complete UTC
calendar days of native bid/ask quotes under `C:\data\ftmo_xauusd_ticks`.
Repeated timestamps are preserved in their original order. It resumes
completed periods and records any day where M1 exists but the broker serves no
ticks. The verifier reopens every file. The runner stops on
missing months, an obviously incomplete month, wrong broker, wrong contract
size, or a non-Bid chart mode. It never transmits orders.

All MT5 Python rate timestamps are treated as UTC. New York and London session
times use `zoneinfo` (`America/New_York`, `Europe/London`) so daylight-saving
changes are handled by timezone data, not fixed offsets. [MetaTrader documents
UTC rate timestamps](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py)
and [Bid-based OTC bars and Ask-side execution](https://www.metatrader5.com/en/terminal/help/trading_advanced/price_data).

## Baseline rule

The provided configuration tests one session only: New York 09:30, 30-minute
observed range, M5 close-confirmed first breakout, entry at the next M1 open,
opposite-range stop, 2R target, 0.5% equity risk and a 16:00 New York time
exit. No filter, breakeven, trailing, martingale or same-day re-entry is active.
To test London 08:00 or New York 08:20 later, make separate configuration files
and runs; do not combine sessions in the baseline.

The engine reads OHLC from Bid bars. It approximates Ask as Bid plus each M1
bar's recorded spread, or an explicit fallback if the spread is zero. It applies
configured slippage to entry and exit, respects broker lot steps, and resolves
an M1 bar touching both stop and target as a stop. MAE/MFE omit the unknown
intraminute path after exit. These are conservative bar-data assumptions, not
tick-exact execution. Real-tick replay against the same server is required
before claiming high-fidelity execution.

**Cost calibration is still required.** FTMO says to check the instrument's
[specification in the trading platform](https://ftmo.com/en/faq/what-are-the-account-specifications/).
The initial config sets commission to zero as an explicit *uncalibrated placeholder*;
the resulting net return must not be called broker-net performance. Enter the
verified equivalent cost for the intended account before interpreting profit
or prop-rule results. The configured spread fallback and slippage are also
assumptions to be checked against account history. April, May, June, and
September 2021 have more than 5% zero-spread M1 bars (5.6%, 25.9%, 18.8%, and
55.9% respectively). There are no native broker ticks for those months to
recover the historical bid/ask path, so cost-sensitive inferences from them
remain weak.

## Outputs

One command saves under `outputs/xauusd_baseline/`:

- `trades.csv`, `monthly_returns.csv`, `yearly_returns.csv`
- `metrics.json`, `diagnostics.json`, `data_provenance.json`, `config_used.json`
- `summary_report.md`
- equity, drawdown, monthly/yearly return, rolling-expectancy, rolling-Sharpe,
  and MAE/MFE HTML plots

The `--post-baseline-analysis` flag additionally runs seeded 10,000-path
reshuffle and bootstrap Monte Carlo methods and an **illustrative** prop-rule
replay, 95% trade/bootstrap confidence intervals, plus 1×/1.5×/2× cost
sensitivity. Run it only after inspecting the
baseline trades. Prop rules are read from `configs/prop_research_example.json`;
pass `--prop-rules PATH` for a reviewed rule set. The prop replay uses
closed trades, so it cannot prove compliance with intraday equity limits or
FTMO's actual terms. The example $5,000 / 10% / 5% / 5% / 10% rule set comes
from the research brief, not from a verified program contract.

## Research sequence

1. Confirm target account, server, symbol, contract, spread and data coverage.
2. Run the unmodified baseline on the development period only.
3. Hand-inspect at least ten signals and fills, including DST weeks and losing
   trades. Compare a sample to MT5 real-tick Strategy Tester output.
4. Calibrate commission and slippage; run normal, 1.5× and 2× cost scenarios.
5. Only if the baseline passes these checks, implement and run a small random
   parameter search on training data, followed by walk-forward tests and local
   parameter-stability analysis.
6. Freeze the strategy before the final holdout. If it is changed afterward,
   that period becomes development data.

No optimizer or walk-forward results are claimed yet. Data acquisition alone
does not establish realistic fills, commission, or an out-of-sample edge.
