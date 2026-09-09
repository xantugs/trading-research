# Canonical ORB rule contract

This document defines the intended strategy. A Python or MT5 result is valid
only when it follows these rules and uses the same instrument, clock, data,
costs and parameters.

## Session and range

The strategy day is the date in the configured dataset or broker-server clock.
State resets at 00:00. The range timestamp must be a signal-timeframe boundary,
and the entry cutoff must be later than it.

The range is one of:

1. `signal_bar`: high and low of the selected opening candle. It becomes known
   only when that candle closes.
2. `historical_percentile`: the selected percentile of normalized high-low
   widths from the prior N matching opening candles, multiplied by an arbitrary
   positive factor and applied to today's opening price. The box is centred on
   that opening price and is known at the range timestamp.
3. `historical_percentile_premarket`: the historical forecast multiplied by
   `(today premarket RV / median prior-session premarket RV) ^ sensitivity`.
   All volatility windows end before the range timestamp.

Forecast modes never use the current opening candle's completed high, low or
volume. The volume-spike filter therefore applies only to `signal_bar`.

Optional range filters compare width with yesterday's completed daily ATR and
with the high-low span of a complete prior intraday window. Missing history
rejects an enabled filter; it does not silently shorten the lookback.

## Signal and daily lock

Immediate mode accepts the first permitted price crossing of a range boundary.
Close mode requires N consecutive signal candles closing beyond one boundary.
If colour confirmation is enabled, every long candle must close above its open
and every short candle below its open. The close immediately before the N-bar
sequence must not already be beyond the same boundary. It can be inside the box
or beyond the opposite boundary.

The entry occurs at the first available quote/open after confirmation and must
be before the cutoff. The first valid breakout consumes the entire day's one
attempt, regardless of direction. A trend-filter veto, spread rejection,
minimum-stop rejection, gap rejection, margin rejection, or broker order
rejection does not reopen the day. Later breakouts are recorded as locked.

When enabled, the trend filter requires a long price above its MA or a short
price below it. Its optional slope uses MA values completed before the
breakout's higher-timeframe candle. Long slope must meet the positive threshold;
short slope must meet the negative threshold.

## Stop, target and size

For range high H, low L and width W:

- range-multiple long stop: `H - W * sl_ratio`
- range-multiple short stop: `L + W * sl_ratio`
- midpoint stop, either direction: `(H + L) / 2`
- target distance: `abs(entry - initial_stop) * tp_ratio`

An entry on or beyond its stop is rejected. The spread and minimum-stop guards
are evaluated at entry. Position size risks the selected percentage of current
equity, rounds down to the lot step, then applies the symbol's min/max lots.
Minimum-lot clamping can exceed requested risk.
When `max_gross_leverage` is positive, money simulation also caps total open
notional at equity times that value. If even one minimum lot is unaffordable,
the hypothetical signal remains in the research ledger with zero executed lots.

## Exit and measurement

Initial SL and TP are active from entry. Later-bar gaps fill at the bar open in
Python. If both levels occur in one unresolved bar, `same_bar_rule` selects SL
first by default. MT5 real-tick testing uses the recorded tick path instead.
Breakeven and trailing distances use the original entry-to-stop distance as 1R.
The fixed TP preserves that initial risk. Optional EOD close exits on the first
available event at or after its time; remaining positions are marked `END` at
the final Python bar.

Gross R excludes commission. Money P/L includes configured spread behavior and
per-side commission. MFE and MAE are bar-path estimates in initial R; entry-bar
ordering can overstate them without ticks. The result bundle records the range,
decision events, trades, parameters, hashes and source assumptions. Every bar
that touches both SL and TP is explicitly flagged with its timestamp so the raw
quote archive can later determine which executable side crossed first.

## Required parity procedure

Compile the checked-in EA with zero warnings, run MT5 using real ticks where
available, disable breakeven/trailing for the first comparison, and export its
CSV log. Reconcile against Python on the same symbol, date span, signal bars,
M1 path, clock, spread and contract settings. Investigate systematic entry,
stop, target, direction, exit-reason or R differences before interpreting any
performance result.
