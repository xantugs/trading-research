# XAUUSD ORB research, version 2

This project starts with a deliberately plain Python baseline. Its purpose
is to test whether the opening-range rule survives unseen dates and trading
costs; it does not target a specified monthly return.

## Status

The baseline code and synthetic verification are ready. A real FTMO-Demo
development-period baseline was run on 17 September 2026: 1,749 trades,
−0.120 mean modeled net R per trade, −62.22% cumulative return, and all seven
calendar years negative. Twelve representative trades were checked directly
against cached minute bars, including stop, target, time, gap, ambiguous
same-minute exits, and both 2024 US DST changes. The audit is saved under
`outputs/runs/<timestamp>/trade_audit.json`. These are provisional bar-based
results, not tick-exact fills. A second check read the native tick order for
eight selected exits. The four DST samples agreed with M1. Of four ambiguous
M1 exits with tick history, three touched target first and one touched stop
first. The tick sample is saved as `sample_tick_exit_comparison.json`. Revaluing
those three positions at their original size would recover about $86 before
the changed commission; even a deliberately favorable correction of all 13
ambiguous exits would recover at most about $472 at original sizes, versus
$3,111 modeled net loss. These figures are diagnostics, not a tick-replayed
equity curve. On 17 September 2026, the connected terminal
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
python run_all.py backtest --config configs/xauusd_orb_research.json
```

Edit the JSON parameters before running. The command installs required Python
packages, checks the connected broker, prepares and verifies M1 and native tick
data through the development end date, runs the strategy, generates charts,
audits sample bars and tick exits, and saves a timestamped report bundle under
`outputs/runs/`. A completed first end-to-end run on 17 September 2026 used
the existing archive and needed no manual step after choosing the config.
`--cached-only` verifies and reuses the archive without MT5 connectivity;
`--skip-ticks` omits tick preparation and comparison.

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
The development config models 0.0005% of notional per side for metals CFD,
inferred from [FTMO's 2025 published change](https://ftmo.com/en/blog/trading-updates/trading-update-25-sep-2025/)
to 0.0007% per side effective 29 September 2025. This does not prove the same
rate applied throughout 2018–2024 or that our account was charged identically.
The modeled commission paid across baseline trades was about $155.53. The
configured spread fallback and slippage are also
assumptions to be checked against account history. April, May, June, and
September 2021 have more than 5% zero-spread M1 bars (5.6%, 25.9%, 18.8%, and
55.9% respectively). There are no native broker ticks for those months to
recover the historical bid/ask path, so cost-sensitive inferences from them
remain weak.

## Outputs

One command saves under a new `outputs/runs/<timestamp>/` folder:

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
3. Audit at least ten signals and fills, including DST weeks and losing
   trades. Twelve bar-level checks passed and eight selected exits were
   compared with archived ticks; broader tick replay and MT5 Strategy Tester
   comparison are next.
4. Calibrate commission and slippage; run normal, 1.5× and 2× cost scenarios.
5. Only if the baseline passes these checks, implement and run a small random
   parameter search on training data, followed by walk-forward tests and local
   parameter-stability analysis.
6. Freeze the strategy before the final holdout. If it is changed afterward,
   that period becomes development data.

No optimizer or walk-forward results are claimed yet. Data acquisition alone
does not establish realistic fills, commission, or an out-of-sample edge.
