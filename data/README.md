# Local data

Run `python run_all.py` to generate the deterministic synthetic M1 demo.
Use `--source dukascopy --from YYYY-MM-DD --to YYYY-MM-DD` for real downloads.

Everything in this directory except this README is excluded from Git.
Dukascopy subfolders contain canonical M1, resampled M15, MT5 import bars and
provider metadata. Files supplied in the original kit may remain in your local
checkout; the app selects M1 bars and does not treat trade logs as price data.
