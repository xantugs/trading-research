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
