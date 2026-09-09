"""Portable result bundles: CSV tables, strict JSON metadata and offline HTML."""
from __future__ import annotations

import hashlib
import html
import importlib.metadata
import json
import math
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from .engine import Result


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean_json(value), indent=2, allow_nan=False, default=str), encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quality_report(m1: pd.DataFrame, tf: int, orb_hour: int, orb_minute: int) -> dict:
    counts = m1.open.resample(f"{tf}min").count()
    present = counts[counts > 0]
    opening = counts[(counts.index.hour == orb_hour) & (counts.index.minute == orb_minute)]
    observed_days = set(m1.index.normalize())
    missing = [str(t.date()) for t, n in opening.items()
               if t.normalize() in observed_days and t.weekday() < 5 and n < tf
               and (n > 0 or (m1.index[0] <= t and m1.index[-1] >= t + pd.Timedelta(minutes=tf - 1)))]
    return {
        "rows": len(m1), "first": str(m1.index[0]), "last": str(m1.index[-1]),
        "observed_days": len(observed_days), "partial_signal_bars": int((present < tf).sum()),
        "incomplete_or_missing_opening_ranges": missing,
        "gaps_over_one_minute": int((m1.index.to_series().diff() > pd.Timedelta(minutes=1)).sum()),
        "note": "Gaps include scheduled closures. No prices are forward-filled. Incomplete opening ranges abort the run.",
    }


def save_result(result: Result, output: Path, source: dict, quality: dict) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    result.trades.to_csv(output / "trades.csv", index=False)
    result.days.to_csv(output / "days.csv", index=False)
    result.events.to_csv(output / "events.csv", index=False)
    result.equity.to_csv(output / "balance.csv", index_label="time")
    write_json(output / "params.json", result.params.to_dict())
    write_json(output / "stats.json", result.stats)
    write_json(output / "data_quality.json", quality)
    for key in ("year", "weekday", "entry_hour", "direction", "reason"):
        result.by(key).to_csv(output / f"by_{key}.csv")
    try:
        root = Path(__file__).resolve().parents[1]
        if not (root / ".git").exists():
            raise OSError("Project is not initialized as its own Git repository")
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True))
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    source_hashes = {str(p.relative_to(root)): file_hash(p) for p in sorted((root / "orb").glob("*.py"))}
    source_hashes["run_all.py"] = file_hash(root / "run_all.py")
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "project_version": "1.5.0",
        "git_revision": revision, "git_dirty": dirty, "source_sha256": source_hashes,
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": {k: importlib.metadata.version(k) for k in ("numpy", "pandas", "plotly", "tzdata")},
        "source": source, "params": result.params.to_dict(),
        "limitations": [
            "Research simulation; local EA compilation passes, but broker smoke testing and reconciliation are pending.",
            "Balance drawdown excludes unrealized intratrade drawdown.",
            "R statistics are gross of commission; money profit includes per-side commission at exit.",
            "No margin, slippage, financing, currency conversion or netting-account model.",
            "Minimum lot follows the EA clamping behavior and can exceed requested risk.",
            "Immediate entries and stop management have unknown intrabar ordering.",
            "END exits mark still-open trades at the last available close.",
        ],
    }
    write_json(output / "manifest.json", manifest)
    balance = go.Figure(go.Scatter(x=result.equity.index, y=result.equity.values, name="Balance"))
    balance.update_layout(template="plotly_white", title="Closed-trade balance", height=350)
    dd = (result.equity / result.equity.cummax() - 1) * 100
    drawdown = go.Figure(go.Scatter(x=dd.index, y=dd.values, fill="tozeroy", line_color="#d1495b"))
    drawdown.update_layout(template="plotly_white", title="Closed-trade drawdown (%)", height=250)
    stats = pd.Series(result.stats).drop("reasons", errors="ignore").rename("value").to_frame().to_html()
    report = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>ORB research report</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 24px;color:#172033}}
table{{border-collapse:collapse;font-size:14px}}td,th{{padding:7px 12px;border:1px solid #ddd}}
.note{{padding:16px;background:#fff4d6;border-radius:8px}}h1{{margin-bottom:8px}}</style>
<h1>Opening Range Breakout</h1><p>{html.escape(str(source.get("label", "")))}</p>
<p class="note">Research output. Check the data clock, gaps, costs and broker agreement before interpreting results.
Synthetic runs demonstrate software behavior only. Balance drawdown excludes open-position risk.</p>
{balance.to_html(full_html=False, include_plotlyjs=True)}
{drawdown.to_html(full_html=False, include_plotlyjs=False)}
<h2>Statistics</h2>{stats}
<h2>Data quality</h2><pre>{html.escape(json.dumps(quality, indent=2))}</pre>
<h2>Assumptions and limitations</h2><ul>{"".join("<li>"+html.escape(x)+"</li>" for x in manifest["limitations"])}</ul>
<h2>Trades (first 100)</h2>{result.trades.head(100).to_html(index=False)}
<p>Full trades, settings, diagnostics and source checksums are alongside this report.</p></html>"""
    (output / "report.html").write_text(report, encoding="utf-8")
    return output / "report.html"
