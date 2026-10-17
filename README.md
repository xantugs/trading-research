# trading-research

My intraday trading research, mostly on the Nasdaq-100, gold and US stocks.

One rule throughout: write the test down before running it, keep a holdout period sealed, and open it once. Most ideas didn't survive. The write-ups in `docs/` say why.

## Results

| Study | What happened | Write-up |
|---|---|---|
| Opening-range breakout (gold, Nasdaq-100) | On gold nothing held up, and it didn't carry over to silver. The Nasdaq version looked good on 2021–26 but made nothing on 2010–21. Dropped. | [holdout](docs/PRE2021_HOLDOUT_PLAN.md), [notes](docs/ORB_NOTES.md) |
| Noise-area intraday momentum (replicating Zarattini, Aziz & Barbon, 2024) | The one survivor. Sharpe 1.65 (t = 3.84) on the sealed 2018–24 holdout, still fine at 2× costs. Weaker on 2025–26 (Sharpe 0.69). | [plan and results](docs/INTRADAY_MOMENTUM_PLAN.md) |
| ML on intraday features (XGBoost + neural net) | No edge. An early t = 12 result turned out to be a one-minute look-ahead in the DAX and gold features. | [ML study](docs/ML_PLAN.md) |
| Chart-image CNN (replicating Jiang, Kelly & Xiu) | Replicated on CRSP and still works after publication (2020–25), but before costs and mostly in small caps. | [replication](docs/JKX_REPLICATION_PLAN.md) |
| Same CNN on CFDs, FX and commodities | Didn't transfer. | [pilot](docs/JKX_TRANSFER_PILOT.md) |
| Intraday chart CNN (TAQ) | 2–4 bps a day before costs, against 8 bps of costs. Dropped. | [study](docs/INTRADAY_CHARTS_PLAN.md) |
| Gap fade, late-day reversal, overnight, leveraged-ETF close | All failed. | [gap fade](docs/GAP_FADE_PLAN.md), [0DTE-era rules](docs/REGIME_STRATEGIES_PLAN.md), [LETF](docs/LETF_CLOSE_PLAN.md) |

Nothing here is a proven money-maker, and nothing is trading live.

## What's in the repo

- `orb_research/`: backtest engine, strategies, data loaders
- `scripts/`: one script per study
- `mt5/`: MetaTrader 5 expert advisors (breakout and noise-area)
- `webapp/`: small dashboard for parameter runs
- `configs/`: strategy and cost settings
- `docs/`: the plan and results for each study
- `tests/`: pytest suite

## Running it

Python 3.11–3.13.

```bash
pip install -e ".[research-v2,ml,dev]"
pytest -q
```

A few entry points:

```bash
python run_all.py backtest --config configs/xauusd_orb_research.json   # gold breakout (Windows + MT5)
python -m scripts.intraday_momentum_study develop                      # noise-area momentum, development years
python -m scripts.ml_study build                                       # ML feature build
```

## Data

No data is committed. It comes from:

- an FTMO demo account through MetaTrader 5 (minute bars and ticks)
- HistData (Nasdaq-100 minute bars from 2010)
- CRSP and TAQ through WRDS (needs a subscription, can't be shared)

## Caveats

- Costs are modelled from broker spreads, not real fills.
- The FTMO server clock is New York + 7 hours. `scripts/verify_broker_clock.py` checks it.

The repo used to be called `orb-backtest`, back when it was only breakouts.
