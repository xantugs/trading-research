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
