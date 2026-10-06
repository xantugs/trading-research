# Brute-force ORB search: protocol (7 October 2026)

Written and committed **before** the search runs. A large grid always produces a
combination that looks excellent on the data it was chosen from; the question is whether
that choice carries over to data it was not chosen on. This protocol fixes the grid, the
split, the ranking and the verdict in advance. There is one round only: if no edge is
found, the grid is not widened until something passes.

## Data and split

FTMO-Demo `US100.cash`, `US500.cash` and `XAUUSD`, M1 bars from 2021-10-01 (the first
month of full FTMO index history) to each cache's end (6 October 2026 for the indices,
16 September 2026 for gold). Broker clock New York + 7 h.

- **Training:** trades entered before 2025-01-01 (New York time).
- **Test:** trades entered from 2025-01-01. Those years appeared in a handful of earlier
  runs, but not in this search, so the test is only partly out-of-sample.

## Grid (3,072 combinations per symbol, 9,216 in total)

| Dimension | Values |
|---|---|
| Range length from 09:30 New York | 5, 15, 30, 60 min |
| Entry | first close outside the range on 1, 5 or 15 min bars; or a stop order at the range edge |
| Target | 1.0, 1.5, 2.0, 3.0 × risk |
| Stop | far side of the range, or the range midpoint (`range_multiple` 0.5) |
| No new entries from | 11:00, 13:00, 15:30 New York |
| Direction | long and short, or long only |
| Exit | held to stop or target, or flat at 16:00 New York |
| Range filter | none, or 1.25–3 × ATR(1344) on range-timeframe bars |

Fixed for every combination:
- one trade per direction per session;
- each symbol's own spread and slippage, plus gold's modelled commission;
- 8% a year overnight financing, as an approximation of FTMO's swaps;
- position sizing that keeps R unaffected by the minimum lot.

## Ranking and verdict

R is each trade's net P/L ÷ its planned risk.

1. **Eligible:** at least 200 training trades and 80 test trades.
2. **Rank:** within each symbol, by the t-statistic of training mean R
   (mean ÷ standard deviation × √n).
3. **Report:** the top 10 per symbol on the test years.
4. **Diagnostics:**
   - Spearman rank correlation between training and test mean R across all eligible
     combinations, per symbol. Near zero means training rank does not predict test
     results.
   - The deflated Sharpe ratio (Bailey and López de Prado, 2014) of each symbol's
     best training combination. It uses the per-trade Sharpe, the number of eligible
     combinations tried across all symbols, and the variance of their training Sharpes.

**An edge is declared found** only if, for at least one symbol, all four hold:
- (a) the best training combination has test mean R > 0 with a test t-statistic ≥ 2.0;
- (b) the median test mean R of that symbol's top 10 is > 0;
- (c) the deflated Sharpe ratio of the best training combination is ≥ 0.95;
- (d) the training-to-test Spearman correlation is > 0.1.

Otherwise the result is "no edge found by brute force". The best combinations are then
reported as what they are: the luckiest of 9,216 tries.
