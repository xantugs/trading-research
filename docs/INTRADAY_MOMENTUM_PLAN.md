# Intraday momentum on the Nasdaq-100: plan (8 October 2026)

Written and committed **before** either strategy below has been run on any data.

**Why these two.** Both come from published research and have fixed, published settings,
so there is nothing to tune. The ORB lesson is applied from the start: the longest
available data, a split fixed before looking, and one holdout test. Nothing for
deployment is built until a strategy passes.

**Honest limit.** The pre-2021 HistData bars were used once, for the ORB holdout
(docs/PRE2021_HOLDOUT_PLAN.md). That says little about these strategies, but the
development years are not untouched by us.

## Strategies

Both are bid M1 bars in New York time. The session is 09:30–16:00, and every position is
flat by the 16:00 close. Returns are % of notional at 1× notional, net of costs.

**S1. Market intraday momentum** (Gao, Han, Li & Zhou, *Journal of Financial Economics*,
2018).
- The first half-hour return is r₁ = price at 10:00 ÷ the previous trading day's 16:00
  close − 1.
- If r₁ > 0, buy at 15:30 and sell at 16:00. If r₁ < 0, short. One trade a day.
- The price "at" a time is the close of the minute bar that ends then.

**S2. Noise-area intraday momentum** (Zarattini, Aziz & Barbon, *Beat the Market*, 2024),
without VWAP, because the feed has no volume.
- **Move at each half-hour checkpoint t** (10:00, 10:30, … 15:30): the absolute move
  from the 09:30 open, |price(t) ÷ open − 1|.
- **σ(t):** the average move at t over the previous 14 trading days.
- **Bands:** upper = max(open, previous close) × (1 + σ(t)); lower = min(open, previous
  close) × (1 − σ(t)).
- **At each checkpoint:**
  - a long with price ≤ upper is closed;
  - a short with price ≥ lower is closed;
  - then, if flat, go long when price > upper and short when price < lower.
- Everything is closed at 16:00.

**Costs.**
- **Spread:** as in the ORB holdout. On HistData, FTMO's median US100 spread as a fraction
  of price (5.16 × 10⁻⁵), times the bar's close. On FTMO, the recorded spread.
- **Slippage:** 10% of the spread per fill.
- **Fills:** longs buy at the ask and sell at the bid; shorts the reverse.

## Data and split

- **Development:** HistData `NSXUSD`, 2010-11-14 to 2017-12-31.
- **Holdout A:** HistData `NSXUSD`, 2018-01-01 to 2024-12-31.
- **Holdout B:** FTMO `US100.cash`, 2025-01-01 to 2026-10-06. This is a second feed,
  already calibrated against HistData.

## Rules

1. **Development gate.** A strategy goes to the holdout only if its development mean daily
   return is above 0. The run checks the code; no setting may change after it.
2. **Holdout A pass.**
   - Mean daily return > 0 with **t ≥ 2.3**, which allows for two strategies.
   - At least 4 of the 7 years are positive.
3. **Holdout B.** Mean daily return > 0. It is reported, and a strategy that passes A but
   fails B is marked "not confirmed".
4. **Holdout access.** Each holdout is read once, by one run that tests both strategies
   together.
5. **If neither passes,** the result is recorded and nothing is retried on these holdouts.

Reported for each strategy and period: trades, days traded, mean daily return in basis
points, t, hit rate, annualised Sharpe (from daily returns, with trade-free days at 0),
and yearly mean.
