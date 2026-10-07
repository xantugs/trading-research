# Pre-2021 Nasdaq-100 holdout: plan (8 October 2026)

Written and committed **before** any strategy has been run on the pre-October-2021 data.
Every FTMO bar from October 2021 on has been used in development, so this is the only
untouched historical test left.

**Aim.**
1. Give the frozen US100 candidate (docs/US100_CANDIDATE_PLAN.md) a real out-of-sample
   test.
2. Allow one openly declared development round on 2021-10 to 2026-10. Its targets are
   2026's losing months and a quicker strategy. Each finalist is then tested once on the
   holdout.

## Data

- **Holdout:** HistData.com `NSXUSD`, a Nasdaq-100 CFD quote, as M1 bid bars from
  2010-01-01 to 2021-09-30 UTC. HistData stamps bars in EST without daylight saving
  (UTC−5); `scripts/download_histdata_m1.py` converts them to UTC. The file records no
  spread and no volume. Dukascopy was the first choice but is unreachable from this
  network.
- **Development:** FTMO-Demo `US100.cash`, 2021-10-01 to 2026-10-06, as used so far.
- **Overlap for calibration:** HistData and FTMO, 2021-10-01 to 2024-12-31.

**Costs on HistData.** Every bar is charged a spread equal to FTMO's median recorded
US100 spread as a fraction of price. That median is taken from 09:30–12:00 New York,
2021-10 to 2024, and multiplied by the bar's close. Slippage is 10% of that spread per
fill. Nothing else differs from the FTMO configuration.

**Access rule.** Until the final runs below, no strategy run reads a HistData bar before
2021-10-01. Data checks such as bar counts and gaps may read all years, but compute no
returns.

## Calibration (overlap only; both must pass, or the study stops)

- **C1, clock.** The correlation of 1-minute close-to-close returns between HistData and
  FTMO, 09:30–16:00 New York, is highest at lag 0 among lags of −90 to +90 minutes, and
  at least 0.8.
- **C2, same trades.** The frozen candidate is run on both feeds. At least 80% of FTMO's
  trades have a HistData trade on the same date and in the same direction. The per-trade R
  correlation and both mean R values are reported.

## Tests

**H0: frozen candidate on the holdout, unchanged.**
- Pass: mean R > 0 with t ≥ 2.0.
- Also: at least 7 of the 11 full years from 2010 to 2020 are positive.

**Development round on FTMO 2021-10 to 2026-10.** Three families, each with its settings
fixed here:

| Family | Aim | Settings tried |
|---|---|---|
| D1 range-size filter | 2026's wide ranges (median 130 points, against 60–95 before) | `or_max_atr` ∈ {2.0, 2.5, 3.0} × ATR(1344) of 15-minute bars |
| D2 trend filter | 2026's losing longs | `trend_sma_days` ∈ {20, 50, 100} |
| D3 quicker | more trades per month | `signal_minutes` = 5; `trades_per_session` = `after_close`; both together |

In each family, the setting with the best development mean R is the finalist, provided it
keeps at least 60% of the baseline's trades (D3 has no such floor). Nothing outside this
table is tried.

**Holdout tests of the finalists:** at most three, each run once.
- A finalist passes only if its holdout mean R is above 0 with **t ≥ 2.5**, and above
  H0's holdout mean R. The stricter t-statistic allows for testing three finalists.
- Each finalist's trades per month and its FTMO 1-Step (1% risk) median months to funded
  are reported. They are not used to choose.

**Outcomes.**
- If H0 passes and no finalist does, the frozen candidate stays, now with real
  out-of-sample support.
- If a finalist passes, it replaces the candidate for the forward test.
- If H0 fails, the candidate is not supported, whatever the finalists show.

**One round only.** No family, setting or rule is added after any holdout result is seen.

## Amendment 1 (8 October 2026, after calibration, before any holdout run)

The first calibration failed C1. The return correlation peaked at a 60-minute lag (0.62)
instead of 0 (0.38). Split by season, winter aligned at lag 0 (0.90) and US summer time at
−60 minutes (0.89). HistData's NSXUSD stamps are therefore New York wall-clock time with
daylight saving, not the fixed UTC−5 its documentation states. The converter now reads them
as `America/New_York`, and the cache was rebuilt from the same zips. This corrects the data
only; no strategy setting changed.

**Calibration after the fix: pass.**
- **C1:** correlation 0.894 at lag 0, and at most 0.06 at ±60 minutes.
- **C2:** 87.8% of FTMO's trades are matched, and the matched R values correlate at 0.949.
  Mean R on the overlap is +0.104 on HistData (829 trades), against +0.090 on FTMO (907).

**Clock check for the holdout years.** This uses absolute 1-minute price moves only, with
no strategy returns. For every year from 2010 to 2024, in both seasons, the largest
relative jump in volatility between 09:00 and 10:00 New York falls at 09:30, or at
09:28–09:29 in a few earlier years. There is never an hour's shift. HistData's coverage
starts on 2010-11-14, so 2010 has six weeks of data; the "7 of 11 positive years" rule
still counts it.
