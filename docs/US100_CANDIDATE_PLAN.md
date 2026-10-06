# US100 candidate: frozen rules and pre-declared checks (7 October 2026)

Written and committed **before** any of the checks below were run. The candidate was
chosen after seeing its result ($10k → $63,185, PF 1.09, 42.3% drawdown) among twelve
index runs. Every year from 2021-10 to 2026-10 has already been seen, so none of these
checks is out-of-sample. Their job is to find whether the result is fragile; only a
forward test can show whether it is real.

## Frozen candidate

`configs/indices/ftmo_us100_orb_framework_15_15.json`, unchanged:

- FTMO-Demo `US100.cash`, M1 bars 2021-10-01 to 2026-10-06, broker clock New York + 7 h.
- Range: 09:30–09:45 New York. Signal: the first 15-minute close outside it, once per
  direction per session, before 12:00 New York. Entry at the next bar's open.
- Stop at the far side of the range; target 1.5 × risk; held overnight until one is hit.
- 2% of equity per trade; spread and 0.10 slippage, no commission, no financing.

## Pre-declared checks and bars

Trade statistics are in R (net P/L ÷ planned risk), so the bars don't depend on compounding.

| # | Check | Bar to pass |
|---|---|---|
| C1 | Calendar years 2022–2026 (Q4 2021 is reported, not counted) | Mean R > 0 in at least 3 of the 5 years, and no single year supplies more than 60% of total net R |
| C2 | Longs and shorts separately | Reported only. The forward test keeps both directions whatever this shows |
| C3 | Neighbourhood: range 5/15/30 min × target 1.25/1.5/2.0R × cutoff 11:00/12:00/13:00 (27 cells, entries on 15 min) | Mean R > 0 in at least 19 of 27 cells, and the median cell's mean R at least half the candidate's |
| C4 | Costs: spread ×2 and slippage ×2, plus 10% a year overnight financing | Mean R > 0 under the combined stress |
| C5 | Walk-forward: training from 2021-10-01 up to each test year; pick the C3 cell with the best training mean R (ties go to the candidate); test it on 2023, 2024, 2025 and 2026 to date | Pooled test-year mean R > 0, and mean R > 0 in at least 3 of the 4 test years |

The candidate **passes robustness** only if C1, C3, C4 and C5 all pass. Failing any one
of them means it does not go forward as is. No rescue variant is chosen from these
results.

## Sizing for FTMO (step 4)

The FTMO 2-step rules as modelled:
- Phase 1 target +10%, then Phase 2 target +5%.
- 5% maximum daily loss, measured from each day's opening balance, with days resetting
  at midnight Prague time.
- 10% maximum total loss.
- At least 4 trading days per phase.

Risk levels tested: 0.25%, 0.5%, 0.75%, 1.0%, 1.5% and 2.0% per trade.

Two estimates:
1. **Historical replay** from every trade as a start date (`orb_research/prop_simulator.py`).
2. **Monte Carlo**: 10,000 paths that redraw whole trading days of the candidate's trades
   with replacement.

The reported figures are the probability of passing both phases, the probability of
breaching a loss limit first, and the median trades and calendar days to pass.

**Rule for the forward test's risk:** the highest level at which the Monte Carlo chance
of breaching any loss limit before passing is no more than 25%.

Known understatement: these replays book each trade's P/L on its exit day. FTMO counts
open losses against the daily limit too, so positions held overnight make real daily
breaches more likely than modelled.
