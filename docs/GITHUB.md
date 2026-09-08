# Publish the source project

This folder is independent of the neighboring Semester OS app. Keep its own
Git repository root; do not add the ORB directory to the parent's source tree.

The prepared branch is `codex/orb-project`. Create an **empty GitHub repository**
in your account (a private repository is a reasonable starting point), then
run these commands from this folder, replacing the owner/repository name:

```sh
git remote add origin https://github.com/YOUR_ACCOUNT/orb-backtest.git
git push -u origin codex/orb-project
```

If you prefer main, rename your local branch before the first push:

```sh
git branch -m main
git push -u origin main
```

No GitHub repository or remote is created by the project launcher. It never
pushes, logs into a broker, starts a trading test, or uploads market data.

Run the local release gate before the first push:

```sh
python run_all.py --reinstall --check --compile-mt5 --no-app
python -m build
```

Omit `--compile-mt5` away from a Windows machine with MetaTrader installed.
The first command refreshes the isolated environment, runs lint and tests, and
builds a deterministic synthetic report. Market data remains a local runtime
artifact and is never needed by GitHub Actions.

Before a push, `git status` should show only intentional source changes.
`data/`, `outputs/`, `.cache/`, `.venv/`, `.streamlit/secrets.toml`,
.env files and compiled EX5 files are ignored. Source archives are generated
from an explicit release file list and exclude these local artifacts.

GitHub Actions checks Linux and Windows, Python 3.11 and 3.13. Live Dukascopy
requests are not part of CI; a provider/network outage should not block code
tests. MT5 compilation requires a separately installed Windows terminal.

## License choice

The supplied EA attributes copyright to echo. An open-source license was not
specified, so this project does not add a blanket license grant. Before making
a public open-source release, choose a license with all code contributors.
Tutorial inspiration is documented in PROVENANCE.md. The provider's market-data
terms remain separate from any future code license.
