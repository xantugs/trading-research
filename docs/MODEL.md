# Model contract and known approximations

> This document records the Python reference model. MT5 is now the active
> backtester; see [MT5_WRDS.md](MT5_WRDS.md) for the imported-tick contract.

## Signal and stop rules

The engine has three range constructions. `signal_bar` waits for the selected
opening candle to complete and uses its high H, low L and width W.
`historical_percentile` takes the selected percentile of the same opening
candle's normalized high-low range over the prior N sessions, applies an
editable positive multiplier, and centres that forecast width on today's open.
`historical_percentile_premarket` additionally scales the width by today's
pre-range realized volatility relative to the median of those prior sessions.
Both forecasts use only information available before today's opening candle.

Close mode can require N consecutive signal candles beyond the range. An
optional colour rule also requires green candles for a long or red candles for
a short. The close immediately before the sequence must not already be beyond
the relevant boundary, so a breakout that reverses before completing
confirmation is rejected.
Entry is the next signal bar's open, before cutoff. Immediate mode checks
path-bar highs/lows and does not use multi-candle confirmation. A forecast range
can trade from the selected opening time; the observed range cannot trade until
its candle has completed.

The default long stop is H - W * sl_ratio and the default short stop is
L + W * sl_ratio. Midpoint mode places either initial stop at (H + L) / 2.
A stop ratio below one puts the stop inside the range; one reaches the opposite
edge; greater than one extends past it. The old prose describing an additional
far-side offset was inaccurate. Target distance is entry-to-stop risk times
tp_ratio. A gap that puts entry beyond stop is skipped.

The first confirmed signal consumes the day's only attempt, regardless of
direction, even if the trend filter vetoes it. No-signal days are reported
separately from traded days.
Daily state resets at midnight of the configured clock.

## Indicators and timing

ATR uses the simple moving average of true range in the installed MetaQuotes
`Indicators/Examples/ATR.mq5`, with its initial seed after period+1 daily
bars. Only yesterday's completed ATR is used. Wilder ATR remains available
as an explicit utility but is no longer the engine default.
[MetaQuotes ATR source](https://www.mql5.com/en/code/12).

EMA/SMA trend filters use completed higher-timeframe history plus a partial
current-bar price approximation. A period-one SMA is exactly current price.
The optional slope veto measures the signed percentage change between completed
MA values. It deliberately lags one completed higher-timeframe bar so the
breakout cannot manufacture the trend used to approve itself. Longs require a
positive slope above the threshold; shorts require the corresponding negative
slope.

The range-proportion filter compares W with the high-low span of a completed
lookback window, for example 120 minutes, and rejects W/span above a threshold
such as 0.25. When enabled, it rejects days without full time coverage instead
of silently shortening the window.
Warmup requirements depend on ATR and MA lookbacks. Like the inherited EA,
unavailable ATR or volume ratios do not veto a trade; missing trend history
does. Fetch additional history when filters are on and inspect early days.

## Execution

The runner exposes two separate WRDS models. M1 mode reads only the configured
M1 root. Tick mode reads only the raw tick root, derives its own M1 and signal
candles, then executes against that root's complete-NBBO events. There is no
cross-root fallback or data mixing.

### M1 bar model

- Bid OHLC is the path. Long entry adds the selected spread, long exits use bid.
  Short entry uses bid; exit tests add spread to the bid OHLC.
- Same-bar SL/TP uses `sl_first` by default. `tp_first` is an explicit optimistic
  alternative. A gap at a later bar open takes precedence and fills at that open.
- Immediate entries have unknown order between the entry crossing and that
  minute's extremes. BE/trailing use extremes and apply the new stop starting
  with the next bar. Neither can reproduce event ordering.
- EOD closes on a recorded path bar at/after the selected time. Missing late
  session data can postpone closing; inspect gaps and `END` exits.
- Unresolved positions are marked at the final available close with reason
  `END`. These are research marks rather than confirmed executions.

### Strict top-of-book tick model

- Strategy state and close confirmation come from M1-derived signal candles;
  M15 is the default signal timeframe. The confirming bar must finish before
  the order can activate.
- Quotes are valid, complete, non-crossed NBBO observations ordered by New York
  time and nanoseconds, then shifted into the `ny+7` strategy clock.
- The first eligible quote at or after activation plus configured latency fills
  a long at ask or a short at bid. Longs liquidate at bid and shorts at ask.
- Stops, targets, and EOD exits fill at the observed executable quote, including
  jumps through a level. EOD has priority at the first quote on/after its time.
- Strict coverage checks reject missing or delayed entry/EOD quotes rather than
  silently substituting an M1 value. The default allowed delay is 1,000 ms.
- This milestone supports close-confirmed entry with fixed SL/TP/EOD exits. It
  does not support immediate entry, breakeven, or trailing.
- Complete NBBO is observed SIP top of book. It does not reveal depth, queue
  position, hidden liquidity, broker routing, partial-fill behavior, or market
  impact, so a replayed price is not a guarantee of a live fill.

Neither model includes margin calls, stop-out, financing/swap, changing
currency conversion, or an exchange holiday/early-close calendar. The engine
allows independent overlapping positions. Hedging accounts align better; an
MT5 netting account can merge or reverse positions and invalidate the
one-trade-per-date/direction reconciliation assumption.

EA v1.50 persists the daily consumed-signal lock and reconstructs the range
after a live restart. Open-trade logging still cannot reconstruct a restart
during an existing position or partial fill/exit. Duplicate reconciliation
keys are rejected, not multiplied.

## Money and statistics

Money per lot is account-currency value for a 1.0 price move (tick value divided
by tick size). Size is risk percent of balance plus floating P/L marked at the
new entry price, floored to the lot step, then clamped to the EA's min/max behavior.
**Clamping to the minimum may exceed the chosen risk percentage.** Spread-aware
floating valuation and broker currency/margin rules are approximations.

An optional gross-leverage cap constrains open notional to equity times the
configured multiple. For QQQ, one lot is one share, `point_value=1`, and
notional is `entry price × shares`. Actual risk and gross exposure are recorded
per trade because a cash cap can prevent use of the full risk budget.

Per-side commission is charged twice at exit; opening commission is therefore
not deducted before sizing overlapping trades. Reported R is gross of costs.
The balance curve is realized P/L, so maximum drawdown is closed-balance
drawdown and can understate open-position losses.

The legacy Sharpe diagnostic uses calendar-day P/L between first/last exits and
a sqrt(252) multiplier. It is descriptive and not a validated risk-adjusted
performance estimator; do not compare it to differently sampled Sharpe ratios.
Profit factor is based on net money; no-loss infinity becomes null in JSON.
Reports identify all assumptions and the exact code/input hashes.

The synthetic generator includes a small configured drift and exaggerated
session-volume patterns. It is a software fixture, not a statistical null
hypothesis test or evidence of any profitable strategy.
