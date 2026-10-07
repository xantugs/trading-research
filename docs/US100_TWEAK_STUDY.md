# US100 candidate: tweak study with a re-sealed 2025–26 test (7 October 2026)

Written and committed **before** any tweak below was run.

**Aim.** Find out whether a small number of reasoned changes improve the frozen US100
candidate (docs/US100_CANDIDATE_PLAN.md). Every decision is made on 2021-10 to 2024
data only. Then the final rule set is tested **once** on 2025-01-01 to 2026-10-06.

**How 2025–26 stays sealed during development.** Every study config sets
`development_end: "2025-01-01"` with no `holdout_opened`, so `Config.validate` refuses
to load later bars. Only the single final run may read past that date.

**Honest limit.** 2025–26 is not untouched. The candidate's results there are already
known: 2025 at +0.150R, 2026 at −0.043R, +0.065R pooled over 483 trades. And the
candidate itself was chosen after seeing them. The final test is therefore partly
contaminated. It can still reject a tweak set, but a pass is weaker evidence than a true
forward test.

## Development data and periods

FTMO-Demo `US100.cash`, broker clock New York + 7 h, M1 bars from 2021-10-01.

- **Period A, fitting:** 2021-10-01 to 2023-12-31.
- **Period B, validation:** 2024.
- **Baseline:** the candidate unchanged.

## Tweaks: one fixed value each, chosen for a reason given in advance

None of these values comes from looking at 2025–26 results. Ideas that did (for example
the 2.0R target, which ranked best in the earlier check's 27 nearby settings) are
excluded.

| # | Tweak | Fixed value | Reason |
|---|---|---|---|
| T1 | Trend filter | Trade only in the direction of the prior day's close vs its 50-day simple average | Breakouts with the higher-timeframe trend should follow through more |
| T2 | Breakeven stop | Move the stop to the entry price once price reaches +1R | Cuts winners-turned-losers on held positions |
| T3 | Gap filter | Skip a session whose open gaps more than 0.5 × daily ATR(14) from the prior close | After a large gap, the opening range reflects the gap more than intent |
| T4 | Range-size filter | Range between 1.25 and 3 × ATR(1344) on 15-minute bars | The creator's live-bot filter |
| T5 | First breakout only | One trade per session, not one per direction | A breakout reversing the other way is a failed day, not a new signal |
| T6 | Flat at 16:00 New York | No overnight holding | Avoids gaps and financing |
| T7 | 5-minute entries | Entry closes on 5-minute bars | The video's main idea |
| T8 | Long only | No shorts | The index's long-term upward drift |

## Rules

1. **Adoption.** Each tweak is run alone against the baseline on periods A and B.
   It is adopted only if all three hold:
   - mean R beats the baseline in **both** A and B;
   - it keeps at least 50% of the baseline's trades in each period;
   - its mean R in B is above 0.
2. **Combination.** The adopted tweaks together form one final rule set, run once on
   A and B for the record. No combination is chosen by trying alternatives.
3. **Final test.** The final rule set is run **once** on 2025-01-01 to 2026-10-06. It
   passes only if its mean R is above 0 **and** above the baseline's +0.065R on the
   same dates.
4. **If nothing is adopted,** there is no final test, and the candidate stays as frozen.
5. **One round only.** No new tweaks are added after seeing these results.
