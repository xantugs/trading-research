# Intraday strategies for the 0DTE era: plan (8 October 2026)

Written and committed **before** any of the rules below has been run.

**Why.** Independent out-of-sample evidence shows S2's paper strategy losing most of its
edge on SPY from May 2024 to March 2026 (Sharpe 0.39; Paz, SSRN). Our own S&P 500 S2
result for 2025–26 is negative. Research on same-day (0DTE) S&P options finds that dealers
are mostly long gamma, which strengthens intraday **reversals** and mutes momentum, most of
all on calm days. The candidates below follow from that mechanism, plus one well-documented
calendar effect. Every setting is fixed here, and none is tuned.

## Markets and data

- **US100 / Nasdaq-100:** HistData `NSXUSD`, 2010-11 to 2024, then FTMO `US100.cash`,
  2025-01 to 2026-10.
- **US500 / S&P 500:** HistData `SPXUSD`, the same years, then FTMO `US500.cash`.
- **Costs as before:**
  - spread on HistData: FTMO's 2025–26 median in-session spread as a fraction of price;
  - spread on FTMO: the recorded spread;
  - slippage: 10% of the spread per fill.
- **Overnight positions** also pay FTMO's current long swap on every night: 2.13 bps of
  price per night for US100 and 2.23 bps for US500, three times on Fridays. The same rate
  is applied to every year, which is conservative for the low-rate years 2010–2021.

## Candidates (New York time; the price at a time is the close of the minute bar ending then)

**R2: calm-day fade back to the open.**
- **A calm day:** the previous session's range (the highest close minus the lowest, ÷ the
  open) is at or below the median of the previous 20 sessions' ranges.
- **σ(t):** the 14-session average |price(t) ÷ open − 1| at each half-hour checkpoint t,
  10:00 to 15:30 (at least 10 sessions).
- **Entry:** on a calm day, at the first checkpoint where |price ÷ open − 1| > σ(t), trade
  against the move.
- **Exit:**
  - **Take profit:** the first minute close back at or past the open.
  - **Stop:** the first minute close with the move at 2σ(t) of entry checkpoint t or more,
    against the trade.
  - **Otherwise:** at 16:00.
- At most one trade a day.

**R3: late-day reversal.**
- **Move:** at 15:00, m = price ÷ open − 1.
- **Entry:** if |m| is above its 14-session average at 15:00 (at least 10 sessions), trade
  against it from 15:00.
- **Exit:** at 16:00. There is no stop.

**R4: overnight long.**
- Buy at 16:00 every session and sell at the next session's 09:30 open, paying the swap.
- This is the documented overnight drift (Cliff, Cooper & Gulen, 2008; Kelly & Clark,
  2011), not a 0DTE effect.

## Pass rule: 3 candidates × 2 markets = 6 tests, so the bar is strict

A (candidate, market) **passes** only if all three hold:
- it is profitable in **the 0DTE era, 2023-01 to 2026-10**, with **t ≥ 2.5**;
- the 2023–24 mean (HistData) is above 0;
- the 2025–26 mean (FTMO) is above 0.

**Reported, not required:**
- 2010–2022 (before 0DTE). The mechanism predicts R2 and R3 are weaker there.
- Win rate on traded days.
- Weeks with at least 3 winning days.
- Winning months.
- Dollars per day for $10k at 1× and 4×.

**One run.** No setting changes after it. A candidate that fails is not retried with other
settings.

**Honest limits.**
- 2025–26 has already been used to judge S2, which these rules partly oppose. So the
  2023–24 condition and the t ≥ 2.5 bar carry the weight.
- Anything that passes then goes to the demo next to S2 before any money is at risk.
