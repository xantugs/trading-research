# Contributing

Use Python 3.11–3.13 and an isolated environment. Install `-e ".[research-v2,dev]"`
and run `.venv\Scripts\python -m ruff check .` plus
`.venv\Scripts\python -m pytest -q`.

Keep strategy changes separate from data/packaging changes. Describe the exact
before/after rule, add a small independent price scenario for behavioral fixes,
and update `docs/RESEARCH_V2.md` whenever execution assumptions change. Never describe a
synthetic check as broker reconciliation.

Do not commit market data, account details, terminal logs, generated reports,
or credentials. Include manifests and a minimal synthetic reproducer when
reporting a discrepancy. The M1 research result is not a full tick replay.
