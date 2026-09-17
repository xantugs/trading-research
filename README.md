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
Every stored file passed read-back validation. The first development-period
baseline (2018–2024) produced 1,749 trades, −0.120 mean modeled net R per
trade and −62.22% cumulative return at 0.5% initial equity risk per trade.
All seven calendar years were negative. These are provisional bar-based results,
not verified broker fills. The 2025-and-later final holdout was not read.
Archived ticks reversed the stop-first order on three of four ambiguous
post-2021 exit bars checked, so the M1 result is pessimistic on those trades.
Even treating all 13 ambiguous exits as target-first would recover at most
about $472 at the original position sizes against a $3,111 modeled loss.

## One-command backtest

On Windows, install Python 3.11–3.13 and MetaTrader 5, then log into the
intended FTMO-Demo account. Set the strategy and cost parameters in
[`configs/xauusd_orb_research.json`](configs/xauusd_orb_research.json). From the
repository root, run:

```powershell
python run_all.py backtest --config configs/xauusd_orb_research.json
```

That command creates the Python environment and installs dependencies if
needed; downloads or reuses FTMO development-period M1 and tick data; verifies
the files and broker identity; runs the backtest and charts; checks representative
trades against M1 bars and tick order; and saves everything in a new
`outputs/runs/<timestamp>/` folder. It prints a progress bar and final metrics.
It never places orders or opens the 2025-onward strategy holdout. Each run keeps
a copy of the chosen parameters, so changing the JSON for the next run does not
change the old result. Once data is cached, `--cached-only` reruns without an
MT5 connection. `--skip-ticks` performs a quicker M1-only run.

The separate data commands remain available when you want to extend the
archive beyond the development period:

```powershell
python run_all.py broker-probe --symbol XAUUSD --start-year 2021
python run_all.py ftmo-data --with-ticks
```

`ftmo-data` installs the research dependencies, then downloads and verifies
each M1 month; completed months are reused on the next run. `--with-ticks`
also archives the full native bid/ask tick stream by complete UTC day, marking
broker-history gaps instead of inventing quotes. The downloader caches
development and holdout M1 months under
`C:\data\ftmo_xauusd_m1` (outside OneDrive). The one-command backtest runner
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
for bars that touch both exit levels. The development run models a 0.0005%
per-side notional commission, inferred from FTMO's published 2025 change from
that rate to 0.0007%; older historical charges have not been verified. Several 2021 M1 months
have high rates of zero recorded spread; fallback costs there are assumptions.
FTMO's exact Gold
commission and the slippage assumption must be checked against the account's
contract terms and trade history before interpreting returns.

## Private browser dashboard

To tune parameters without editing JSON, start the local web dashboard:

```powershell
python run_all.py web
```

Open `http://127.0.0.1:8520/` and enter the access key printed in the terminal
(also saved in the ignored `outputs/web/access_key.txt`). The page lets you
adjust the session, range, risk, stop, target and cost assumptions. Run starts a
background `run_all.py backtest --cached-only` job against the **2018–2024
development period only**. It shows stage-based progress, recent log lines,
closed-trade drawdown and a persistent history of key results. Only one run may
use the local worker at a time. Checking the tick option adds selected exit
spot-checks; it does not switch the engine to tick execution.

The web server binds to localhost and requires the key for every API request.
To reach it from a phone or another machine, add `--tunnel`:

```powershell
python run_all.py web --tunnel
```

That starts `cloudflared` alongside the worker and prints a temporary
`https://….trycloudflare.com` link a few seconds later. The link is different
on every start and stops working when the window closes; the worker keeps
binding to localhost, and the access key still protects every API call. Install
cloudflared once with `winget install Cloudflare.cloudflared` (other platforms:
[Cloudflare downloads](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)).
[Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/#quick-tunnels-development)
are meant for testing; use an authenticated named tunnel for a stable address.

The browser view exposes results and parameters, not raw broker data. Vercel
can host a static page, but its functions cannot read the data and MT5 login
on this Windows machine; the long-running worker must stay on an accessible
host, which is why the tunnel points at it instead. Do not publish the access
key or the `outputs/` directory.

After reviewing the baseline, optional analysis is available with:

```powershell
python run_all.py backtest --cached-only --post-baseline-analysis
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

Market data, account details, and generated reports are not committed. Source
code is MIT licensed; market-data rights remain with their providers.
