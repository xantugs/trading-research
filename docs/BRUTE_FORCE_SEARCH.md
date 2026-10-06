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

## Results (7 October 2026)

`python -m scripts.brute_force_search --workers 12`, run once after the protocol above was
committed (`a05aae9`). 9,216 combinations ran (3,072 per symbol); 8,736 met the trade-count
minimums. Output: `outputs/search/20261007_brute_force/` (`results_*.csv`, `verdict.json`).

**Verdict: no edge found.** No symbol meets all four conditions; no symbol meets condition (a)
or (c).

| | US100 | US500 | XAUUSD |
|---|---:|---:|---:|
| Eligible combinations | 2,816 | 2,992 | 2,928 |
| Share profitable in training (2021-10 to 2024) | 77.5% | 55.1% | 37.8% |
| Share profitable in test (2025 to Sep/Oct 2026) | 34.5% | 18.2% | 50.6% |
| Spearman, training vs test mean R | −0.146 | +0.231 | +0.249 |
| Best training combination: train → test mean R | +0.119 → −0.011 | +0.221 → −0.038 | +0.116 → −0.040 |
| Deflated Sharpe ratio of that combination | 0.004 | 0.011 | 0.002 |
| Median test mean R of the top 10 | −0.011 | +0.034 | −0.027 |
| (a) best test > 0, t ≥ 2 / (b) top-10 median > 0 / (c) DSR ≥ 0.95 / (d) Spearman > 0.1 | ✗ ✗ ✗ ✗ | ✗ ✓ ✗ ✓ | ✗ ✗ ✗ ✓ |

Test mean R by training-rank decile (worst to best):
- **US100:** −0.055, −0.017, −0.014, −0.034, −0.034, −0.027, −0.038, −0.047, −0.049, −0.040. Every decile loses in the test, and the top decile does no better than the middle.
- **US500:** −0.132, −0.051, −0.047, −0.048, −0.056, −0.074, −0.059, −0.054, −0.044, −0.024. Rank carries some information, but even the best decile loses.
- **XAUUSD:** −0.073, −0.009, 0.004, 0.010, 0.006, 0.005, 0.011, 0.020, 0.025, 0.053. The one clear monotone pattern; its top decile averages +0.053R in the test. But no single choice from it survives the correction for 8,736 tries.

**Reading.** What wins in training is mostly the luck of that period:
- **US100:** the training winners are all 60-minute-range, long-only stop orders that bought
  2021–24's rally. They break even in 2025–26.
- **Best test results:** these fit the noise in the other direction. The best US100 test
  combination (15 min, 5-min close entry, 1.5R, long; +0.31R) lost money in training
  (−0.009R).
- **Cost of trying everything:** a deflated Sharpe near zero for every symbol means the best
  of ~8,700 tries is what chance alone would produce.
- **Gold:** the decile pattern is the only hint of persistent structure. The protocol allows
  no second round, so it is recorded here, not pursued.

The frozen US100 candidate (docs/US100_CANDIDATE_PLAN.md) is outside this grid: its 12:00
cutoff is not among the grid's cutoffs, and it has no financing. Its status is unchanged:
robustness passed, forward test pending, 2026 negative.
