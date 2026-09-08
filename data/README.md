# Local data

Run `python run_all.py` to generate the deterministic synthetic M1 demo.
Run `python run_all.py --source dukascopy --data-only` to prepare the default
US 100 dataset from 2022 through the latest completed UTC day. Dates remain
overridable with `--from` and `--to`.

Everything in this directory except this README is excluded from Git.
Dukascopy subfolders contain canonical M1, resampled M15, MT5 import bars and
provider metadata. Files supplied in the original kit may remain in your local
checkout; the app selects M1 bars and does not treat trade logs as price data.
Exact completed datasets are reused only when their recorded hashes still match;
use `--refresh-data` to rebuild one from the raw cache.
