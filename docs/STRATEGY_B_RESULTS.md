# True 08:20 New York XAUUSD ORB (Strategy B)

The FTMO-Demo M1 cache labels broker/server wall time as UTC. Six scheduled
08:30 Eastern releases, including winter, summer and US/EU DST mismatch weeks,
show volume bursts at 15:30–15:34 on the stored clock. Decoding the stored
time as New York + 7 hours places those releases at 08:30–08:34 New York;
Sunday evening opens and late-Friday closes also align. The verifier passed
on the 2018–2024 cache. The raw archive was not changed. The original
broker-clock-window strategy remains separately reproducible as Strategy A.

Strategy B uses an actual 08:20–09:20 America/New_York opening range, M5
close-confirmed first breakout, opposite-range stop, 2R target, 0.5% research
risk and a 16:00 New York exit. The four annual folds selected the ATR breakout
buffers 0.25, 0.25, 0.25 and 0.55 using only preceding training years.

| 2021–2024 modeled unseen sequence | B1: no filters | B2: frozen P60 timing + P20 OR width |
| --- | ---: | ---: |
| Trades | 381 | 249 |
| Mean net R | +0.0205 | +0.0038 |
| Profit factor | 1.088 | 1.010 |
| Return | +2.38% | +0.18% |
| Max closed-equity drawdown | 2.69% | 2.19% |
| Positive years | 2/4 | 2/4 |

B2 reused B1's selected buffer in each fold, isolating the two filters.
It kept 249 baseline sessions and removed 132; the removed trades averaged
+0.0520 R. The B2 minus B1 mean-R difference was −0.0167, with a
session-block bootstrap 95% interval of [−0.0576, +0.0218]. These filters
did not transfer favorably to the true New York session. They are retired for
Strategy B and were not used again.

The legacy Strategy A walk-forward reproduced exactly: 371 trades,
+0.0248 mean R, 1.149 profit factor, +3.82% return and 3.00% closed drawdown.
Its actual New York range was 06:20–07:20 EST or 05:20–06:20 EDT, with exit at
14:00 EST or 13:00 EDT. These are different session hypotheses; both remain
development-period results affected by prior selection. M1 execution still
uses modeled costs and the conservative stop-first convention. No 2025+
strategy outcome was accessed.

## Minimum-lot correction

The $5,000 balance censored the sample. The true 08:20-09:20 range spans the
08:30 release and the COMEX open, so the opposite-range stop is wide (median
about 0.79% of price), and any session whose stop exceeded roughly $25 per
ounce could not be sized at the 0.01-lot minimum and was dropped before its
outcome was recorded. Skips reached 31 of 54 signals in the 2024 fold. A
$100,000 research balance removes the censoring without changing the broker's
lot minimum, the risk fraction or any execution rule; $5,000 feasibility is
kept as a reported statistic.

| Variant | Sizing | Trades | Mean R | PF | Return | Max closed DD | Positive years |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B1 | $5k original | 381 | +0.0205 | 1.088 | +2.38% | 2.69% | 2/4 |
| B1 | $100k research | 358 | +0.0120 | 1.067 | +2.03% | 2.76% | 2/4 |
| B2 | $5k original | 249 | +0.0038 | 1.010 | +0.18% | 2.19% | 2/4 |
| B2 | $100k research | 230 | +0.0059 | 1.026 | +0.56% | 2.49% | 2/4 |

Removing the censoring moved the uncensored B1 result down, so the original
figure was flattered by the dropped trades. A separately labelled Strategy A
sensitivity at the same sizing gives 387 trades and +0.0291 mean R; it does not
replace the published $5,000 Strategy A result.

## Prespecified 4x4 opening-range sweep

The 60-minute range was itself chosen during Strategy A work on the wrong
clock window, so the single pre-registered extension was run: opening range in
15/30/45/60 minutes by breakout buffer in 0.25/0.35/0.45/0.55, selected inside
each training window only among cells with at least 100 training trades, ties
resolved toward the shorter range and lower buffer, then frozen for one unseen
year. No filters, no other change. Support was defined before the run as all
four of: positive combined mean R, session-block bootstrap P(mean R <= 0) below
0.10, at least three positive years, and the top three eligible training cells
inside one adjacent 2x2 block in every fold.

| Test | Selected | Train trades | Train mean R | Test trades | Test mean R | PF | Return |
| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2021 | 60m / 0.25 | 268 | -0.0003 | 90 | -0.0141 | 0.901 | -0.63% |
| 2022 | 15m / 0.25 | 432 | +0.0140 | 193 | +0.0391 | 1.133 | +3.63% |
| 2023 | 30m / 0.55 | 155 | +0.0293 | 81 | +0.0840 | 1.713 | +3.35% |
| 2024 | 30m / 0.55 | 208 | +0.0616 | 71 | -0.0178 | 0.904 | -0.62% |

Combined unseen 2021-2024: 435 trades, mean R +0.0272, median R -0.0185,
profit factor 1.127, return +5.76%, maximum closed drawdown 3.75%, drawdown
duration 301 calendar days, longest losing streak 8, two positive years of
four. Session-block bootstrap 95% interval [-0.0273, +0.0851] with
P(mean R <= 0) = 0.181. Long trades averaged +0.0600 and short trades -0.0052;
that asymmetry is recorded as a future hypothesis only, and gold's 2018-2024
drift is an obvious confound.

Three of the four support conditions failed: the bootstrap probability, the
count of positive years, and the plateau test, which failed in all four folds.
**The intended 08:20 New York XAUUSD ORB is unsupported by the 2018-2024
development data under the prespecified grid.** No rescue search follows, and
the grid is closed.

Two post-hoc descriptions, in `robustness.md` beside the run, explain why the
positive headline mean is not evidence. First, concentration: five trades of
435, each a 2R target hit, supply +9.95 R of the +11.82 R total, the other 430
average +0.0043 R, and the median trade loses. Second, the training surface is
an era effect rather than a parameter structure: the share of profitable cells
runs 0/16, 6/16, 11/16 and 16/16 across the four training windows, so the
argmax is mostly reading the era. Training level did not predict the test year
at all (correlation -0.12 across folds); the strongest training window observed,
2021-2023 with every cell positive, produced a negative 2024.

Separately from the statistical verdict, the session is impractical on a small
account: at $5,000 and 0.5% risk, 49% of the selected 2024 signals could not be
sized at the 0.01-lot minimum.

Reproduce with:

```powershell
python run_all.py strategy-b-sweep --config configs/strategy_b_0820.json \
  --research-sizing-dir outputs/strategy_b_research_sizing/<stamp> \
  --output-dir outputs/strategy_b_sweep/<stamp>
```

Reproduce the clock and frozen comparison with:

```powershell
python run_all.py verify-clock --config configs/strategy_b_0820.json
python run_all.py strategy-b --config configs/strategy_b_0820.json
```

The commands write detailed fold, trade, clock and comparison artifacts under
`outputs/strategy_b/` and `outputs/session_comparison.md` locally. The
scheduled release dates and 08:30 Eastern times are documented in the
BLS [2023](https://www.bls.gov/schedule/2023/home.htm) and
[2024](https://www.bls.gov/schedule/2024/) release calendars.
