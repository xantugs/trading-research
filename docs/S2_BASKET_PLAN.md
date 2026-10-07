# S2 on a basket of markets: plan (8 October 2026)

Written and committed **before** S2 has been run on any market other than the Nasdaq-100.

**Aim.** Make S2's daily results steadier by trading it on several markets at once. The
goal is a positive edge with more winning days, more weeks with at least 3 winning days,
and more winning months than S2 on one market. The research basis is intraday momentum
found across 60+ futures markets (Baltussen et al., 2021) and in 16 equity markets out of
sample (Li, Sakkas & Urquhart, 2022). See the summary in this branch's chat record.

**S2 unchanged:**
- 14-day noise area;
- checkpoints every 30 minutes from the session open + 30 minutes to the close − 30
  minutes;
- flat at the session close;
- costs as before.

## Markets and sessions (local exchange time; fixed now)

| HistData | FTMO | Market | Session |
|---|---|---|---|
| NSXUSD | US100.cash | Nasdaq-100 | 09:30–16:00 New York |
| SPXUSD | US500.cash | S&P 500 | 09:30–16:00 New York |
| GRXEUR | GER40.cash | DAX 40 | 09:00–17:30 Berlin |
| UKXGBP | UK100.cash | FTSE 100 | 08:00–16:30 London |
| JPXJPY | JP225.cash | Nikkei 225 | 09:00–15:00 Tokyo |
| XAUUSD | XAUUSD | Gold (COMEX day session) | 08:20–13:30 New York |
| WTIUSD | USOIL.cash | WTI crude (NYMEX day session) | 09:00–14:30 New York |

**Clock check, before any strategy run.** HistData's Nasdaq stamps were New York wall time
with daylight saving, and every market is read the same way. A market is dropped if its
largest 1-minute volatility jump around the open is not within ±2 minutes of its session
open in both seasons, in most years from 2010 to 2024. This check uses absolute price
moves only.

**Costs.**
- **Spread:** for each market, FTMO's median recorded spread ÷ price, 2025-01 to 2026-10,
  inside the session, applied to every HistData bar's close.
- **Slippage:** 10% of the spread per fill.
- **Holdout B:** uses FTMO's recorded spreads.

## Periods

- **Development:** HistData, 2010-11 to 2017-12.
- **Holdout A:** HistData, 2018–2024.
- **Holdout B:** FTMO, 2025-01 to 2026-10.

Nasdaq-100's holdout A result is already known: it passed. It stays in the basket, so the
basket's holdout A is not fully unseen. Its six other markets are.

## Rules

1. **Market gate, on development only.** A market joins the basket if its development
   mean daily return is above 0.
2. **Weights.** Each basket market is weighted by 1 ÷ its development daily standard
   deviation, so each carries equal risk. The weights are scaled so that the basket's
   development daily standard deviation equals the Nasdaq-100's, which keeps the FTMO
   leverage tables comparable. The basket's daily return is the weighted sum; a market
   with no session that day contributes 0.
3. **Holdout A pass (basket):**
   - mean daily return above 0 with **t ≥ 2.3**;
   - at least 6 of the 7 years positive.
4. **Holdout B:** basket mean > 0 confirms. If it is below 0, the result is "not
   confirmed".
5. **Reported for the user's goal, not used to choose:**
   - winning days, as a share of days with a trade;
   - weeks with at least 3 winning days;
   - winning months;
   - dollars per day for a $10k account at 1×, 2× and 4×.

   Each is given for the basket and for each market.
6. **One run per holdout.** No market is added or removed, and no weight changed, after
   any holdout result.

## Amendment 1 (8 October 2026, after the clock check, before any strategy run)

The clock check's rule (the volatility jump within ±2 minutes of the session open, in at
least 70% of year-seasons) passed Nasdaq-100 (90%), DAX (97%) and WTI (74%; HistData has
no WTI file for 2024, so WTI's holdout A is 2018–2023). It dropped four markets:

| Market | Jump at the open | Where the other jumps fall |
|---|---|---|
| S&P 500 | 69% | mostly 10:00 New York |
| Gold | 10% | almost all 08:30 New York |
| FTSE 100 | 69% | scattered, 07:31–08:55 |
| Nikkei 225 | 7% | 08:30–08:45 Tokyo |

For S&P 500 and gold, the jumps sit on the scheduled US data releases (10:00 and 08:30 New
York). That points to news outranking the open, not a wrong clock. Both have FTMO data
overlapping HistData, so they were re-checked with the Nasdaq calibration test: 1-minute
price changes in session, correlated by lag, with no strategy returns.

| Overlap test | All | Summer | Winter | At ±1 or ±60 minutes |
|---|---|---|---|---|
| S&P 500 vs US500.cash, 2021-10 to 2024 | 0.893 | 0.895 | 0.889 | ≤ 0.10 |
| Gold vs FTMO XAUUSD, 2018 to 2024 | 0.886 | 0.870 | 0.921 | ≤ 0.04 |

Both peak at lag 0, so **S&P 500 and gold are reinstated**. FTSE 100 and Nikkei 225 have no
FTMO data before 2025 to check against, so they stay dropped. The basket candidates are
Nasdaq-100, S&P 500, DAX, gold and WTI. The development gate and weights are unchanged.

## Results (8 October 2026)

**Development (2010-11 to 2017).** Every market passed the gate, so all five are in the
basket.

| | Nasdaq-100 | S&P 500 | DAX | Gold | WTI | **Basket** |
|---|---|---|---|---|---|---|
| bps/day | +1.81 | +0.98 | +1.95 | +0.72 | +0.39 | **+1.89** |
| t | 1.79 | 1.10 | 1.51 | 0.86 | 0.20 | **2.05** |
| Sharpe | 0.69 | 0.43 | 0.57 | 0.31 | 0.08 | **0.72** |

The modelled FTMO spreads were 0.49, 0.77, 0.53, 0.80 and 5.00 bps respectively.

**Holdouts, run once.**

| | Nasdaq-100 | S&P 500 | DAX | Gold | WTI | **Basket** |
|---|---|---|---|---|---|---|
| A, 2018–2024: bps/day (t) | +5.89 (3.85) | +4.01 (3.29) | −0.52 (−0.42) | +0.63 (0.75) | −4.26 (−1.32) | **+2.84 (2.36)** |
| A: winning days / winning months | 45% / 67% | 46% / 62% | 37% / 53% | 40% / 47% | 35% / 34% | **44% / 62%** |
| B, FTMO 2025-01 to 2026-10: bps/day (t) | +2.66 (0.88) | −1.15 (−0.61) | +1.08 (0.53) | −1.60 (−0.69) | −10.87 (−2.71) | **−2.12 (−0.85)** |

Basket by year, A: 2018 +9.9, 2019 −0.8, 2020 +1.3, 2021 +0.5, 2022 +6.9, 2023 +0.3,
2024 +0.9 bps/day. B: 2025 +1.0, 2026 −6.1.

**Verdict: "pass A, not confirmed by B".** Holdout A passes (t 2.36, 6 of 7 years
positive), but the basket loses on FTMO 2025–26. The basket is also weaker than the
Nasdaq-100 alone in both holdouts, because its weaker markets (WTI, gold, DAX) dilute the
edge rather than smoothing it. WTI, the market with the widest spread, is the worst.

**Against the user's goal.** The basket's winning days stay at 35–44% of days traded, and
weeks with three or more winning days at 25–32%. Diversifying S2 does not make it a
"3 days out of 5" strategy.

By rule 6, nothing is re-weighted or dropped after these results. S2 on the Nasdaq-100
(docs/INTRADAY_MOMENTUM_PLAN.md) stays the supported strategy.
