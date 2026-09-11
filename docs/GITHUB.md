# Publish the source project

This folder is independent of the neighboring Semester OS app. Keep its own
Git repository root; do not add the ORB directory to the parent's source tree.

The repository uses the `main` branch and is connected to
`https://github.com/xantugs/orb-backtest.git`. Push reviewed commits from this
folder with:

```sh
git push -u origin main
```

No GitHub repository or remote is created by the project launcher. It never
pushes, logs into a broker, starts a trading test, or uploads market data.

Run the local release gate before the first push:

```sh
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m pytest -q
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\compile_mt5.ps1
python -m build
```

Omit `--compile-mt5` away from a Windows machine with MetaTrader installed.
The first command refreshes the isolated environment, runs lint and tests, and
builds a deterministic synthetic report. Market data remains a local runtime
artifact and is never needed by GitHub Actions.

Before a push, `git status` should show only intentional source changes.
`data/`, `outputs/`, `.cache/`, `.venv/`,
.env files and compiled EX5 files are ignored. Source archives are generated
from an explicit release file list and exclude these local artifacts.

GitHub Actions checks Linux and Windows, Python 3.11 and 3.13. Live Dukascopy
requests are not part of CI; a provider/network outage should not block code
tests. MT5 compilation requires a separately installed Windows terminal.

## License

The source repository includes the MIT License. Tutorial inspiration is
documented in PROVENANCE.md. WRDS, Dukascopy and broker market-data rights and
redistribution terms remain separate from the code license; data stays ignored.
