# Idea B: intraday chart CNNs on US stocks (PRE-REGISTERED 10 October 2026)

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
  2024-12-31** only. 2003–2011 and 2025–2026 return "permission denied".
- **Speed.** Server-side 5-minute bars for ~500 stocks take **25–60 s per day**. The
  extraction runs as 156 monthly SGE jobs, 4 at a time (`scripts/wrds_cloud/`): about 8–12
  hours, submitted as job 40400561.
- **Universe.** `crsp.dsp500list` gives point-in-time membership (505 names on 2019-03-01).
  `wrdsapps_link_crsp_taqm.taqmclink` misses about 15% of members, mostly
  foreign-incorporated (ETN, LYB, SLB…). Those fall back to the CRSP ticker that day.
- **Test day 2019-03-01.**
  - 501 of 505 members got bars, 78 bars each (09:30–15:55).
  - **The last 5-minute close is within 0.5% of the CRSP close for 501/501**, including the
    70 ticker-matched names.
- **A bug caught by the test.** The Postgres `::int` cast rounds, which shifted trades into
  the next bar and created a phantom bar 78. Bucketing now uses `floor()`.

## Pre-registration (frozen 10 October 2026, before any model or baseline result)

Everything below is fixed. Changes after this commit are amendments: dated, justified and made
before the result they could affect is seen.

### Data and universe
- **Bars:** TAQ 5-minute bars (Gate D passed) for S&P 500 members on each day (`crsp.dsp500list`,
  point in time), read through `orb_research/taq_bars.py`.
- **Daily data:** official closes, opens, returns and the previous day's market cap from CRSP
  daily.
- **Bar timing:** bar b covers [09:30 + 5b, 09:35 + 5b) New York, and **its close is known at
  09:35 + 5b**.

### Decision times and samples
- **Decision times T:** 10:30, **12:00 (primary)** and 14:00 New York.
- **Today's bars:** those ending at or before T. That is b = 0..11 (12 bars) at 10:30, 0..29
  (30) at 12:00, and 0..53 (54) at 14:00.
- **Eligibility.** A stock-day qualifies at T if:
  - it is a member that day;
  - it has a bar for at least 90% of the window's bars (yesterday's 78 plus today's up to T);
  - it has a bar ending exactly at T;
  - CRSP has today's close and yesterday's market cap.

### Image
- **Window:** yesterday's 78 bars, then today's bars up to T, side by side with no gap column
  (90, 108 and 132 bars).
- **Drawing, as in JKX:**
  - 3 pixels per bar: open tick, high-low bar, close tick;
  - **height 64**: price area 52 pixels, volume area 12;
  - price scaling uses the window's min and max of high, low, open, close and VWAP;
  - missing bars stay blank.
- **The VWAP line replaces the moving average.** It is the running volume-weighted price within
  each day, restarting at each day's open, drawn through the centre columns.
- **Volume bars** are scaled to the window's maximum.

### Label and returns
- **Entry:** the open of the first bar after T (bar index n_T), the first trade after the
  decision. **Exit:** the official CRSP close (a market-on-close order).
- **Return:** r = CRSP close / entry − 1.
- **Label:** y = 1 if r is above that day's cross-sectional median among eligible stocks at
  that T, else 0.

### Model
- **Architecture:** the JKX 20-day CNN: 3 blocks of 5×3 conv, batch norm, leaky ReLU and 2×1
  max-pool, with 64/128/256 filters. The first layer has vertical stride 3 and dilation 2.
  Dropout is 50% before a 2-output linear layer.
- **Training:** Adam with learning rate 1e-5, batch 128, Xavier initialisation, pixels
  standardised with training statistics.
- **Ensemble:** 5 seeds, probabilities averaged.
- **One model per decision time, with no hyperparameter search.**

### Splits
- **Train:** 2012–2016.
- **Validation (early stopping only):** 2017. Training stops after 2 epochs without a lower
  validation loss, and the best epoch is kept.
- **Test:** 2018–2020, evaluated **once**, after the models are frozen.
- **Sealed:** 2021–2024 (`vault.json`: `intraday_b_sealed`). Opened once, only if the test
  passes, by a committed amendment, with the same frozen models.

### Portfolios and costs
- **Construction:** each day at T, deciles on the score (decile 10 = most likely to beat the
  median). H-L = decile 10 − decile 1, held to the close.
- **Weights:** equal weight, and value weight by the previous day's market cap. The **top
  100** version keeps the 100 largest by the previous day's market cap.
- **Costs:** 2 bps per side per leg, so **H-L net = H-L gross − 8 bps per day** (both legs
  bought and sold daily). Gross and net are both reported.
- **Statistics:** Sharpe ratios annualised by √252; Newey–West t with 5 lags.

### Baselines (same eligibility, same portfolios and costs; each score is known at T)
1. **Intraday momentum:** the return from yesterday's CRSP close to the close of the bar
   ending at T.
2. **Same-day reversal:** −(the return from today's first bar open to the close of the bar
   ending at T).
3. **S2 per stock:** today's move from the open to T, divided by the stock's 14-day average
   |move from the open to the same bar|.
4. **1-week reversal:** −(the 5-day CRSP return to yesterday's close).
5. **Intraday periodicity (Heston, Korajczyk & Sadka 2010):** the stock's mean return from the
   same T to the close over the previous 5 days.
6. **Logistic regression** on 1–5 plus the realised volatility so far today, trained on
   2012–2016.

### Gate B1: label and portfolio integrity (before any model is trained)
On 2012–2017:
1. **Entry prices:** the first bar's open is within 0.5% of the CRSP open on ≥ 99% of
   stock-days.
2. **Random score:** a random score gives an H-L gross t with |t| < 3.
3. **Perfect foresight:** a perfect-foresight score gives perfectly monotone deciles.

Failure stops the study until the code is fixed.

### Pass condition (12:00 model, test 2018–2020); all three required
1. Equal-weight H-L **net** NW t ≥ 3.0.
2. Top-100 value-weight H-L **net** Sharpe > 0.
3. The CNN's equal-weight H-L net Sharpe exceeds the best baseline's by ≥ 0.3.

The 10:30 and 14:00 models are reported but not gated. A failure closes idea B with a full
write-up, and no rescue search.

## Compute and budget

- **WRDS side:** about 4,000 trading days × 500 symbols. Feasibility depends on the
  per-day aggregation time measured by the probe.
- **Images:** about 500 × 252 × 20 years × 3 decision times ≈ 7.5 million.
- **GPU:** CNN training on a rented RTX 4090/5090, estimated at ¥20–40. That fits inside the
  remaining ~¥100 of the ¥120 cap.

## Gate D: PASSED (10 October 2026), open years 2012–2020

Data: 156 monthly files (3.1 GB, 128 M bars). SHA-256 verified after download.
`scripts/taq_gate_d.py`, results in `outputs/taq_b/gate_d.csv`. 2021–2024 are sealed in
`vault.json` (`intraday_b_sealed`) and were not read.

| Check | Result |
|---|---|
| Stock-days matched to CRSP | 100% every year (≈125k a year) |
| Last bar's close within 0.5% of the CRSP close | **99.72–99.93%** a year (≥ 99% required); median gap 1.1–2.2 bps |
| Within 0.1% | 93.6–98.6% |
| Full 78-bar days | 97.5–99.4% |
| Clock: the open bar has the largest mean \|5-minute return\| | rank 1 in every year |
| Bar volume / CRSP daily volume, median | 0.94 (2012) falling to 0.85 (2019–20) |

**Why the CRSP close differs slightly.** The CRSP close is the 16:00 closing-auction price.
Our last bar ends at 16:00 and excludes the auction, by design: the auction price is not
known before 16:00.

**Why the volume share falls.** The excluded closing auction grew from about 5% to over 10%
of daily volume over 2012–2020. This is a known market trend, not a data fault.

**Other changes the same day.**
- **The JKX seal is retired.** `jkx_post_publication` (CRSP 2020+) is marked retired: Gate 3
  ran once and is recorded. Retired ranges stay in `vault.json` but no longer block reads.
- **New loader.** `orb_research/taq_bars.py` loads bars through the vault. A bar's close
  is known at its END (09:35 + 5b).
