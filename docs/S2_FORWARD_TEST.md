# S2 on FTMO: demo forward test (8 October 2026)

**EA:** [`mt5/S2_NoiseArea.mq5`](../mt5/S2_NoiseArea.mq5) v1.10. It compiles in MetaEditor
with 0 errors and 0 warnings, and trades S2 the way `orb_research/intraday_momentum.py`
does.

## Attach

1. Open an FTMO **demo** account (free) in MT5, and a **US100.cash** chart (any
   timeframe).
2. Drag **S2_NoiseArea** onto the chart and load the preset **S2_demo_forward_1x**.
3. Set `InpStartBalance` to the account size, then switch **Algo Trading** on.
4. The panel shows today's open, the bands for the next checkpoint, the position and
   the guards.

**Hours (Ulaanbaatar time).** The session is 09:30–16:00 New York, which is 16:30–23:00
on FTMO's server.

| Period | MT5 running from | Until |
|---|---|---|
| Until 1 November 2026 | 21:15 | 04:00 |
| From 2 November 2026 | 22:15 | 05:00 |

The EA closes everything at the session close, so nothing is held overnight. If MT5 is
off during the session, that day is missed. Unlike the ORB EA, S2 trades all afternoon in
New York, so a VPS is a better fit than a PC that sleeps.

## Presets (`mt5/presets/`, also installed in MT5's Presets folder)

| Preset | Leverage | Daily stop | Hard stop | Use |
|---|---|---|---|---|
| S2_demo_forward_1x | 1× | 4.5% | 9.5% static | the demo forward test |
| S2_2step_challenge_8x | 8× | 4.5% (limit 5%) | 9.5% static | 2-Step challenge, $10k fast plan |
| S2_2step_funded_4x | 4× | 4.5% | 9.5% static, news filter on | 2-Step funded (Standard) |
| S2_1step_challenge_4x | 4× | 2.5% (limit 3%) | 9.5% trailing | 1-Step challenge |
| S2_1step_funded_2x | 2× | 2.5% | 9.5% trailing, news filter on | 1-Step funded |

The sizes come from docs/S2_FTMO_SIZING.md. Unlike the ORB EA, S2's daily stop **closes**
positions, because S2 holds positions during the day.

## Check fidelity weekly

```powershell
python -m scripts.s2_reconcile --log "<Common>\Files\S2_<login>_US100.cash.csv"
```

The script pairs the EA's deals into trades and replays the engine on FTMO's own bars for
those days. It reports:
- the share of live trades matched by date, checkpoint and direction;
- the live and engine mean returns;
- their correlation.

**Before paying for a challenge, aim for:**
- at least 90% of trades matched;
- a mean gap of a few bps at most;
- at least 4 weeks of live trading, which is about 60–80 trades.

## Known differences from the backtest

- Orders fill at market on the first tick after a checkpoint. The backtest uses the close
  of the minute bar that ends at the checkpoint. Entering one minute late cost about
  0.4 bps a day in the holdout test.
- The noise-area lookback skips days with no 16:30 opening bar. The engine counts them as
  empty days in its 14-day window, so it uses the same days in all but rare cases.
- The daily and hard stops are not in the sizing simulation. They can only reduce
  breaches, at the cost of a few days cut short.

## v1.10: a stop-loss on every order, and The5ers

The5ers allows EAs, provided you own the source code and **every trade carries a visible
stop-loss**. v1.10 sets one on every order, `InpStopPct` = **1.25% of price** from the
entry. That is beyond 99.5% of S2's 2010–2017 adverse moves (99th percentile 1.11%,
maximum 1.79%), so it rarely triggers.
- **2018–2024:** 15 of 1,171 trades stopped; +5.63 against +5.87 bps a day (t 3.64).
- **FTMO 2025–26:** 1 of 328 trades stopped, with no change.

The engine models the stop as `s2_noise_area(..., stop_pct=0.0125)`: the first minute
close at or beyond the stop, filled at the stop or that close, whichever is worse.

**The5ers High Stakes 2-Step ($2.5k for $19).**
- **Targets:** +10%, then +5%.
- **Limits:** 5% daily loss and 10% maximum loss.
- **Profitable days:** at least 3 per phase, each up by at least 0.5% of the initial
  balance.
- **Leverage:** up to 1:100.

**Presets:**

| Preset | Leverage | Use |
|---|---|---|
| `S2_5ers_highstakes_steady_2x` | 2× | the forward test |
| `S2_5ers_highstakes_fast_8x` | 8× | the sizing study's fast pass |

At 8×, a normal 0.5% adverse move is about 4% of equity, so the EA's 4.5% daily stop
fires often. That preset is close to a coin flip.

**Check the clock before attaching.** The session inputs assume a server at New York + 7
hours (16:30 = 09:30 New York), which is what a server on GMT+2/+3 switching on US dates
gives. If the server switches on EU dates, the session sits an hour off for a few weeks a
year. Check that the US100/NAS100 chart's big opening move prints at 16:30 server time.

**12-month plan for The5ers $2.5k (`python -m scripts.s2_lifecycle --size 2500 --fee-2step 19 ... --tag _5ers`).**
The simulation includes retries, the 80% split and lost funded accounts.

| Challenge → funded | Net $/month, development → holdout edge | First payout (months) | Below zero after 12 months | Zero edge |
|---|---|---|---|---|
| **8× → 2× (recommended)** | **+9 → +27** | 3.4–4.3 | 19–31% | −$10/month |
| 4× → 2× | +7 → +26 | 4.5–6.1 | 18–40% | −$1/month |
| 2× → 2× | +2 → +17 | 6.4–8.2 | 33–70% | −$1/month |

So the challenge runs on `S2_5ers_highstakes_fast_8x` (only the $19 fee is at risk), and
once funded the account switches to `S2_5ers_highstakes_steady_2x`. The 2× preset alone is
right for checking fidelity, but too slow to pass the +15% two-phase target.

## v1.11: early-close days

On US early-close days (13:00 New York, 20:00 server) the EA would have waited for a
22:59 server close that never comes, so an open position could stay overnight, or over the
weekend after Black Friday. v1.11 does not trade on the dates in `InpEarlyCloseDates`.
The Python engine already skipped those days, since they have no 16:00 close.

The default list is 2026-11-27, 2026-12-24 and 2027-11-26. **Check the NYSE holiday
calendar each year and add any new dates.** The 1.25% stop-loss stays as a backstop.
