# Verification record — 2026-09-08

## Passed locally

Environment: Windows, Python 3.13.5, NumPy 2.3.5, pandas 2.3.3,
Streamlit 1.63.0, Plotly 6.9.0. The exact installed dependency snapshot is
`requirements-lock-py313-windows.txt`.

- `python run_all.py --check --compile-mt5 --no-app` completed its setup,
  compilation, lint, tests, 120-day demo and report pipeline.
- **77 pytest cases passed**, including the original 17 engine scenarios and
  new clock, data validation, BI5, cache/retry, strict reconciliation, report,
  app and launcher cases.
- `python -m ruff check .`: all checks passed.
- `python -m pip check`: no broken requirements.
- `python -m build`: source distribution and engine wheel built.
- MetaEditor **5.0.0.6140**, EA **1.30**: **0 errors, 0 warnings**.
  The local compiler log is `mt5/compile.log`; it is not committed.
- Browser smoke test: the launcher served the app on localhost, the synthetic
  dataset loaded, Run backtest produced the summary metrics and rendered the
  balance chart. The browser values agreed with the command-line bundle.
- Dark-workspace tests cover automatic reruns, unapplied setting changes,
  saved result snapshots, comparison clearing and empty explorer filters.
- Browser review at the in-app panel width confirmed dark controls, readable
  two-column summary cards, the candlestick inspector and saved-run chart.

Synthetic baseline reproducibility (120 days, seed 42, baseline.json):
122 trades; 109 traded days; total gross R approximately -6.6670; final
closed balance 8541.4721 from 10000. These are fixture results, not strategy
performance evidence. Reports are local under `outputs/`.

## Pending or unavailable

- Dukascopy live-feed verification: a two-day request for
  [2025-03-10, 2025-03-12) UTC timed out at the public feed host from this
  machine. DNS resolved, but TCP/443 failed; HTTPS/HTTP BI5 URLs and the official
  web export routes all timed out. No real-data result is claimed and no
  substitute synthetic data was used. Resume and completed-dataset hash checks
  are verified offline.
- MT5 visual Strategy Tester smoke test and ten-trade manual review.
- Real broker EA/Python trade reconciliation.
- GitHub-hosted Windows/Linux/Python matrix: configured but not yet run on
  GitHub. Only the local Windows/Python 3.13 environment is verified here.
- WRDS access, entitlements and adapter: not implemented or claimed.

## Dependency replay

On a new Windows/Python 3.13 environment, install
`requirements-lock-py313-windows.txt`, then install this project with
`pip install -e . --no-deps`. Use `run_all.py --no-install` to keep that
environment unchanged. Other platforms use the compatible ranges in
pyproject.toml and record their resolved versions in each manifest.

The source archive includes the app, scripts, config, tests, EA and docs.
The wheel provides the `orb` Python package; use a source checkout for the
complete one-command workflow.
