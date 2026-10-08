# JKX chart-image CNN: replication plan (pre-registered 8 October 2026)

**Paper.** Jiang, Kelly & Xiu, "(Re-)Imag(in)ing Price Trends", *Journal of Finance* 78(6),
3193–3249 (2023). A CNN reads 5-, 20- or 60-day black-and-white stock charts and predicts
whether the next 5, 20 or 60 days' return is positive. "Ix/Ry" means x-day images predicting
y-day returns.

**Why replicate first.** Our own ideas will use the same machinery: CRSP, the image
generator, the CNN and the portfolio code. Matching published numbers is the only proof that
this machinery has no bugs. The earlier ML study had no such proof, and three bugs went
unnoticed until late (docs/ML_PLAN.md).

## What the paper specifies (page numbers in the JF article)

| Item | Specification |
|---|---|
| Data | CRSP daily, all firms on NYSE, AMEX and NASDAQ, 1993–2019 (open/high/low start June 1992), p. 3206 |
| Price path | First close of each image = 1; later closes from CRSP returns; open, high and low scaled by that day's close, p. 3206 |
| Image | 3 pixels per day (open tick, high-low bar, close tick); black background, white marks; max and min of the price path at the image top and bottom, pp. 3198–3200 |
| Extras | n-day moving average (one dot per day in the centre column, joined by a line); volume bars in the bottom fifth, scaled to the window's maximum volume, p. 3200 |
| Missing data | IPO or delisting inside the window: image dropped. Missing days in the middle: blank columns. No high or low: day blank; high and low only: bar only (fn. 5) |
| Labels | 1 if the next 5/20/60-day return is positive, else 0, p. 3206 |
| CNN | Blocks of 5×3 conv, batch norm, leaky ReLU, 2×1 max-pool; 2/3/4 blocks; 64→128→256→512 filters; first-layer vertical stride 1/3/3 and dilation 1/2/3; 50% dropout; fully connected layer to 2 outputs (pp. 3246–3247). **155,138 / 708,866 / 2,952,962 parameters** |
| Training | Train and validate on 1993–2000 only: random 70/30 split, Xavier initialisation, Adam with learning rate 1e-5, batch 128, cross-entropy, early stopping after 2 epochs without improvement, pixels standardised with training mean and std (fn. 8), ensemble of 5 runs. Fixed (never retrained) for the 2001–2019 test, pp. 3204–3206 |
| Portfolios | Each week, month or quarter: deciles on predicted up probability; equal and value weights; H-L = decile 10 − decile 1; held to the next rebalance, p. 3206 |

## Choices where the paper is silent (fixed now, before any data are seen)

1. **Image heights 32 / 64 / 96 pixels.** These are the only heights consistent with the
   paper's fully connected sizes (15,360 / 46,080 / 184,320).
2. **Price area and volume area.** Volume gets `height // 5` rows (6 / 12 / 19), with no
   gap. The moving average is included in the min/max scaling, so the line stays inside the
   image.
3. **Padding** is (2, 1) everywhere except (12, 1) on the first layer of the 20- and
   60-day models. Leaky ReLU slope is 0.01. The test `test_cnn_matches_the_papers_parameter_counts`
   checks the paper's exact parameter and fully connected sizes.
4. **Moving average** is the mean of the available path closes in the n trading days
   ending each day, using days before the image when listed. Lines are 8-connected.
5. **Volume** is CRSP `vol × cfacshr`, so splits inside a window do not jump.
6. **Universe** is `exchcd` 1, 2, 3 with all share codes, following the paper's "all firms".
   Share codes 10 and 11 only is the single robustness variant.
7. **Formation dates** are the last trading day of each week (R5), month (R20) or quarter
   (R60). Models are trained on images sampled at that same frequency. A stock qualifies if
   it has a CRSP row on every day of the window and a price and market cap at formation.
8. **Returns.**
   - Labels compound CRSP `ret` over the next 5/20/60 trading days.
   - Holding returns compound to the next formation date.
   - A delisting return is placed on the first trading day after the stock's last row. Days
     after delisting earn zero, as cash.
   - A missing `dlret` counts as zero.
9. **Benchmarks** are measured on the same universe as the 5-day images:
   - WSTR = −(5-day return);
   - STR = −(20-day return);
   - MOM = return from day t−251 to day t−21.

   TREND is not reproduced.
10. **Sharpe ratios** are annualised by √52, √12 or √4. t-statistics use Newey–West with
    4 lags.
11. **Data end.** The legacy CRSP tables (`crsp.dsf`) are used. Forward returns that would
    need data past the loaded end are NaN, never zero.

## Gates (fixed now; each must pass before the next is attempted)

**Gate 0, code (passed 8 October 2026).** `tests/test_jkx.py`, 14 tests:
- exact paper parameter counts;
- the image layout;
- price-level and split invariance;
- **features unchanged when every input after the formation date is rewritten**;
- IPO and delisting exclusion;
- delisting-return timing;
- NaN rather than zero past the data end;
- decile maths on a planted signal;
- the vault;
- the CNN learning a planted chart pattern.

**Gate 1, data and portfolio code (CPU, before any GPU spend).** This checks CRSP, the
universe and the portfolio code with no CNN involved. Benchmark H-L Sharpe ratios for
2001–2019 must land:
- **equal weight:** within ±0.5 of Tables I and II for WSTR/R5 (2.84), STR/R5 (1.76),
  MOM/R5 (0.07), WSTR/R20 (1.23) and STR/R20 (0.55);
- **value weight:** within ±0.4 for WSTR/R5 (0.77) and STR/R5 (0.44).

A miss means a data or universe bug, which is investigated before anything else.

**Gate 2, replication.** The primary models are I5/R5, I20/R5 and I20/R20.
- **Equal-weight H-L Sharpe:** at least 0.7× the paper's (7.15 / 6.75 / 2.16).
- **Value-weight H-L Sharpe:** at least 0.5× the paper's (1.49 / 1.74 / n/a).
- **Decile monotonicity:** decile mean returns rank-correlate with the decile number at
  ≥ 0.9.
- **Too good to be true:** a Sharpe ratio above 1.3× the paper's is treated as a suspected
  leak and must be explained before proceeding.

Everything else in Tables I, II and IV is reported, but not gated.

**Gate 3, post-publication test (sealed).** The years 2020 to the end of the legacy CRSP
data are sealed in `vault.json` (`jkx_post_publication`). Loaders refuse them, and only an
experiment with this committed plan can unseal them, once.
- **When:** after Gate 2 passes.
- **What:** the frozen 1993–2000 models are run on 2020+ images, with no retraining and no
  change of any choice above.
- **Success requires both:**
  - I20/R5 equal-weight H-L t (NW) ≥ 2.0;
  - the 500-largest-stocks equal-weight H-L Sharpe > 0.

A failure is reported as the decay of a published anomaly, as in McLean & Pontiff, and
there is **no rescue search**.

### Gate 3 extension: 2025 and 2026 (added 8 October 2026, before any sealed data was read)

The legacy `crsp.dsf` ends on 2024-12-31. The new CRSP format (`crsp.dsf_v2`) reaches
2025-12-31.
- **2025 joins the sealed post-publication test.** The test then covers 2020–2025 and is
  still run once.
- **Parity check first.** 2025 is used only if `dsf_v2` reproduces the legacy fields (price,
  open, high, low, return, volume, shares, exchange) on the **open year 2019**, for at least
  99.9% of stock-days, and the 2019 Gate-1 benchmark Sharpe ratios match to two decimals. If
  parity fails, the test stops at 2024.
- **2026 is not in CRSP yet.** It is covered by a live paper-trading forward test, started
  after Gate 3 and reported separately. Weekly predictions are recorded before each week
  begins.

### Scope and budget (added 9 October 2026, before any CNN was trained)

- **Budget cap: ¥120** of rented GPU time for the replication and the user's own follow-up
  study combined. The replication is planned at about ¥15.
- **The 60-day models (I60/R5, I60/R20, I60/R60) are dropped.** Each 96×180 image costs about
  10× the compute of a 20-day image. I60/R5 alone was estimated at 35–40 GPU hours, more than
  the rest of Tables I and II together. Gates 2 and 3 use only 5- and 20-day models, so they
  are unchanged.
- **Speed-ups that leave the maths unchanged.** Several seeds are trained concurrently on one
  GPU; convolutions use PyTorch's default TF32. No reduced-precision (bf16) training, and no
  change to the batch size, learning rate, seeds or early-stopping rule.
- **Hours spent are logged here** after each session.

## Results

**Gate 1 passed (8 October 2026).** Benchmark H-L Sharpe ratios for 2001–2019, ours against
the paper:

| Benchmark | Weights | Ours | Paper | Difference | Tolerance | Result |
|---|---|---|---|---|---|---|
| WSTR/R5 | equal | 2.86 | 2.84 | +0.02 | ±0.5 | pass |
| STR/R5 | equal | 1.80 | 1.76 | +0.04 | ±0.5 | pass |
| MOM/R5 | equal | 0.24 | 0.07 | +0.17 | ±0.5 | pass |
| WSTR/R20 | equal | 1.23 | 1.23 | 0.00 | ±0.5 | pass |
| STR/R20 | equal | 0.52 | 0.55 | −0.03 | ±0.5 | pass |
| WSTR/R5 | value | 0.97 | 0.77 | +0.20 | ±0.4 | pass |
| STR/R5 | value | 0.57 | 0.44 | +0.13 | ±0.4 | pass |

**Annualised H-L returns** also match: WSTR/R5 equal weight 0.53 against the paper's 0.53,
and STR/R5 0.39 against 0.39.

**Turnover** is close: WSTR/R5 717% against 660%, STR/R5 378% against 341%.

The data, the universe, the return compounding and the portfolio code reproduce the paper
without any CNN, so any Gate-2 gap is in the images or the model. The full table is in
`outputs/jkx/report.csv`.

## Compute and storage

| Step | Where | Size / time |
|---|---|---|
| CRSP 1992–2024 download | this PC, `C:/data/crsp` | ≈ 1–2 GB, under 1 hour |
| Image build | this PC for 5-day; the GPU server for 20- and 60-day | weekly 1993–2019 ≈ 9 M images: 0.55 GB (5-day), 4.4 GB (20-day), 20 GB (60-day). Measured 62k / 18k / 5k images per second on one core |
| Training | rented GPU (RTX 3090/4090, 24 GB) | This PC's CPU trains at 370 (5-day) and 69 (20-day) images per second: weeks. A GPU is needed even for 5-day |

This PC has 8 GB of free disk, so the 20- and 60-day image sets are built on the server.
Only images and labels go to the server. **Check the WRDS terms of use before copying any
CRSP-derived data to a rented machine, and delete it afterwards.**

## Order

1. Save the WRDS login (`python -m scripts.wrds_pgpass`, run by the user).
2. Download CRSP (`python -m scripts.jkx_download_crsp --user <name>`).
3. Build the 5-day images (weekly, monthly, quarterly) and run Gate 1
   (`python -m scripts.jkx_evaluate --benchmarks-only`).
4. On the GPU, run I5/R5, I20/R5 and I20/R20 (`python -m scripts.jkx_train`), then Gate 2.
5. The rest of Tables I and II, then Gate 3.

Every training and evaluation run appends a row to `outputs/ledger.jsonl`.
