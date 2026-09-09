# Local data

Everything in this directory except this README is excluded from Git.

- `USTEC_M1.csv` and `USTEC_M15.csv` are the current local working bars.
- Put incoming WRDS raw events under `wrds/ticks/YEAR/MONTH/`.
- Put WRDS yearly research bars under `wrds/years/YEAR/`.

`python run_all.py` prefers `USTEC_M1.csv` when present. Use
`python run_all.py --source demo` only when a deterministic synthetic software
fixture is useful. Use `--source dukascopy --data-only` to prepare a fresh
USATECHIDXUSD dataset. Market data and provider files must not be committed.
