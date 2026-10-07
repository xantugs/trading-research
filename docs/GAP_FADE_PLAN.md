# Opening-gap fade on the Nasdaq-100: plan (8 October 2026)

Written and committed **before** the strategy has been run on any data.

**Why.** S2 is momentum. It loses often and wins big, with 41–46% winning days. A strategy
that wins often on different days could raise the share of winning days. The research:
the Nasdaq-100 futures' overnight return tends to reverse in the opening hours (Yu,
Rentzler & Wolf, JOIM 2005), and large opening moves in US index futures reverse
intraday (Grant, Wolf & Yu, 2005), though costs take much of that profit. One version is
specified here, from those findings, and nothing is tuned.

## Rule (M1), New York time

- **Gap:** g = 09:30 open ÷ the previous session's 16:00 close − 1.
- **Large gap:** the day trades only if |g| is at least the average |g| of the previous 14
  sessions (at least 10 with a gap), the same lookback as S2.
- **Entry:** at 09:31, the close of the 09:30 bar, against the gap. Short if g > 0, long
  if g < 0.
- **Exit:** at the first minute close back at or beyond the previous close (the gap
  filled), or else at 10:00, the close of the 09:59 bar. No other stop. The trade is
  always closed before S2's first checkpoint, so the two never hold a position at the same
  time.
- **Costs:** as for S2. Spread is FTMO's US100 median as a fraction of price (HistData), or
  the recorded spread (FTMO); slippage is 10% of the spread per fill.

## Data

- **Development:** HistData `NSXUSD`, 2010-11 to 2017.
- **Holdout A:** HistData 2018–2024. The fade has never been run on it, though S2 has.
- **Holdout B:** FTMO `US100.cash`, 2025-01 to 2026-10.

## Rules

1. **Gate.** Development mean daily return > 0 (days without a trade count as 0).
2. **Holdout A pass.**
   - Mean daily return > 0 with t ≥ 2.0.
   - At least 4 of the 7 years positive.
   - Win rate is reported, not required.
3. **Holdout B.** Mean > 0 confirms.
4. **Reported for the user's goal:** S2 and the fade combined at equal notional, each day's
   return being S2's plus the fade's.
   - winning days;
   - weeks with at least 3 winning days;
   - winning months;
   - dollars per day for $10k at 1× and 4×.

   These are reported for development, holdout A and holdout B, next to S2 alone.
5. **One holdout run.** Nothing is changed after it.

## Results (8 October 2026)

| | Fade alone | S2 alone | S2 + fade |
|---|---|---|---|
| Development 2010-11 to 2017: bps/day (t) | +0.29 (0.56) | +1.79 (1.77) | +2.03 (1.84) |
| Development: winning days, of days traded | 48% | 41% | 46% |
| **Holdout A, 2018–2024: bps/day (t)** | **+0.29 (0.38)** | +5.87 (3.84) | +5.91 (3.63) |
| Holdout A: winning days / weeks with 3+ wins / winning months | 50% / 5% / 53% | 45% / 10% / 67% | 49% / 26% / 54% |
| Holdout B, FTMO 2025-01 to 2026-10: bps/day (t) | +1.31 (0.87) | +2.66 (0.88) | +3.88 (1.23) |
| Holdout B: winning days | 53% | 44% | 50% |

The fade by year, holdout A: 2018 +3.4, 2019 −1.4, 2020 +3.4, 2021 +0.5, 2022 −5.6,
2023 +3.4, 2024 −0.5 bps/day. It traded on about 40% of days.

**Verdict: fail.** The fade's holdout t is 0.38 (2.0 was required), though 4 of 7 years
were positive. It wins about half the days it trades and earns about nothing after costs,
which is what Grant, Wolf & Yu warned. Adding it to S2 lifts winning days from about 45%
to about 50%. In holdout A it also lowers the Sharpe ratio (1.53 against 1.65) and cuts
winning months from 67% to 54%. So it is not adopted, and S2 alone stays the strategy.
