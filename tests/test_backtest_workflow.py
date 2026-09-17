"""Run isolation and cache-only safeguards for the one-command workflow."""
from __future__ import annotations

import json

import pytest

from run_all import _prepare_backtest_config, _require_cached_development


def test_backtest_snapshots_parameters_without_changing_source(tmp_path):
    source = tmp_path / "chosen.json"
    chosen = {"symbol": "XAUUSD", "risk_percent": 0.25,
              "development_end": "2025-01-01", "holdout_start": "2025-01-01"}
    source.write_text(json.dumps(chosen), encoding="utf-8")
    target = tmp_path / "run"
    effective = _prepare_backtest_config(source, target)
    saved = json.loads(effective.read_text(encoding="utf-8"))
    assert saved["risk_percent"] == 0.25
    assert saved["output_dir"] == str(target.resolve())
    assert json.loads(source.read_text(encoding="utf-8")) == chosen
    with pytest.raises(ValueError, match="already contains files"):
        _prepare_backtest_config(source, target)


def test_backtest_refuses_holdout_leak_and_missing_cached_month(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text(json.dumps({"development_end": "2025-02-01",
                                  "holdout_start": "2025-01-01"}), encoding="utf-8")
    with pytest.raises(ValueError, match="sealed holdout"):
        _prepare_backtest_config(source, tmp_path / "run")

    cache = tmp_path / "cache"
    config = {"start": "2024-12-01", "development_end": "2025-02-01",
              "cache_dir": str(cache), "expected_server": "FTMO-Demo", "symbol": "XAUUSD"}
    month_dir = cache / "FTMO-Demo" / "XAUUSD"
    month_dir.mkdir(parents=True)
    (month_dir / "202412.parquet").touch()
    with pytest.raises(ValueError, match="2025-01"):
        _require_cached_development(config)
    (month_dir / "202501.parquet").touch()
    _require_cached_development(config)
