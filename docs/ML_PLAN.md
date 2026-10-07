# Machine-learning study: meta-labeling S2 and a direct predictor (8 October 2026)

Written and committed **before** any model has been trained.

**Recipe.** The method follows the 1st-place Jane Street Market Prediction solution (team
"Cats Trading", incl. Yuanzhe Zhou):
- XGBoost blended with a supervised-autoencoder MLP;
- the target as a sign classification, with sample weights proportional to |return|;
- several horizons as a multi-label target;
- Gaussian-noise input augmentation and swish activations;
- averaging over 3 seeds;
- time-ordered validation with a gap.

Our features are built from public minute prices, not from Jane Street's private signals.

## Data and splits (all New York time, by date)

**Markets.**
- Nasdaq-100 (the traded market) and S&P 500: HistData `NSXUSD` and `SPXUSD`.
- Cross-asset inputs: DAX (`GRXEUR`) and gold (`XAUUSD`).
- Forward check: FTMO `US100.cash`, `US500.cash`, `GER40.cash` and `XAUUSD`.

**Splits.** 5 trading days are dropped between each split.

| Split | Period | Use |
|---|---|---|
| Train | 2010-11 to 2017-12 | fitting |
| Validation | 2018-01 to 2021-12 | early stopping, the blend and the trading threshold |
| **Sealed test** | **2022-01 to 2024-12** | **run once** |
| Forward check | FTMO 2025-01 to 2026-10 | run once, with the test |

## Features at each decision time t (half-hour checkpoints 10:00 … 15:30)

Every feature uses only data available at t.

**Calendar:**
- checkpoint index;
- day of week.

**Noise area:**
- the move from the 09:30 open, plain and in units of σ(t), S2's 14-day average move;
- distance to S2's upper and lower bands, in σ units;
- S2's signal at t (−1, 0 or +1).

**Gap:**
- the 09:30 open over the previous close;
- that gap over its 14-day average.

**Momentum:**
- returns over the last 5, 15, 30 and 60 minutes;
- the return since the previous close.

**Volatility:**
- the standard deviation of 1-minute returns since the open, over its 20-day average at
  the same time;
- the range since the open, in σ units.

**Daily:**
- the previous day's return and range;
- 5-day and 20-day returns;
- 20-day realised volatility.

**Cross-asset:**
- the other US index (S&P 500 for the Nasdaq, and the reverse): its move since its open,
  and its last 30-minute return;
- the DAX: its move since its own 09:00 open, and its last 30 minutes;
- gold: its move since 08:20 New York, and its last 30 and 60 minutes.

## Project B: meta-labeling S2 (the first priority)

**Samples.** Every S2 entry on the Nasdaq-100 and the S&P 500. Pooling the two markets
doubles the data, and a market flag is a feature.
- **Label:** the trade's net return > 0, weighted by |net return|.
- **Features:** taken at the entry checkpoint.

**Rule.** Take an S2 trade only if the blended model's P(win) is at least p\*.
- p\* is chosen on validation from {0.40, 0.45, 0.50, 0.55}, as the value with the best
  validation Sharpe of the filtered Nasdaq S2.
- A skipped trade's day returns 0 from that trade, and the rest of S2 runs unchanged.

**Pass, on the sealed test (Nasdaq S2, 2022–2024), against unfiltered S2 on the same
days:**
- filtered mean daily return ≥ unfiltered;
- filtered Sharpe ≥ unfiltered + 0.20;
- a higher win rate on the days traded.

The FTMO 2025–26 check confirms if the filtered Sharpe is at least the unfiltered.

## Project A: direct 30-minute predictor

**Samples.** Every Nasdaq checkpoint from 10:00 to 15:30, about 12 a day.
- **Labels:** multi-label signs of the next 30 minutes, 60 minutes and the move to 16:00,
  weighted by the mean |return| of the three.
- **Prediction:** P(up) is the mean of the three heads.

**Trading.**
- At each checkpoint the position is +1 if P(up) − 0.5 > θ, −1 if it is < −θ, and flat
  otherwise.
- Trades happen only when the position changes, and everything is flat at 16:00.
- θ is chosen on validation from {0.00, 0.02, 0.05, 0.08}, for the best validation
  Sharpe.
- Costs as in S2: the HistData spread model, the FTMO recorded spread, and 10%
  slippage.

**Pass, on the sealed test (2022–2024):**
- mean daily return > 0 with **t ≥ 2.0**;
- at least 2 of 3 years positive.

The FTMO 2025–26 check confirms if its mean is > 0.

## Models (fixed now)

**XGBoost:** `hist`, max_depth 5, eta 0.03, subsample 0.8, colsample 0.7,
min_child_weight 50, up to 2,000 rounds, early stopping on validation log-loss (100
rounds). One model per label.

**Supervised-autoencoder MLP** (PyTorch):
- **Autoencoder:** Gaussian noise (σ = 0.05 on standardised inputs) → encoder 96 → 32,
  with swish → decoder back to the inputs.
- **Auxiliary head:** on the 32-dimensional code, for the labels.
- **Main MLP:** on [inputs, code], 256 → 128, with batch norm, dropout 0.2, swish and
  sigmoid outputs.
- **Loss:** reconstruction MSE + auxiliary BCE + main BCE, all weighted.
- **Training:** AdamW, lr 1e-3, batch 512, early stopping on the validation main BCE,
  patience 10, and 3 seeds averaged.

**Blend:** the mean of the XGBoost and MLP probabilities.

## Rules

1. Validation chooses only p\* (B), θ (A) and early stopping. Nothing else is tuned.
2. The sealed test and the FTMO check run **once**, together, after the choices are
   written to `outputs/ml/choices.json`.
3. The results are recorded whatever they are.
