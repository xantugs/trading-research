# Frozen US100 rules on other FTMO symbols (7 October 2026)

Written and committed **before** any symbol below was run.

**Aim.** Find out whether the frozen US100 candidate (docs/US100_CANDIDATE_PLAN.md) also
works on other US stocks and indices, using the same split the US100 deployment plan
uses: a backtest on 2021-10 to 2024 and a forward period on 2025-01-01 to 2026-10-06.

## Rules, unchanged from US100

New York 09:30 open, 15-minute range, the first 15-minute close outside it per
direction, no entries from 12:00, stop at the far side of the range, target 1.5R, held
until stop or target. Nothing is tuned per symbol.

**Costs.** Each symbol uses the broker's recorded M1 spread. Where a bar shows no spread,
the fallback is that symbol's median non-zero recorded spread between 09:30 and 12:00
New York in the backtest period. Slippage is 10% of that median per fill, at least one
tick; US100's 0.1-point slippage on a 1.5-point fallback spread is about the same ratio.
No commission is charged, as for the indices. This is an assumption: FTMO charges
commission on stock CFDs, so the stock results are optimistic, and the cost-stress run
below shows by how much.

## Symbols

All 46 FTMO-Demo `Equities I CFD` symbols (US-listed shares) and the US indices
`US30.cash`, `US500.cash` and `US2000.cash`; `US100.cash` is the source and is left out.
The list comes from the broker's symbol listing alone, before any result is seen.
A symbol is excluded, with the reason recorded, if its M1 history starts after
2023-01-31 or it has fewer than 100 backtest trades. A month in which more than 20% of
weekdays have no bars is accepted as a broker gap and listed.

`scripts/other_symbols_study.py` writes the configs, downloads the bars and runs the
study.

## Verdict per symbol, fixed in advance

A symbol **passes** only if both hold:

1. **Backtest (2021-10 to 2024):** mean R > 0 with a t-statistic ≥ 2.0.
2. **Forward (2025-01-01 to 2026-10-06):** mean R > 0.

With no edge anywhere, about 1% of symbols would pass both by chance (2.3% × 50%).
The report states how many passed against that chance level.

**Cost stress.** Every passing symbol is re-run once with twice the spread and slippage.
It is reported, not used to choose.

**One round only.** No rule changes, no per-symbol settings, and no second list after
seeing the results. A symbol that fails is not re-tried with other settings.

## Amendment 1 (7 October 2026, after the download, before any strategy result)

The cost rule above cannot be applied. For most stocks the broker's M1 spread field is
zero on every bar until 2026 (AMD and BA, for example). Where it is filled, it is far
lower before 2025 than after: AAPL shows 2 cents through 2024 and 10 cents from 2025.
So the rule now is: **every bar of every symbol is charged one constant spread, the
median non-zero recorded spread between 09:30 and 12:00 New York from 7 April to
6 October 2026**, the most recent six months. Recorded spreads are otherwise ignored.
Slippage stays at 10% of that spread per fill, at least one tick. This charges today's
costs to every year, which is conservative where earlier spreads were lower. A symbol
with no recorded spread in those six months is excluded. Nothing else changes.

`US30.cash` is excluded: the broker fails every M1 history request for it
("Terminal: Call failed"), so it has no data.

## Results (7 October 2026, `python -m scripts.other_symbols_study run`)

**Verdict: no symbol passes.** 43 symbols were tested and 6 excluded: ARM, GM, MCD and
SPCX (history starts after 2023-01), BRK.B (no trades) and US30.cash (no data). Chance
alone would give about 0.5 passes.

| | Trades | Mean R | t |
|---|---|---|---|
| All 43 symbols, backtest 2021-10 to 2024 | 28,288 | −0.251 | −32.8 |
| All 43 symbols, forward 2025-01 to 2026-10 | 6,959 | −0.229 | −15.3 |

Only 3 of 43 symbols have a positive backtest mean, none with t above 0.6: TSLA +0.033R
(575 trades), AMZN +0.019R (588) and US500 +0.019R (927). US2000 is −0.020R (834).
Most stocks lose 0.1 to 0.5R per trade, and the worst t-statistics are below −10
(KO, INTC, CSCO, PLTR). Per-symbol rows are in `outputs/symbols/results.json`.

**Why the stocks lose.** Today's spread on these CFDs is about $0.09 to $0.10 per share.
On a 15-minute opening range of a few tens of cents, that, plus slippage, is a large
fraction of the initial risk on every trade. The amended cost rule charges today's spread
to every year, so earlier years are charged more than they paid at the time. But costs
alone do not explain it: US500 and US2000 have small costs relative to their ranges and
are still flat.

**Forward-period gap.** From the second quarter of 2024, FTMO's US stock CFDs open at
09:35 New York, five minutes after the exchange. For KO, CSCO and similar stocks the
change came in January 2026. The 09:30–09:45 range then never forms, so the frozen rules
take no trades. As a result, AAPL, AMZN, GOOG, MSFT, META, NVDA, NFLX, TSLA and 10 others
have no forward trades. This does not affect the verdict, since every symbol already
fails the backtest condition. It does mean **the EA cannot trade these stock CFDs as
written**.

By the one-round rule, no symbol is re-tried with other settings, and no rule is adapted
to the 09:35 open.

## Addendum: US30 (7 October 2026)

A later download request for `US30.cash` succeeded: 1,754,025 M1 bars from 2021-10. The
earlier failure was temporary. US30 was run under the same rules and the same amended cost
rule, with a constant spread of 2.10. **It fails:** 2021-10 to 2024 is −0.050R (t −1.22,
862 trades), and 2025-01 to 2026-10 is +0.019R (474). The verdict stands: no symbol
passes.

Daily R correlation of the frozen rules, 2021-10 to 2026-10: US100–US500 0.59,
US100–US30 0.27, US30–US500 0.42.
