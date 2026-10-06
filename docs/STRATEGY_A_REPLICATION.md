# Strategy A replicated on XAGUSD — the frozen plan does not transfer

Run 18 September 2026. Pre-registered before the silver cache existed
(`trading/0918_silver_replication_preregistration.md` in the Tugsuu project):
one run, no tuning, and a declared reading rule. Strategy A fails it.

## What was held fixed

Everything except the instrument. The stored-clock `08:20` session with
`broker_clock: stored_as_utc` — the legacy interpretation, i.e. the real window
Strategy A actually traded — 60-minute range, M5 close confirmation, first
breakout only, opposite-range stop, 2R target, 16:00 exit label, 0.5% risk,
`sl_first`, buffer family 0.25/0.35/0.45/0.55, and the same four annual folds
with selection on training years only. `validate_frozen_config(...,
expected_symbol="XAGUSD")` refuses any other change; a test covers that.

Only two things moved, neither by choice. The contract specification came from
the broker (point 0.001, contract 5000, lot 0.01/0.01/100, tick value 5.0,
cross-checked as contract x tick size). And the two price-unit cost parameters
were scaled by the pre-registered ratio: silver's median recorded development
spread over gold's 0.15 price units, giving a ratio near 0.11, so slippage
0.02 -> 0.002 and fallback spread 0.20 -> 0.023. The same script returns a
ratio of exactly 1.0000 on gold, leaving its values untouched.

Sizing was the $100,000 research convention, so the comparison is against
Strategy A's $100k sensitivity (387 trades, +0.0291 mean R, PF 1.157, +5.50%),
not its published $5,000 figure.

## Result

| Train | Test | Buffer | Train mean R | Trades | Test mean R | PF | Return | Max closed DD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2018–2020 | 2021 | 0.45 | -0.1022 | 90 | -0.0287 | 0.851 | -1.25% | 2.38% |
| 2019–2021 | 2022 | 0.55 | -0.0205 | 76 | +0.0022 | 1.007 | +0.04% | 2.56% |
| 2020–2022 | 2023 | 0.55 | +0.0073 | 87 | -0.0422 | 0.803 | -1.77% | 3.71% |
| 2021–2023 | 2024 | 0.55 | -0.0199 | 77 | -0.0010 | 0.991 | -0.06% | 1.68% |

Combined unseen 2021–2024: 330 trades, mean net R **-0.0187**, median R
-0.0228, profit factor 0.899, return **-3.02%**, maximum closed drawdown 3.82%,
one nominally positive year of four (2022 at +0.04%). Session-block bootstrap
(4,000 draws): 95% interval [-0.0708, +0.0333], P(mean R <= 0) = 0.767.

Pre-registered rule: **fails** (mean R <= 0). It also misses the >= 3/4
positive-years condition. No ambiguity to argue about.

## The training surface is the real finding

Mean net R for all sixteen training cells:

| Fold | 0.25 | 0.35 | 0.45 | 0.55 |
| ---: | ---: | ---: | ---: | ---: |
| 1 (2018–20) | -0.2080 | -0.1382 | -0.1022 | -0.1031 |
| 2 (2019–21) | -0.1519 | -0.0689 | -0.0378 | -0.0205 |
| 3 (2020–22) | -0.0932 | -0.0443 | -0.0180 | +0.0073 |
| 4 (2021–23) | -0.0495 | -0.0315 | -0.0250 | -0.0199 |

Fifteen of sixteen are negative. On gold the selected training cells were
+0.070, +0.049, +0.044 and +0.041. So selection did not pick badly on silver;
there was nothing to pick. The structure is absent in-sample as well as
out-of-sample, which is a cleaner negative than a failed transfer would be.

One thing does replicate directionally: within every fold, expectancy improves
monotonically as the buffer rises, exactly as on gold. The ATR buffer really
does filter weak breakouts. On silver it never lifts the result above zero.

## Why it degenerates

Silver's opening range is far wider relative to price: median stop distance
1.945% of price against 0.22–0.58% for Strategy A on gold. With the stop at
the opposite side of that range, almost nothing is ever touched — 313 of 330
exits are the session close, 16 are stops, and exactly one trade in 330 reached
the 2R target. On silver the specification stops being a breakout strategy with
a stop and a target and becomes a directional hold to the close.

That is a property of the frozen specification meeting a different range
geometry, not proof that no silver session has structure. An ATR-normalised
stop would be a different strategy, and inventing one now, after seeing this
result, is the rescue move the pre-registration exists to prevent. If anyone
wants to test it, it needs its own pre-registration and its own data.

Long and short were both negative (-0.0143 and -0.0229) and their yearly signs
alternate at random, so gold's long/short asymmetry does not replicate either.

## Consequence

Per the pre-registration, Strategy A does not go to the 2025+ holdout, and the
ORB project closes on this data. The holdout stays sealed and unspent. No
2025+ outcome was read at any point; the silver cache was downloaded with
`--end 2025-01-01` and never contained the holdout.
