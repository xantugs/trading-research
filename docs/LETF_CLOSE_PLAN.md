# Leveraged-ETF close momentum on US100 and US500: plan (8 October 2026)

Written and committed **before** the rule below has been run.

**Mechanism.** Leveraged and inverse index ETFs must rebalance near the close every day.
They buy after an up day and sell after a down day, in proportion to the day's
close-to-close move. Their daily rebalancing flows reached a record of about $50 billion
in 2026 (Benzinga and Bloomberg, July 2026), and research links these flows to returns in
the last 30 minutes, although a 2024 survey doubts the size of the effect. If the
mechanism is real, the last minutes of a big-move day should continue in the day's
direction, and should do so more strongly in recent years.

## Rule L1 (New York time; the price at a time is the close of the minute bar ending then)

- **Day move:** r = price at 15:45 ÷ the previous session's 16:00 close − 1.
- **Big day:** |r| is above the average |r| at 15:45 over the previous 20 sessions (at least
  15 of them).
- **Trade:** on a big day, go in r's direction at 15:45 and exit at 16:00. One trade a day,
  no stop.
- **Costs as before:** the recorded FTMO spread (or the HistData spread model), and slippage
  of 10% of the spread per fill.

## Tests: one rule × two markets

- **Primary: FTMO data, 2021-10-01 to 2026-10-06**, the leveraged-ETF boom.
  - US100: `US100.cash`.
  - US500: `US500.cash`.

  A market **passes** if all three hold:
  - mean daily return above 0 with **t ≥ 2.3**;
  - a positive mean in 2021-10 to 2023;
  - a positive mean in 2024 to 2026-10.
- **Context, reported only:**
  - HistData 2010-11 to 2020, smaller leveraged ETFs (the mechanism predicts a weaker
    effect);
  - win rate on days traded;
  - winning months;
  - dollars per day for $10k at 1× and 4×.

**One run.** No variant (other times, thresholds or exits) is tried after it.

**Honest limit.** The late-day reversal test (docs/REGIME_STRATEGIES_PLAN.md, R3) faded
large open-to-15:00 moves from 15:00 to 16:00 and lost money in 2023–26. That is partial
knowledge that late-day moves continued on such days. L1 differs in its signal (move from
the previous close), its window (the last 15 minutes) and its threshold, but it is not
fully independent of what we have already seen.

## Results (8 October 2026, `python -m scripts.letf_close_study`, run once)

| | FTMO 2021-10 to 2026-10 | 2021-10 to 2023 | 2024 to 2026-10 | HistData 2010-11 to 2020 (context) |
|---|---|---|---|---|
| US100: bps/day (t), win rate on days traded | −0.46 (−0.94), 46% | +0.86 (1.07), 49% | **−1.41 (−2.34)**, 43% | +0.23 (0.64), 47% |
| US500: bps/day (t), win rate on days traded | −0.74 (−1.71), 44% | +0.28 (0.39), 50% | **−1.48 (−2.77)**, 40% | +0.56 (1.74), 51% |

**Verdict: both fail.** Big-move days traded about 43% of the time. The mechanism predicts
the effect is strongest now, but in 2024–26, the record-flow period, the last 15 minutes
of big days **reversed** significantly on both indices. One reading is that traders now
front-run the predictable rebalancing flow before 15:45 and the move gives back into the
close. This study cannot tell. The fade of the same window is not tested here, because
choosing it after seeing these results would be choosing on the data. It could only be
judged on new data, such as a forward demo.
