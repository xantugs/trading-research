# S2 on FTMO: sizing under the 2-Step and 1-Step rules (8 October 2026)

Written before the sizing simulations were run. The strategy is S2 from
docs/INTRADAY_MOMENTUM_PLAN.md, unchanged. Only the position size is chosen here.

**Size.** Leverage L is the position's notional ÷ account equity, applied to every S2
trade. L is chosen from {0.5, 1, 1.5, 2, 2.5, 3, 4}.

## FTMO rules simulated

| Account | Profit target | Daily loss limit | Maximum loss | Other |
|---|---|---|---|---|
| 2-Step challenge | +10% (phase 1), then +5% (phase 2, a new account) | 5% of the day's starting balance | Equity at or below 90% of the initial balance | No time limit |
| 1-Step challenge | +10% | 3% of the day's starting balance | Equity at or below the highest day-start balance − 10% of initial (trails) | Best day ≤ 50% of positive days' profit; no time limit |
| 2-Step funded | — | 5% | Static 10% | One year simulated |
| 1-Step funded | — | 3% | Trailing 10%, if FTMO keeps it after funding (to be checked) | One year simulated |

Each limit is checked against **marked equity**: each day's lowest point, open positions
included, from minute closes (`worst` in `s2_noise_area`). The day's close is used for
targets. S2 is flat by 16:00 New York, so each trading day falls within one FTMO day
(which resets at midnight Prague time). The EA's guards are not modelled here.

## Return scenarios

The simulations bootstrap whole trading days, days without a trade included, 21 to a
month.

| Scenario | Data | Mean |
|---|---|---|
| **Development edge (conservative, used to choose)** | S2's daily results, HistData 2010-11 to 2017 | +1.8 bps/day at 1× |
| Holdout edge | HistData 2018–2024 plus FTMO 2025–26 | about +5 bps/day at 1× |
| Zero edge | Holdout days with their mean subtracted | 0 |

## Measures

- **Challenge:** first-try pass rate, average attempts, and median months to a funded
  account, with retries included (up to 10 attempts).
- **Funded:** the chance of losing the account within a year, and the median one-year
  return.

## How the sizes are chosen (fixed now)

- **Funded size:** the largest L whose chance of losing the funded account within a year
  is **≤ 10%** under the development edge. Chosen separately for 2-Step and 1-Step.
- **Fast challenge size:** the L with the shortest median months to funded under the
  development edge, provided it averages **≤ 3 attempts** under the development edge and
  **≤ 5** under zero edge.
- **Safe challenge size:** the L with the highest first-try pass rate under the
  development edge. Ties go to the larger L.

All three scenarios are reported for every L.

## Results (8 October 2026, `python -m scripts.s2_ftmo_sizing`)

1,500 challenge paths and 5,000 funded-year paths per cell. Full table in
`outputs/s2_sizing/results.csv`.

| L | Scenario | 2-Step pass 1st try | 2-Step attempts | 2-Step months to funded | 1-Step pass 1st try | 1-Step attempts | 1-Step months to funded | 2-Step funded: lose in 1y / median year | 1-Step funded: lose in 1y / median year |
|---|---|---|---|---|---|---|---|---|---|
| 0.5 | development | 0.92 | 1.09 | 68.9 | 0.91 | 1.09 | 41.8 | 0.00 / +2% | 0.00 / +2% |
| 1.0 | development | 0.84 | 1.20 | 31.1 | 0.69 | 1.46 | 17.6 | 0.03 / +4% | 0.18 / +4% |
| 1.5 | development | 0.63 | 1.60 | 19.0 | 0.60 | 1.68 | 10.0 | 0.21 / +7% | 0.46 / +5% |
| 2.0 | development | 0.60 | 1.67 | 13.1 | 0.47 | 2.18 | 6.6 | 0.28 / +8% | 0.84 / +4% |
| 2.5 | development | 0.52 | 1.93 | 9.3 | 0.41 | 2.39 | 4.7 | 0.45 / +8% | 0.96 / +3% |
| 3.0 | development | 0.47 | 2.13 | 7.3 | 0.35 | 2.91 | 3.7 | 0.56 / +7% | 1.00 / +3% |
| 4.0 | development | 0.37 | 2.72 | 5.1 | 0.27 | 3.58 | 2.6 | 0.89 / +5% | 1.00 / +2% |
| 1.0 | holdout | 0.96 | 1.04 | 12.7 | 0.77 | 1.30 | 7.7 | 0.02 / +13% | 0.30 / +12% |
| 2.5 | holdout | 0.48 | 2.08 | 5.1 | 0.39 | 2.71 | 2.8 | 0.82 / +14% | 1.00 / +4% |
| 3.0 | holdout | 0.44 | 2.28 | 4.4 | 0.30 | 3.20 | 2.4 | 0.89 / +11% | 1.00 / +3% |
| 1.0 | zero | 0.32 | 3.25 | 48.0 | 0.32 | 3.23 | 22.3 | 0.24 / −1% | 0.58 / 0% |
| 2.5 | zero | 0.18 | 4.74 | 11.5 | 0.19 | 4.63 | 5.1 | 0.92 / −1% | 1.00 / 0% |
| 3.0 | zero | 0.20 | 4.60 | 8.6 | 0.17 | 5.05 | 4.1 | 0.95 / −2% | 1.00 / 0% |

**Sizes chosen by the rules above** (`outputs/s2_sizing/chosen.json`):

| | 2-Step | 1-Step |
|---|---|---|
| Funded | **1.0×** | **0.5×** |
| Fast challenge | **3.0×** | **2.5×** |
| Safe challenge | 0.5× | 0.5× |

**Reading.**
- Only the fee is at risk in a challenge, so the fast sizes are aggressive. Once funded,
  size drops to the funded leverage.
- The rule's "safe" size is 0.5× for both. That is technically the highest first-try pass
  rate, but at 0.5× a pass takes years.
- The 1-Step funded account's 3% daily limit and trailing maximum loss make it fragile
  above 0.5×. One normal bad day for S2 at 1× is about −3% marked. So the 2-Step account
  is the better one to hold.
- Daily limits are checked on minute-close marked equity, not on ticks, so real breaches
  will be somewhat more frequent, most of all at the larger sizes.

## Lifecycle: net dollars per month (planned 8 October 2026, before running)

**Objective.** The most money, soonest, per $100k account, over the next 12 months
(252 trading days).

**The cycle.**
1. Pay the fee and run the challenge at leverage Lc. If it fails, pay again and restart.
2. Once funded, trade at leverage Lf.
3. Every 21 trading days, any profit above the initial balance is paid out at the
   profit split, and the balance resets to the initial balance.
4. The first payout of each funded account also refunds that account's fee.
5. If the funded account breaches a limit, pay the fee again and start a new challenge.

**Assumptions.**
- Fees: $499 for a 1-Step $100k, $540 for a 2-Step $100k.
- Splits: 90% on 1-Step, 80% on 2-Step (conservative; FTMO quotes 90% at $100k).
- After a payout, the 1-Step trailing maximum loss restarts from the initial balance.

**Grid.**
- 1-Step: Lc ∈ {1, 1.5, 2, 2.5, 3}, Lf ∈ {0.5, 1, 1.5, 2}.
- 2-Step: Lc ∈ {1, 2, 3, 4}, Lf ∈ {0.5, 1, 1.5, 2}.

1,000 paths per cell, under each of the three return scenarios.

**Choice (fixed now).** For each account type, the (Lc, Lf) with the highest mean net per
month under the **development** edge. The holdout and zero-edge results are reported next
to it, with the chance of finishing the 12 months below zero.

### Lifecycle results (`python -m scripts.s2_lifecycle`; full grid in `outputs/s2_sizing/lifecycle.csv`)

Per $100k account, averaged over the first 12 months. The months before the first payout
are included.

| Plan (chosen by rule) | Scenario | Mean net per month | Median net per month | Months to first payout | Below zero after 12 months |
|---|---|---|---|---|---|
| **1-Step, Lc 3×, Lf 2×** | development | **+$403** | +$264 | 5.7 | 33% |
| | holdout | +$900 | +$747 | 4.4 | 22% |
| | zero edge | +$23 | −$195 | 5.6 | 61% |
| **2-Step, Lc 4×, Lf 2×** | development | **+$330** | +$194 | 6.1 | 37% |
| | holdout | +$1,086 | +$960 | 4.5 | 16% |
| | zero edge | +$67 | −$180 | 5.9 | 62% |

**Reading.**
- Both winners sit at the top of the grid. A trader's losses are capped at the fees while
  payouts are not, so the most aggressive sizes score best on average, even with zero
  edge (+$23 to +$67 a month). That is the prop-firm payoff, not evidence of skill. With
  zero edge, the typical (median) year still loses about $2,200.
- The grid was not widened after these results. Larger sizes would lean even harder on
  the uncertain edge and on daily-limit breaches that minute bars understate.
- The funded sizes here (2×) are far above the "keep the account" sizes chosen earlier
  (1× and 0.5×). This plan expects to lose funded accounts and buy new challenges, and it
  only pays if the edge is real.

## Riskier grid for a $10k account (planned 8 October 2026, before running)

The user asked for more risk and can afford a $10k account. The same lifecycle is run with:
- **Fees:** $79 for a 1-Step and $89 for a 2-Step.
- **Grid:** 1-Step Lc ∈ {3, 4, 5, 6, 8}; 2-Step Lc ∈ {4, 5, 6, 8}; Lf ∈ {2, 3, 4, 5}.

The choice rule is unchanged: the highest mean net per month under the development edge,
reported with the holdout and zero-edge cases. At these sizes, one 0.4–0.6% adverse move
can breach a daily limit, so the minute-bar understatement of breaches matters more.
