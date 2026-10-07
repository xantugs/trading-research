# S2 on FTMO: demo forward test (8 October 2026)

**EA:** [`mt5/S2_NoiseArea.mq5`](../mt5/S2_NoiseArea.mq5) v1.00. It compiles in MetaEditor
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
