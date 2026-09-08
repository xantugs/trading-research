# Model contract and known approximations

## Signal and stop rules

One complete opening candle per strategy-clock date supplies high H, low L and
width W. Close mode checks previous close <= H and last close > H for a long,
or previous close >= L and last close < L for a short. Entry is the next
signal bar's open, before cutoff. This catches gap breakouts. Immediate mode
checks M1 highs/lows and can see both directions within one minute.

Long stop = H - W * sl_ratio. Short stop = L + W * sl_ratio.
A stop ratio below one puts the stop inside the range; one reaches the opposite
edge; greater than one extends past it. The old prose describing an additional
far-side offset was inaccurate. Target distance is entry-to-stop risk times
tp_ratio. A gap that puts entry beyond stop is skipped.

The signal consumes that day's directional attempt even if the trend filter
vetoes it. No-signal days are reported separately from traded days.
Daily state resets at midnight of the configured clock.

## Indicators and timing

ATR uses the simple moving average of true range in the installed MetaQuotes
`Indicators/Examples/ATR.mq5`, with its initial seed after period+1 daily
bars. Only yesterday's completed ATR is used. Wilder ATR remains available
as an explicit utility but is no longer the engine default.
[MetaQuotes ATR source](https://www.mql5.com/en/code/12).

EMA/SMA trend filters use completed higher-timeframe history plus a partial
current-bar price approximation. A period-one SMA is exactly current price.
Warmup requirements depend on ATR and MA lookbacks. Like the inherited EA,
unavailable ATR or volume ratios do not veto a trade; missing trend history
does. Fetch additional history when filters are on and inspect early days.

## Execution

- Bid OHLC is the path. Long entry adds spread, long exits use bid. Short
  entry uses bid; exit tests add spread to the bid OHLC.
- Same-bar SL/TP uses sl_first by default. tp_first is an explicit optimistic
  alternative. A gap at a later bar open takes precedence and fills at that open.
- Immediate entries have unknown order between the entry crossing and that
  minute's extremes. BE/trailing use extremes and apply the new stop starting
  with the next bar. Neither can reproduce tick ordering.
- EOD closes on a recorded path bar at/after the selected time. Missing late
  session data can postpone closing; inspect gaps and END exits.
- Unresolved positions are marked at the final available close with reason END.
  These are research marks, not confirmed broker executions.
- There is no slippage, margin, stop-out, financing/swap, changing currency
  conversion or exchange holiday/early-close calendar in the Python model.
- The engine allows independent overlapping positions. Hedging accounts align
  better; an MT5 netting account can merge/reverse positions and invalidate the
  one-trade-per-date/direction reconciliation assumption.
- Partial fills/exits and restarts of the EA are not fully reconstructed by
  the log. Duplicate reconciliation keys are rejected, not multiplied.

## Money and statistics

Money per lot is account-currency value for a 1.0 price move (tick value divided
by tick size). Size is risk percent of balance plus floating P/L marked at the
new entry price, floored to the lot step, then clamped to min/max like EA v1.2.
**Clamping to the minimum may exceed the chosen risk percentage.** Spread-aware
floating valuation and broker currency/margin rules are approximations.

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
