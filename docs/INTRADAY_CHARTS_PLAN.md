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

### Amendment 1 (10 October 2026, before any baseline or model result)

Gate B1's first run passed checks 2 and 3 but **failed check 1**. The day's first-bar open was
within 0.5% of the CRSP open on 97.61–99.24% of stock-days a year (≥ 99% required).

**Diagnosis.** The check compared the CRSP open, which is the listing exchange's opening-auction
price, with the *first trade* of the day. On volatile mornings, other venues print trades
seconds before the auction. Mismatches cluster on such days, for example 24 August 2015 (157
stocks). The official open lies inside the first bar's high–low range (±0.1%), or within 0.5%
of its open, on:

| Year | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 |
|---|---|---|---|---|---|---|
| Share of stock-days | 100.00% | 99.99% | 99.99% | 99.97% | 99.99% | 100.00% |
| Stock-days outside both (mostly single odd prints) | 4 | 12 | 14 | 32 | 12 | 2 |

The closes of those outlier stock-days still match CRSP (Gate D). The bar data is sound;
the check measured the wrong price.

**Changes, approved by the project owner:**
1. **Check 1 now reads:** the CRSP open lies inside the first bar's high–low range (±0.1%), or
   within 0.5% of its open, on ≥ 99% of stock-days, 2012–2017.
2. **"Today's open" in baselines 2 (same-day reversal) and 3 (S2 per stock)** is the official
   CRSP open, not the first trade. It is known by 09:30, so there is no look-ahead, and it is
   immune to pre-auction prints.

Images (which draw the actual trades), labels (which enter at the first trade after T),
models, splits and the pass condition are unchanged.

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

## Gate B1: PASSED after amendment 1 (10 October 2026)

1. **Official open** inside the first bar, or within 0.5% of its open: 99.97–100% of
   stock-days in every year, 2012–2017.
2. **Random score:** H-L gross t = 0.53.
3. **Perfect-foresight deciles:** −103, −49, −29, −15, −4, +8, +20, +34, +54, +113 bps a day,
   monotone.

**Samples (2012–2020):** about 122–126k per year and decision time, 8.5 GB of packed images.
3–6 days a year are excluded by the frozen eligibility rules: the day after each 13:00 half
day (yesterday's window has under 90% of its bars), and at 14:00 the half days themselves.

## Baselines on the development years (2012–2017; `outputs/intraday_b/baselines_dev_2012_2017.csv`)

**H-L, equal weight:** gross bps a day / gross Sharpe (t) / net Sharpe after 8 bps a day.

| Score | 10:30 | 12:00 | 14:00 |
|---|---|---|---|
| Same-day reversal | +4.0 / 1.42 (3.4) / −1.42 | +4.2 / 1.91 (4.7) / −1.70 | +4.6 / 2.93 (6.9) / −2.20 |
| Periodicity (HKS) | −0.2 / −0.09 / −2.92 | +1.9 / 1.03 (2.7) / −3.25 | +4.1 / 3.12 (7.8) / −3.01 |
| 1-week reversal | +0.8 / 0.27 / −2.35 | +0.1 / 0.06 / −3.42 | −0.5 / −0.31 / −5.36 |
| Intraday momentum | −5.0 / −1.58 / −4.10 | −4.8 / −2.02 / −5.41 | −5.0 / −3.16 / −8.18 |
| S2 per stock | −3.5 / −1.53 / −5.05 | −3.7 / −2.00 / −6.30 | −4.3 / −3.33 / −9.55 |
| Logistic regression (2017 only) | +5.4 / 2.19 / −1.09 | +1.9 / 0.92 / −2.95 | +6.3 / 4.92 / −1.36 |

**The logistic regression, value-weighted top 100, 2017:** net Sharpe −2.08 at 10:30, −1.92
at 12:00, **+0.41** at 14:00 (t 0.37, 249 days).

**Reading.**
- **Reversal, not momentum.** In single stocks, moves since the open (and since yesterday's
  close) *reverse* by the close. Momentum and S2 are negative. That is the opposite of the
  index-level momentum S2 trades.
- **Periodicity strengthens late in the day,** as published.
- **Costs dominate.** No single baseline beats the 8 bps-a-day cost. The best gross spread is
  about 4–6 bps.
- **Benchmark for pass condition 3 at 12:00:** the best baseline by equal-weight net Sharpe is
  same-day reversal (−1.70). The logistic regression, judged on 2017 only, is not comparable
  over 2012–2017. The test-period comparison uses each baseline over 2018–2020.

## 12:00 model: the test FAILS; idea B is closed (10 October 2026)

**Training** (RTX 4090, `scripts/intraday_b_train.py`, commit 64b2af9; ledger row `intraday_b/T1200`).
Five seeds on 2012–2016 (619,887 images), early-stopped on 2017 (124,679 images), images
64 × 324, 1,114,370 parameters. The seeds stopped after 7, 4, 6, 9 and 8 epochs with best
validation losses of 0.7038, 0.7157, 0.6987, 0.6958 and 0.6966 (chance is 0.6931) and
validation accuracy 50.2–50.7%. Two seeds were retrained after the first launch of five
processes ran out of GPU memory; the retrained seeds use the same seeds and data, and the
training rule is deterministic given them.

**Dev sanity check** (`outputs/intraday_b/cnn_dev_T1200.csv`; equal-weight H-L, gross bps a
day / gross Sharpe / net Sharpe after 8 bps):

| Years | 2012 | 2013 | 2014 | 2015 | 2016 | **2017 (validation)** |
|---|---|---|---|---|---|---|
| H-L | +33.0 / 28.7 / 21.8 | +27.9 / 27.5 / 19.6 | +25.4 / 22.9 / 15.7 | +30.6 / 24.8 / 18.3 | +32.8 / 20.6 / 15.5 | **+1.6 / 1.56 / −6.49** |

The network memorises its training years (Sharpe 20–29 in-sample) and carries about 1.6
bps a day into the validation year.

**Test, 2018–2020, run once** (`scripts/intraday_b_eval cnn --key 1200 --part test`,
`outputs/intraday_b/cnn_test_T1200.csv`, ledger row `intraday_b/test_T1200`, 750 days):

| Score | EW gross bps / Sharpe (t) | EW net Sharpe (t) | VW gross bps / Sharpe (t) | VW net Sharpe | Top-100 VW gross bps / Sharpe (t) | Top-100 VW net Sharpe |
|---|---|---|---|---|---|---|
| **CNN** | **+1.78 / 1.00 (1.61)** | **−3.48 (−5.60)** | +2.91 / 1.38 (2.28) | −2.41 | +2.64 / 0.98 (1.67) | **−1.99** |
| Intraday momentum | −2.30 / −0.60 (−0.92) | −2.67 | −2.66 / −0.69 | −2.76 | −1.63 / −0.43 | −2.54 |
| Same-day reversal | +2.99 / 0.81 (1.17) | −1.35 | +4.70 / 1.21 (1.89) | −0.85 | +4.07 / 1.08 (1.72) | −1.04 |
| S2 per stock | −3.50 / −1.17 (−1.68) | −3.84 | −4.12 / −1.29 | −3.81 | −3.89 / −1.14 | −3.49 |
| 1-week reversal | +7.49 / 1.64 (2.81) | **−0.11** | +10.89 / 2.44 (3.78) | +0.65 | +11.34 / 2.73 (4.24) | +0.80 (t 1.25) |
| Periodicity (HKS) | −3.05 / −0.95 (−1.65) | −3.43 | −3.38 / −1.01 | −3.38 | −2.09 / −0.60 | −2.88 |
| Logistic regression | +0.76 / 0.22 (0.32) | −2.14 | +2.34 / 0.66 (0.98) | −1.60 | +2.02 / 0.54 (0.82) | −1.59 |

CNN by year, equal weight, gross bps / Sharpe: 2018 +2.2 / 1.75; 2019 +1.3 / 1.06; 2020
+1.9 / 0.74. Positive in each year, never significant.

**Pass condition.**
1. Equal-weight H-L net NW t = **−5.60** (≥ 3.0 required): **FAIL**.
2. Top-100 value-weight H-L net Sharpe = **−1.99** (> 0 required): **FAIL**.
3. CNN equal-weight net Sharpe −3.48 against the best baseline, 1-week reversal, at −0.11
   (margin ≥ 0.3 required): **FAIL**.

**Reading.**
- The CNN finds something: its gross H-L is positive in every test year and its
  value-weight gross t is 2.28. But the spread is about 2 bps a day, a quarter of the 8 bps
  round-trip cost, and its equal-weight gross t of 1.61 is not significant even before costs.
- It does not beat the hand-made baselines. Same-day reversal (+3.0 bps) and 1-week reversal
  (+7.5 bps) both carry larger gross spreads over 2018–2020. The 1-week reversal, flat on
  2012–2017 (+0.8 bps), is the only score with a positive net Sharpe anywhere, in the
  value-weight versions, and that is not significant (t 1.25).
- The 2017 validation year had already shown the pattern: in-sample Sharpe above 20,
  out-of-sample 1.6 bps a day. The frozen test confirms it rather than revising it.
- Intraday momentum, S2 per stock and periodicity are all negative in 2018–2020, as they were
  in 2012–2017: in S&P 500 single stocks, the move since the open reverses by the close.

**Decision.** Idea B is closed. The 2021–2024 seal (`intraday_b_sealed`) stays shut: the
plan opens it only after a pass. No rescue search follows: no other labels, horizons, costs,
architectures or universes will be tried on this data under this idea. The 10:30 and 14:00
models, already queued on the 4090, are trained and reported below because the plan says
they are reported; they are not gated and cannot change the decision.

## 10:30 model: reported, not gated (10 October 2026)

Same recipe (images 64 × 270, 1,031,426 parameters; ledger row `intraday_b/T1030`). Seeds
stopped after 3, 3, 4, 6 and 6 epochs; best validation losses 0.7175, 0.7134, 0.7116, 0.6970
and 0.7022. Dev check: in-sample equal-weight H-L Sharpe 15.5 over 2012–2016; **2017
validation +0.15 bps a day, Sharpe 0.13**.

**Test, 2018–2020, run once** (`outputs/intraday_b/cnn_test_T1030.csv`, ledger row
`intraday_b/test_T1030`, 750 days). CNN equal-weight H-L gross **+1.64 bps a day, Sharpe
0.79 (t 1.55)**, net Sharpe −3.08 (t −6.01); value weight +0.81 / 0.32; top-100 value weight
+0.62 / 0.19, net −2.24. By year, equal weight gross: 2018 +2.0, 2019 +0.2, 2020 +2.7 bps.
Baselines at 10:30 over 2018–2020, equal-weight gross bps / Sharpe / net Sharpe: intraday
momentum −0.9 / −0.18 / −1.83; same-day reversal +1.5 / 0.36 / −1.56; S2 per stock −1.6 /
−0.44 / −2.58; 1-week reversal **+8.7 / 1.57 / +0.12** (top-100 value weight +14.3 / 2.56 /
+1.13, t 1.82); periodicity −4.9 / −1.15 / −3.03; logistic regression −1.1 / −0.26 / −2.15.

Against the (ungated) pass condition: net t −6.01, top-100 net Sharpe −2.24, and the CNN
trails the 1-week reversal by 3.2 Sharpe units. The 10:30 model is weaker than the 12:00
one in every column, which fits a one-hour-old chart carrying less of the day's information.
