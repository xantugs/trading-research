# Contributing

Use Python 3.11–3.13 and an isolated environment. Install `-e ".[dev,wrds]"`
and run `.venv\Scripts\python -m ruff check .` plus
`.venv\Scripts\python -m pytest -q`.

Keep strategy changes separate from data/packaging changes. Describe the exact
before/after rule, add a small independent price scenario for behavioral fixes,
and update MODEL.md whenever execution assumptions change. Never describe a
synthetic check as broker reconciliation.

Do not commit market data, account details, terminal logs, generated reports,
credentials or EX5 files. Include manifests and a minimal synthetic reproducer
when reporting a discrepancy. Verify both EA and Python when changing shared
rules; compiling the EA alone does not establish parity.
