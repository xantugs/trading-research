# Full research notes

The long version of the old README, kept for reference. The short overview is in the [main README](../README.md).

Reproducible, pre-registered trading research: every study fixes its tests before
looking at the data, keeps a sealed holdout, and reports failures in as much detail as
successes. Formerly `orb-backtest`; the repository grew beyond opening-range breakouts.

| Study | Status | Write-up |
|---|---|---|
| Opening Range Breakout (XAUUSD, US100) | Failed its pre-2021 holdout | sections below; `docs/PRE2021_HOLDOUT_PLAN.md` |
| S2 noise-area intraday momentum (US100) | Passed its sealed holdout; MT5 EA on demo | `docs/INTRADAY_MOMENTUM_PLAN.md`, `docs/S2_FORWARD_TEST.md` |
| ML on index intraday features (XGBoost + autoencoder MLP) | No edge after bug fixes | `docs/ML_PLAN.md` |
| JKX chart-image CNN replication (CRSP, 1993–2025) | Replicated; passed the sealed 2020–2025 test | `docs/JKX_REPLICATION_PLAN.md` |
| Pilot A: stock-trained CNN on FTMO instruments | Failed (no transfer) | `docs/JKX_TRANSFER_PILOT.md` |
| Idea B: intraday chart CNNs (TAQ, S&P 500) | Failed its 2018–2020 test: 2–4 bps a day gross at every decision time, net t −5.5 to −6.0 after 8 bps costs | `docs/INTRADAY_CHARTS_PLAN.md` |

There is **no claimed profitable strategy** in this repository.

# Opening Range Breakout research

The original project: a simple Opening Range Breakout strategy, initially on XAUUSD,
tested for whether an edge survives costs, chronological out-of-sample periods, and
parameter changes.

## Current status

### 2025+ holdout opened (6 October 2026)

At the project owner's request, the active strategy configs now read cached FTMO data
through 16 September 2026: the video EA, the Trading Steady presets, the tick scalper
and the dashboard presets. Each records `holdout_opened: "2026-10-06"` and
`development_end: "2026-09-17"`; `holdout_start` stays 2025-01-01 as the historical
boundary. Validation refuses any window past it without that dated field, and every
run prints whether it read the former holdout. **From here on, no result that uses
2025–2026 data is out-of-sample.** Frozen studies keep their sealed 2018–2024 windows,
because their reported results depend on them: the Strategy A walk-forward, Strategy B,
the silver replication, the broker-clock verifier and the legacy dashboard baseline.

First runs on the opened window (FTMO-Demo XAUUSD, $10k start, same rules as before):

| Config | Trades | $10k → end 2024 | → 16 Sep 2026 | 2025–26 trades (mean R) | Max DD |
|---|---:|---:|---:|---:|---:|
| Video EA 15m/15m | 1,615 | $13,887 | $21,509 | 489 (+0.061) | 59.1% |
| Video EA 15m/5m | 1,829 | $7,159 | $8,019 | 552 (+0.025) | 58.7% |
| Live bot (ATR filter, long only) | 616 | $14,654 | $20,154 | 196 | 31.8% |
| Live bot + ATR trailing stop | 616 | $11,619 | $18,356 | 196 | 31.7% |
| Live bot, stop-order entry 2R | 776 | $7,467 | $11,736 | 232 | 51.5% |
| Live bot, flat at 16:00 | 616 | $11,336 | $11,806 | 195 | 22.0% |
| Tick scalper (from 2021-10-04) | 1,999 | $1,849 | $322 | 692 | 97.1% |

2025–26 was kinder to the hold-to-target ORB variants on gold, but nothing approaches
the video's US100 path, which reached about $120k. The 15m/5m entry still trails 15m/15m.
The scalper keeps losing; in 2025–26, margin at 1:30 capped its size on 617 fills,
because gold traded above $3,000.

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

A fixed annual walk-forward selection of the ATR breakout buffer on the
development period selected 0.55 for 2021–2023 and 0.35 for 2024. The four
following-year tests produced 371 trades, +0.0248 mean modeled net R, +3.82%
combined return over four years at 0.5% risk per trade, and 3.00% maximum
closed-equity drawdown. This is encouraging but small and still subject to
earlier development-period selection bias; it is not a validated trading edge.

**Clock caveat (18 September 2026).** The cached MT5 bar timestamps are the
broker's server clock, not UTC: every scheduled 08:30 New York release prints
at 15:30 in the stored clock in winter and summer alike, so the FTMO-Demo
server runs at New York + 7 h year-round. Converting that clock with New York
DST rules places the project's "08:20 New York" session at 06:20 New York in
winter and 05:20 in summer (11:20 / 09:20 UTC), and the "16:00" exit at 14:00
/ 13:00 New York. Every backtest is internally consistent (the same stored
window each day), but the session labels in this repository describe a late
London-morning breakout held to around the COMEX close, not the New York open,
and the window drifts one hour between seasons. `scripts/verify_broker_clock.py`
reproduces the evidence; the `broker_clock` configuration key decodes the clock
correctly when set to `new_york_plus_7`. The default stays `stored_as_utc` so
existing results do not silently move; re-anchoring the session to real New
York time reopens development-period selection and is a deliberate decision.

**True New York session (Strategy B).** The corrected-clock test preserves
Strategy A's settings and results. It uses the actual 08:20–09:20 New York
opening range and 16:00 New York exit. The six-release, trading-week and DST
clock verification passed before backtesting. With fold-selected buffers
0.25/0.25/0.25/0.55 ATR, the 2021–2024 modeled out-of-sample result was
381 trades, +0.0205 mean R, PF 1.088, +2.38% return and 2.69% maximum
closed-equity drawdown; only two of four years were positive. Transferring
the frozen P60 timing and P20 range-width filters at those same buffers
left 249 trades, +0.0038 mean R and +0.18% return. A session-level
bootstrap did not support a positive filter effect. The prespecified
OR-length sweep was not triggered. The 2025+ holdout was not read.

From the repository root, run:

```powershell
python run_all.py verify-clock --config configs/strategy_b_0820.json
python run_all.py strategy-b --config configs/strategy_b_0820.json
```

The second command reproduces the corrected 60-minute test, the original
Strategy A result, and the comparison reports. The shareable summary is in
[Strategy B results](STRATEGY_B_RESULTS.md); detailed local artifacts
are written to `outputs/strategy_b/` and `outputs/session_comparison.md`.

A prespecified multi-arm test of two signal-time entry rules and two later
exits (see below) improved per-trade quality on the same four unseen years:
keeping only first breakouts confirmed within the training P60 of minutes
after the range and sessions whose range width is at or above the training
P20 of ATR gave 158 trades, +0.081 mean net R, profit factor 1.44, +5.39% and
1.74% drawdown against the baseline's 371 / +0.025 / 1.15 / +3.82% / 3.00%.
Later exits made the baseline worse. None of the paired bootstrap intervals
excludes zero convincingly, and the rules were shortlisted on the same years.

**Strategy B is closed (18 September 2026).** After the clock correction, the
intended true 08:20 New York opening-range breakout was tested on its own terms.
Strategy A's two entry filters did not transfer — the trades they removed were
profitable in the New York session — and the single pre-registered 4x4
opening-range/buffer walk-forward failed three of its four pre-declared support
conditions. The intended COMEX-open ORB is unsupported by the 2018-2024
development data; the grid is closed and no rescue search follows. See
`docs/STRATEGY_B_RESULTS.md` and `outputs/session_comparison.md`. Strategy A's
published results are untouched and remain a separate, still-unvalidated
hypothesis about a different session.

**The project is closed (18 September 2026).** Strategy A was replayed
unchanged on XAGUSD as a pre-registered replication: 330 unseen trades, mean
net R -0.0187, profit factor 0.899, -3.02% over four years, and fifteen of
sixteen training cells negative. The structure is absent from silver in-sample
and out-of-sample, so Strategy A does not transfer. Per the pre-registration it
does not go to the 2025+ holdout, which stays sealed and unspent. See
`docs/STRATEGY_A_REPLICATION.md`. Strategy B was already closed by its own
prespecified test. Nothing here is a validated trading edge; what the repository
offers is a verified data pipeline, a broker-clock verifier, and a walk-forward
framework that reached honest negative answers.

## ORB-framework EA: separate range and entry timeframes

The engine reproduces the MT5 "ORB framework" EA, whose change is to read the
opening range on one timeframe and confirm the breakout on another. The range
is the high/low of one range-timeframe candle (5, 15, 30, 45 or 60 minutes)
starting at the ORB time. A signal is the first entry-timeframe close (1, 5 or
15 minutes) outside that range in each direction. Entry is at the next bar's
open: ask for longs, bid for shorts. A 15-minute range entered on 5-minute
closes confirms the same breakout up to ten minutes earlier, nearer the range.
The remaining rules follow `ORB_Backtest.mq5` v1.20:

| EA input | Config key | Preset (`configs/orb_framework.json`) |
|---|---|---|
| ORB hour : minute (broker) | `session_open` (New York; broker = NY + 7 h) | `09:30` (= 16:30 broker) |
| Range timeframe | `range_minutes` | `15` |
| Entry timeframe | `signal_minutes` | `5` |
| Trading cutoff hour | `session_exit`; a bar closing at or after it is not traded | `12:00` (= 19:00 broker) |
| No end-of-day close | `position_exit: stop_or_target` (held across nights and weekends) | yes |
| First breakout per direction | `trades_per_session: one_per_direction` | yes |
| Trade direction | `trade_direction`: `both`, `long`, `short` | `both` |
| SL as multiple of ORB size | `stop_method: range_multiple`, `stop_width_multiple`, from the far side | `1.0` |
| Take-profit ratio | `reward_risk` (× the actual entry-to-stop distance) | `1.5` |
| Risk % per trade | `risk_percent`, of equity incl. open profit on running positions | `2.0` |

Defaults (`position_exit: session_exit`, `trades_per_session: one`,
`trade_direction: both`) keep every earlier configuration and result unchanged.
Two departures from the EA are deliberate. A size below the minimum lot is
skipped rather than rounded up to it. A range timeframe that does not align
with the ORB minute (for example 60 minutes from 09:30) is computed from M1
here; the EA would find no candle at that time and would not trade.

```powershell
python run_all.py orb-framework                              # 15/15 vs 15/5, as in the video
python run_all.py orb-framework --pairs 15/15 15/5 15/1 30/5 # any RANGE/ENTRY pairs
python run_all.py backtest --cached-only --config configs/orb_framework.json
```

First run, FTMO-Demo XAUUSD, 2021–2024 development data only (5 October 2026):

| Range / entry | Trades | Win | Long (win) | Short (win) | Mean R | PF | $10k → | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 15m / 15m | 1,126 | 41.7% | 554 (43.7%) | 572 (39.7%) | +0.030 | 1.03 | $13,887 | 59.1% |
| 15m / 5m | 1,277 | 40.6% | 633 (41.5%) | 644 (39.6%) | +0.002 | 0.97 | $7,159 | 58.7% |

On gold, 5-minute entries were worse than the 15-minute baseline. That is the
opposite of the video's US100 result: about $82k against $120k from $10k,
2021–2026, on OANDA. At 2% risk both arms carry drawdowns near 60%.
This is one exploratory comparison, not a pre-registered test. The 15/5 run
audited 12 trades against M1 bars, and all four tick-compared exits agreed.
US100 is not cached. It needs `symbol`, `point`, `contract_size` and lot
fields set to the broker's specification; `ftmo-data` refuses a mismatch.

## Trading Steady channel variants

All 66 videos on the EA author's channel were read from their captions (6 October 2026).
[docs/TRADING_STEADY_NOTES.md](TRADING_STEADY_NOTES.md) lists every ORB rule change he
tested, what he reported, and how it maps here. The variants he kept or found helpful are
now engine options, all off by default (the 15/15 and 15/5 gold runs above reproduce exactly):

| Option | Keys | Channel source |
|---|---|---|
| Stop-order entry at the range edge on first ask/bid touch; the fill minute can only stop out | `entry_mode: stop_order` | Entry-signals video |
| Range size between bounds of a Wilder ATR on range-timeframe bars | `or_atr_period` (1344 = 14 days of 15m), `or_min_atr`, `or_max_atr` | ATR-filter video; live bot uses 1.25–3.0 |
| Bullish or bearish range candle only | `or_candle` | Six-filters video |
| No fixed target; from `trail_activation_r` × risk, trail each entry-bar close by k × ATR, tighten only | `trail_atr_multiple`, `trail_activation_r`, `trail_atr_period` | Trailing-stop video (1.5R, 1 × ATR) |
| Flat at a fixed time daily, or hold but flat on Friday | `position_exit: end_of_day / friday_close`, `flat_time` | Hold-overnight video |
| Overnight financing on entry notional per calendar night | `financing_pct_annual` | Hold-overnight video (7%) |
| A fresh breakout is retaken once the session's position has closed | `trades_per_session: after_close` | Original rules video |

`python run_all.py orb-framework --configs A.json B.json ...` compares whole configurations.
His published settings, unchanged, on FTMO-Demo XAUUSD 2021–2024 (6 October 2026; all
long only, 15/15, 1.5% risk, ATR filter 1.25–3.0, re-entry after close, 7% financing):

| Config | Trades | Win | Mean R | PF | $10k → | Max DD |
|---|---:|---:|---:|---:|---:|---:|
| `trading_steady_bot` (hold to stop/target) | 420 | 44.8% | +0.074 | 1.14 | $14,654 | 31.8% |
| `trading_steady_trailing` (1 × ATR14 from 1.5R) | 420 | 44.5% | +0.039 | 1.05 | $11,619 | 31.7% |
| `trading_steady_stop_entry` (stop order, 2R, one per day) | 544 | 33.8% | −0.022 | 0.93 | $7,467 | 51.5% |
| `trading_steady_end_of_day` (flat 16:00) | 421 | 48.0% | +0.028 | 1.05 | $11,336 | 21.2% |

On gold the trailing stop and the stop-order entry did worse than the base bot, the
reverse of his S&P 500 results. End of day kept win rate up and drawdown lower, as he
found. The filter halves the trade count against `orb_framework` (1,277 trades at 2%
risk, −28.4%). These are five exploratory runs on development data, not a
pre-registered test; the sampled audits passed for the trailing and stop-entry runs.
Not built: pullback and reversal variants (they failed in his own tests), the
10:00-breakout filter (he flagged it as likely overfit), and the London and
previous-day-high/low strategies, which are separate systems.

## Current candidate and forward test (7 October 2026)

The project's live line of research is one frozen candidate: the video EA's rules on
FTMO `US100.cash`. The rules are a 09:30 New York 15-minute range, the first 15-minute
close outside it per direction before 12:00, and positions held to the stop or a 1.5R
target. It is the dashboard's default preset, at the forward test's 0.5% risk.

- **[docs/US100_CANDIDATE_PLAN.md](US100_CANDIDATE_PLAN.md)** froze it and its
  checks before they ran. It passed all four (years, 27 nearby settings, doubled costs,
  walk-forward), but 2026 to date is negative. FTMO sizing picked 0.5% risk: about an
  84% chance of passing a 2-step challenge, in about 11 months.
- **[docs/BRUTE_FORCE_SEARCH.md](BRUTE_FORCE_SEARCH.md)** searched 9,216
  combinations on US100, US500 and gold, ranked on 2021-10 to 2024 and scored on
  2025–26. **No edge found**: the best training combinations fell to about zero or
  below, and their deflated Sharpe ratios are all under 0.02. Gold's training rank did
  carry into the test, with its top decile at +0.053R. That is a lead for a future study
  pre-registered on future data only, not something to tune now.
- **[docs/US100_FORWARD_TEST.md](US100_FORWARD_TEST.md)** is the only clean test
  left, written before the first live trade. Two FTMO free trials run EA
  [`mt5/ORB_Framework.mq5`](../mt5/ORB_Framework.mq5) v1.30 at 0.5% and 1.5% risk.
  - v1.30 is the user's v1.20 with separate range and entry timeframes and the
    candidate as its defaults. Its trade log now appends instead of being overwritten
    at each restart. It compiles with 0 errors and 0 warnings.
  - `python -m scripts.forward_reconcile --log <EA log>` checks each month that the
    live EA trades the backtested rules. It then applies the pre-declared rules: a stop
    below −0.24R mean at 60 trades, a verdict at 150 trades, and drawdown stops at
    12.2% and 33.3%.

## Index tests: FTMO US100 and US500 (7 October 2026)

The creator's EA and bot trade US100 and the S&P 500, not gold, so the same configs
were run on FTMO-Demo `US100.cash` and `US500.cash`. FTMO keeps full M1 history for
these only from September 2021, so the window is 2021-10-01 to 2026-10-06. That
includes the former holdout, so these results are not out-of-sample. The clock is
New York + 7 h all year, confirmed by the cash-open volume step, including the
March weeks when US and EU clocks differ. Both are modelled spread-only, with no
commission; this is not verified per account. Configs are in `configs/indices/ftmo_*`;
`python -m scripts.index_equity_report` draws the balance curves
(`outputs/indices/equity_curves.html`), with his video's US100 curve overlaid.

| Config ($10k start) | US100 → | Max DD | US500 → | Max DD |
|---|---:|---:|---:|---:|
| Video EA 15m/15m | $63,185 (PF 1.09) | 42.3% | $9,744 (PF 1.00) | 56.3% |
| Video EA 15m/5m | $45,161 (PF 1.07) | 50.1% | $7,149 (PF 0.98) | 58.5% |
| Live bot (ATR filter, long) | $15,140 | 25.6% | $13,324 | 34.6% |
| + ATR trailing stop | $15,380 | 33.1% | $17,798 | 37.3% |
| Stop-order entry, 2R | $15,016 | 32.4% | $12,865 | 34.0% |
| Flat at 16:00 | $16,203 | 19.7% | $20,018 | 22.5% |

US100 is where the video EA works: $63k from $10k at 15m/15m, against about $81.5k in his
OANDA run from January 2021. 5-minute entries did worse here, not better as his video
showed (his 15m/5m run reached about $120k). Most of the gain came in 2025–26, followed by a
sharp drawdown into 2026. On the S&P 500 the EA rules are flat to negative. His live-bot
filters are steadier on both indices but make far less.

Also cached: MOTCapital-Live-1 NAS100 (from 2023-09) and US30 (2021, with a recorded
broker gap from 17 July to 8 September 2025). That server's clock follows EU daylight
saving (`broker_clock: eastern_european`); configs are `configs/indices/nas100_*` and
`us30_*`. `scripts/download_mt5_m1.py --accept-gap YYYY-MM` records a known gap
instead of rejecting the month.

## ORB scalping EA on native ticks

His scalping EA cannot be judged on M1 bars, because its trail moves on every tick.
`orb_research/tick_scalper.py` replays it on the native FTMO bid/ask ticks
(2021-10-04 to 2024; the loader refuses any day from `development_end` on) in the
broker clock (New York + 7 h):
- **Range:** the bid high and low of the 09:15–09:30 New York candle (16:15 broker).
- **Orders:** a buy stop at the high triggers on the ask; a sell stop at the low
  triggers on the bid. Both stay live. An order already inside the spread when placed
  is rejected, as MT5 does. Unfilled orders are deleted at 12:00 New York (19:00 broker).
- **Exits:** a 200-point stop and target, plus a 10-point trail that starts 10 points in
  profit and tightens on every tick. A stop set on one tick applies from the next.
- **Fills:** every market fill waits 130 ms and takes the next tick's price; targets fill
  as limits.
- **Sizing:** 2% of equity, cut to fit margin at 1:30 leverage.

Points are the symbol's own, so on XAUUSD (point 0.01) the stop is $2.00 and the trail
$0.10, against a typical $0.13–0.21 spread in that window. That is about the same
trail-to-spread ratio as on his US100. Three settings are assumptions, because the video
does not state them: the 10-point trail start, both orders staying live
(`one_cancels_other: false`), and positions not being closed at the cutoff.

```powershell
python run_all.py tick-scalper --config configs/tick_scalper.json
```

The run writes a standard run folder, so the dashboard history, trade list and tester
report read it like any backtest. Result on FTMO-Demo XAUUSD, 2021-10-04 to 2024-12-31
(6 October 2026), as configured:

| Trades | Win | Avg win | Avg loss | Mean R | PF | $10k → | Max DD | Exits |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1,307 (631 long, 676 short) | 65.0% | $18.27 | −$51.81 | −0.064 | 0.66 | $1,849 | 83.7% | 1,101 trail, 194 stop, 12 target |

It does not reproduce his US100 result (88% win rate, 2.5% drawdown). Removing the
fill delay makes it worse (−0.094 mean R, $882), and also removing commission still
leaves −0.085R. A 10-point trail exits at about breakeven after one spread, so wins
are a few dollars. They cannot pay for the stops that hit the full 200 points before
the trail starts. Blocked orders: 32 buy and 13 sell stops rejected inside the spread;
10 days had no tick file.

## One-command backtest

On Windows, install Python 3.11–3.13 and MetaTrader 5, then log into the
intended FTMO-Demo account. Set the strategy and cost parameters in
[`configs/xauusd_orb_research.json`](../configs/xauusd_orb_research.json). From the
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
places orders. Read the [research workflow](RESEARCH_V2.md) before
interpreting results and the [FTMO data-quality inventory](FTMO_DATA_QUALITY.md)
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
(also saved in the ignored `outputs/web/access_key.txt`). The sidebar starts
from a **preset**: each brings its own data window and broker clock. They are
the video EA at 15/5 (the default) and 15/15, the four Trading Steady live-bot
variants, and the original legacy-clock baseline for reproducing September
2026 runs. Every engine option stays tunable; the panel marks a changed preset
and resets it in one click. "Load these settings" copies any past run back into
the form. Run starts a background `run_all.py backtest --cached-only` job on
that preset's development window (never past 2024). It shows progress, recent
log lines, closed-trade drawdown, the trade list and a persistent history.

Each completed run also gets an MT5-style **strategy tester report**:
- net and gross profit, profit factor, expected payoff, recovery factor;
- per-trade Sharpe, AHPR and GHPR;
- the balance curve's linear-regression correlation and error, and the runs-test Z-score;
- balance drawdown (absolute, maximal, relative), long/short and win/loss counts;
- largest and average trades, consecutive-win and -loss streaks, and holding times.

The report's charts are the balance curve, entries and profit/loss by hour, weekday
and month (New York time), and scatters of profit against MFE, MAE and holding time.
MT5's equity drawdown needs tick marking. Here it is a worst-case estimate: every open
position sits at its own MAE at once. Margin and deposit load are not modelled. Only
one run may use the local worker at a time. The tick option adds exit spot-checks; it
does not switch the engine to tick execution.

The web server binds to localhost and requires the key for every API request.
The dashboard also has a **Walk-forward** setup tab. Its button runs the fixed
four-fold buffer-selection plan on cached development M1 data, with live fold
progress. Completed selections, test-year metrics and the combined drawdown
appear beside single backtests in the same run history. The walk-forward tab
does not use the editable single-backtest fields.
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
optimize parameters or open the final holdout. The separate walk-forward test
below follows the completed exploratory buffer sweep; it is not an optimizer.

## Fixed buffer walk-forward test

The separate research command selects an ATR breakout buffer using only the
preceding three years, then scores the following year. It uses the cached
FTMO-Demo M1 data and never loads the 2025-onward final holdout:

```powershell
python run_all.py walk-forward
```

The candidate set is fixed at 0.25, 0.35, 0.45 and 0.55 ATR. The folds train
on 2018–2020, 2019–2021, 2020–2022 and 2021–2023, then test 2021, 2022,
2023 and 2024 respectively. Each fold chooses the highest training mean net R;
exact ties choose the smaller buffer. Only the four test years are joined for
the reported walk-forward result. The command writes its settings, selections,
trades and report under `outputs/walk_forward/`. This is development-period
evidence: the candidate values were chosen after earlier inspection of
2018–2024, so the test is not fully independent of the overall research process.

## Entry-rule and exit arms

Two signal-time rules are available as configuration keys and are applied
inside `find_signals`, so the trade audit, the dashboard and the walk-forward
all see the same decision: `entry_cutoff_minutes_after_or` skips a session
whose *first* breakout is confirmed more than that many minutes after the
opening range ends (there is never a second chance in a session), and
`min_or_width_atr` skips sessions whose opening range is narrower than that
multiple of the prior daily ATR14 — with the opposite-range stop that is the
stop distance in ATR units. Both default to null.

```powershell
python run_all.py filter-arms
```

runs fifteen prespecified arms through the same four folds as the buffer
walk-forward: baseline; early rule, width rule and both at the 16:00 exit; the
same four at 17:00 and 18:30 exits with the entry window held at the base
400 minutes; and three arms that re-select the buffer per fold after the rules
are applied. Inside each training window the arm takes the buffer, sets the
timing cutoff at the 60th percentile of minutes-after-range among completed
training trades and the width floor at the 20th percentile of range/ATR, then
runs the following year once. Fixed arms reuse the baseline's buffer so the
difference is attributable to the rule. Outputs under `outputs/filter_arms/`:
`arms_summary.csv`, `arms_folds.csv`, `arms_training.csv`, `arms_paired.csv`
(expectancy difference against each arm's comparison arm with a
session-block bootstrap), one OOS trade file per arm, `spec.json` and
`report.md`. The command re-runs `walk-forward` and refuses to write a report
whose baseline arm differs from it. The percentiles are fixed; do not retune
them against these test years.

## Candidate-trade dataset for later ML research

Build a feature table from the four **frozen** ATR buffers without fitting a
model or changing the strategy:

```powershell
python run_all.py build-ml-dataset --config configs/walk_forward_base.json
```

This command uses the cached FTMO-Demo M1 months from 2018–2024 only. It
requires the complete cache and refuses to download missing months or read the
2025-onward holdout. The existing ORB signal generator and trade simulator
determine each candidate and its realized outcome; features are reconstructed
from completed candles available at that candidate's own signal time. Buffer
siblings on the same date share a `session_id` so future training can group
them. No model is trained here.

The ignored `outputs/ml/` directory receives
`orb_event_features.csv`, `orb_event_features_dictionary.md`,
`orb_event_features_validation.md`, and `orb_event_feature_audit.json`. The
audit compares 20 randomly sampled feature snapshots with raw-history prefixes
and checks 10 sessions where buffer entries occur at different times. It fails
the command if these or the holdout/timestamp checks fail. The dictionary
defines every column and records the latest information timestamp. Outcome and
execution columns are explicitly outside the predictor set. The M1-derived
MAE/MFE and reached-R labels remain approximations; the table is research
input, not a validated live trading filter.

To describe whether the fixed entry-time features have similar relationships
with outcomes across years, buffers and directions, run:

```powershell
python run_all.py analyze-features --config configs/walk_forward_base.json
```

This command reads `outputs/ml/orb_event_features.csv` once. It derives
direction-aligned copies of seven raw context features in memory, uses fixed
full-development quantile bins, and writes descriptive bin, stability,
correlation, redundancy and 10,000-draw session-block bootstrap tables under
`outputs/ml/`. Start with
[`feature_stability_report.md`](outputs/ml/feature_stability_report.md). It
does not reload M1 or tick data, train a model, select a feature or threshold,
or inspect the 2025-onward holdout. Buffer siblings share a session and are
resampled together in the bootstrap; no result is an independent live-trading
test.

## Checks

```powershell
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m pytest -q
```

Market data, account details, and generated reports are not committed. Source
code is MIT licensed; market-data rights remain with their providers.
