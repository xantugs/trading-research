# Idea B: intraday chart CNNs on US stocks (DRAFT, 9 October 2026, not yet pre-registered)

**Question.** JKX (replicated, Gates 1–3 passed: docs/JKX_REPLICATION_PLAN.md) show that a CNN
reading *daily* chart images predicts the cross-section of next-week returns, and that the
signal survives publication.
- **What we ask:** does the same idea work *within the day*? Do charts of 5-minute bars
  predict which stocks rise over the rest of the day?

**Why it is new.** The literature check of 9 October 2026 found:
- daily-image studies;
- single-stock intraday CNNs;
- non-image intraday models.

It found no cross-sectional intraday chart CNN with JKX-style portfolios.

**Why it might work.** Intraday price paths carry trading pressure that daily bars average
away:
- Opening drives, failed breakouts and lunchtime reversals.
- Intraday momentum, the first half hour predicting the last (Gao, Han, Li & Zhou 2018). Our
  S2 study found a related noise-area version.
- Closing flows from hedgers and leveraged ETFs (Baltussen et al. 2021).

A CNN may find shapes that the hand-made rules miss.

## Feasibility (measured 9 October 2026 on WRDS Cloud)

- **TAQ access.** This subscription reads TAQ millisecond trades for **2012-01-03 to
  2024-12-31** only. 2003�2011 and 2025�2026 return "permission denied".
- **Speed.** Server-side 5-minute bars for ~500 stocks take **25�60 s per day**. The
  extraction runs as 156 monthly SGE jobs, 4 at a time (`scripts/wrds_cloud/`): about 8�12
  hours, submitted as job 40400561.
- **Universe.** `crsp.dsp500list` gives point-in-time membership (505 names on 2019-03-01).
  `wrdsapps_link_crsp_taqm.taqmclink` misses about 15% of members, mostly
  foreign-incorporated (ETN, LYB, SLB�). Those fall back to the CRSP ticker that day.
- **Test day 2019-03-01.**
  - 501 of 505 members got bars, 78 bars each (09:30�15:55).
  - **The last 5-minute close is within 0.5% of the CRSP close for 501/501**, including the
    70 ticker-matched names.
- **A bug caught by the test.** The Postgres `::int` cast rounds, which shifted trades into
  the next bar and created a phantom bar 78. Bucketing now uses `floor()`.

## Data

- **Source:** WRDS TAQ millisecond trades, aggregated on WRDS's side into 5-minute bars
  (open, high, low, close, volume, trade count) for 09:30–16:00 New York.
- **Universe:** S&P 500 members as of each date (point in time, from the CRSP or Compustat
  constituents), so there is no survivorship bias. These names are liquid, and 46 of them are
  FTMO stock CFDs.
- **Years:** as far back as TAQ millisecond allows (2003 or later), ideally through 2025.
- **Linking:** CRSP returns and corporate actions join through the CRSP–TAQ link table, for
  adjustments and delisting.
- **Gate D (data):** a clock check and a cross-check against CRSP before any modelling.
  - 5-minute closes at 16:00 must match the CRSP daily close on ≥ 99% of stock-days.
  - Volume sums must be within 5%.
  - Bar timestamps must pass the session-open volatility check (the HistData lesson).

## Design (fixed before modelling; candidates to settle with the probe)

- **Image:** the previous trading day plus today up to the decision time, in 5-minute
  bars:
  - OHLC bars, a volume panel and a VWAP line;
  - the JKX conventions: 3 pixels per bar, scaling by the image's own high and low.
- **Decision times:** 10:30, 12:00 and 14:00 New York, one model each, all reported. 12:00 is
  the primary.
- **Labels and returns:**
  - the label is the sign of the return from the decision time to the 16:00 close;
  - the portfolio return is the same interval, at the next bar's open, no earlier;
  - the price at the decision time is the close of the bar ENDING then (the ML_PLAN
    look-ahead lesson).
- **Model:** the JKX CNN family, adapted to the wider image, with a five-seed ensemble and no
  hyperparameter search beyond a nested validation split.
- **Baselines (mandatory):**
  - intraday momentum (first half hour predicting the rest);
  - same-day reversal from the open;
  - the S2 noise-area signal applied per stock;
  - 1-week reversal;
  - logistic regression on simple path features.
- **Portfolios:** deciles on predicted probability at each decision time, equal and value
  weight, held to the close.
  - Report gross and net of a cost of 2 bps per side for S&P 500 names (a stated
    assumption).
  - Report turnover, and the top-100 by size as the "tradeable on FTMO" proxy.

## Splits and vault

- **Development:** the first available year to 2016. Train on the earlier part, with purged
  walk-forward validation inside.
- **Test:** 2017–2021, evaluated once after development is frozen.
- **Sealed, in vault.json:** 2022–2025, opened once after the test passes, by a committed
  plan.
- **2026:** a live paper forward test.

## Pass conditions (to be fixed before modelling)

- **Primary:** on the 12:00 model's test sample, the equal-weight H-L t (NW) must be at least
  3.0 after costs, and the top-100 value-weight H-L Sharpe must be above 0.
- The CNN must also beat the best baseline's H-L Sharpe by at least 0.3.

## Compute and budget

- **WRDS side:** about 4,000 trading days × 500 symbols. Feasibility depends on the
  per-day aggregation time measured by the probe.
- **Images:** about 500 × 252 × 20 years × 3 decision times ≈ 7.5 million.
- **GPU:** CNN training on a rented RTX 4090/5090, estimated at ¥20–40. That fits inside the
  remaining ~¥100 of the ¥120 cap.
