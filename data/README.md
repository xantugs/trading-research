# Local data

Everything in this directory except this README is excluded from Git. Generated
and prepared bars, provider files, caches, and reports must remain local.

QQQ WRDS data is the primary research input and lives outside this repository:

- `C:\data\wrds_m1` contains the compact yearly M1 layer for `--mode m1`;
- `C:\data\wrds_ticks` contains raw daily TAQ trade/NBBO Parquets for
  `--mode tick`.

The modes are independent. M1 mode never reads the tick tree. Tick mode derives
its own M1 and signal candles and never reads or falls back to the M1 tree.

```powershell
python run_all.py research --mode m1
python run_all.py research --mode tick --from 2018-01-01 --to 2023-01-01
```

The former local USTEC CFD stopgap has been removed. Synthetic and Dukascopy
sources remain explicit software-fixture and alternate-feed workflows; neither
is substituted when a WRDS mode is requested.
