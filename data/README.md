# Local data

Everything in this directory except this README is excluded from Git.

- QQQ TAQ is the target dataset. Put compact WRDS yearly bars under
  `wrds/years/YEAR/` together with each `manifest.json`.
- Keep the 24 GB raw WRDS event archive outside OneDrive at
  `C:\data\wrds_ticks\YEAR\MONTH\`.
- `USTEC_M1.csv` and `USTEC_M15.csv` are a legacy CFD stopgap until every QQQ
  year has been downloaded and validated locally.

`python run_all.py` still prefers `USTEC_M1.csv` when present; select QQQ with
`--source wrds --bars data/wrds/years`. Remove the CFD files only after the full
QQQ manifest set passes validation. Use
`python run_all.py --source demo` only when a deterministic synthetic software
fixture is useful. Use `--source dukascopy --data-only` to prepare a fresh
USATECHIDXUSD dataset. Market data and provider files must not be committed.
