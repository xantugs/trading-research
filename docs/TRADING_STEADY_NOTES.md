# Trading Steady channel: ORB rules and how this engine reproduces them

Source: the 66 public videos on the Trading Steady YouTube channel, read from their
English captions on 6 October 2026. Captions were kept outside the repository. This file
paraphrases the rules and repeats the results each video reported. Those results are
the creator's own, mostly on the S&P 500 CFD (Python backtests from 2020) or OANDA US100
(MT5 EA). None of them was run here unless stated. ORB-related videos are listed; the rest
(RSI, MACD, ADX, carry trade, Python tutorials) do not bear on this engine.

## The base rules (repeated in nearly every ORB video)

- 15-minute chart in New York time. The 09:30 candle's high and low are the range.
- Signal: a candle that opened inside the range closes outside it. Wicks do not count.
  Entry at the next candle's open.
- Stop at the far side of the range; target 1.5 × the stop distance.
- No new entries from 12:00 New York.
- Hold until stop or target, overnight and over weekends if needed (longest about 6 days).
- Live Python bot: S&P 500 CFD, long only, risk 1.5% (2% in the backtests), range must
  lie between 1.25× and 3× ATR(1344) on 15-minute bars (14 days of 15-minute candles).
- The original rules video takes a new signal after a stop-out if it comes before 12:00.
  The later MT5 EA instead allows one long and one short per day.

Config: `configs/trading_steady_bot.json` (15/15, long only, `trades_per_session:
after_close`, `or_min_atr` 1.25, `or_max_atr` 3.0, risk 1.5%, financing 7% a year).

## Videos that tested a rule change

| Video (short title) | What was tested | Reported result | Engine support |
|---|---|---|---|
| Original ORB backtest | Hold vs close at end of day; re-entry before 12:00 | Hold: 62% a year, 28% DD, 47% win. End of day: about 44% a year, 23% DD, average R 1.25 | `position_exit`, `trades_per_session: after_close` |
| ATR filter | Min and max range size vs ATR(1344, 15m) | 1.25×–3× kept 483 of 765 trades; DD 28% → 15%; win 50.8% | `or_atr_period`, `or_min_atr`, `or_max_atr` |
| Trailing stop ("2400%") | Remove the target; trail at 1.5R by k × ATR on each candle close | Best: start at 1.5R, 1 × ATR. 100 → 2,436 over 5 years, 82.5% a year, 28% DD, 47% win, average R 1.63 | `trail_atr_multiple`, `trail_activation_r`, `trail_atr_period` |
| Six trailing-stop types | ATR, swing low, %, EMA50, SuperTrend, PSAR on random daily entries | ATR and EMA best; SuperTrend and PSAR worst | ATR only |
| Entry signals | Close confirmation vs stop order at the range edge | Stop order at 1.5R worse; at 2R, 75% vs 60.6% a year but higher DD and more trades | `entry_mode: stop_order` |
| 1M / 5M / 15M entries | 15m range entered on 15m, 5m or 1m closes | Same 1.5R: 15m best (47% win vs 44%). Each tuned: 5m at 2R still behind; 1m at 2.5R 75.3% a year with more DD. Shorts lost on S&P | `signal_minutes`, `reward_risk` |
| Five ORB windows | 1, 5, 15, 30, 60-minute ranges on 1m bars | 15m best balance. 1m and 5m needed 2× stop. Longer ranges: lower return, lower DD, higher win | `range_minutes`, `stop_width_multiple` |
| Hold overnight? | Hold vs end-of-day vs Friday close, plus 7%/365 nightly financing | Holding returned most; financing cut 60% → 56.25% a year and average R 1.48 → 1.45; end of day raised win rate and lost little | `position_exit: end_of_day / friday_close`, `flat_time`, `financing_pct_annual` |
| Six filters | Bullish vs bearish range candle; breakout size; IBS; VWAP; 09:45 or 10:00 breakout; combination | Bullish range: about +2% win, fewer trades; bearish worse. Breakout size, IBS, VWAP no help. 10:00 breakout near 60% win on few trades; bullish + 10:00 gave 67% on 53 trades (flagged as likely overfit) | `or_candle` (timing filter not added) |
| Breakout + pullback | Wait for a pullback into the range, then re-break; stop order at the edge; midpoint entry | All worse than the base strategy; 130 trades over 5 years | Not implemented (failed) |
| ORB reversal | Liquidity candle > 25% daily ATR, 5m hammer/engulfing back into range, two targets | About 30% win; unprofitable | Not implemented (failed) |
| ORB scalping EA | 09:15 NY 15m candle, stop orders both sides, 10-point trailing stop | 88% win but average loss over 2× average win; needs tick data | `orb_research/tick_scalper.py` on native FTMO ticks (README) |
| EA: separate range/entry timeframes | 15m range, 5m entries on US100 | $10k → $81.5k (15/15) vs → $120k (15/5) | `configs/orb_framework.json`; see README |
| EA: trailing stop, second session | ATR trailing (on/off + multiplier); optional London ORB alongside New York | No numbers | Trailing yes; second session not combined into one equity curve |
| FTMO demo challenges | EA at 2% risk on 14-day trials | Both failed (about 31% and lower win on 13 and 18 trades); conclusion: risk 1–1.5% given 5%/10% loss limits | `risk_percent`; `prop_simulator.py` |

## Other breakout strategies on the channel (not built here)

- **London breakout (GBPJPY, H1):** range from the 04:00–08:00 London candles. Two
  versions. In the first, the breakout candle closes outside the range and must be 1–2.5×
  ATR in size, with a 1.5R target, long only (48% a year, 14% DD). In the second, stop
  orders sit at the range edges with a 4R target and are cancelled at 12:00, long only
  (80% a year, 15% DD).
- **Previous-day high/low on XAUUSD:** stop orders at yesterday's high and low, one
  cancels the other, unfilled orders expire at a cutoff. The stop is 10% of yesterday's
  range inside the level and the target 25% beyond it, at 1% risk. Profit factor 1.11,
  52% DD, profitable only from mid-2023.

## What this repository adds, and what it does not claim

All new keys default to off, and the earlier configurations give identical results; the
15/15 and 15/5 gold runs were reproduced trade for trade. The channel's numbers are on
other instruments, other brokers and a period that includes 2025–2026. Here, only
2018–2024 development data are read. The presets reproduce his published settings
without retuning. Tuning these knobs on our development data would multiply the number
of tested variants, the risk the pre-registration rule in this project exists to
control. Any candidate should be frozen in writing before a walk-forward test.
