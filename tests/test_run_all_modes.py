import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import run_all
from orb import load_bars
from orb.synthetic import make_synthetic_m1
from run_all import (
    _input_artifact,
    _materialize_wrds_m1,
    _prepare_tick_signal_bars,
    _slice_wrds_m1,
    _tick_source_signature,
    app_environment,
    parse_args,
    pipeline,
)


def tick_inventory(root: Path, day: str = "2020-01-02") -> tuple[Path, Path]:
    month = root / day[:4] / day[:7]
    month.mkdir(parents=True)
    trade = month / f"QQQ_{day}_trades.parquet"
    quote = month / f"QQQ_{day}_quotes.parquet"
    trade.write_bytes(b"trade fixture")
    quote.write_bytes(b"quote fixture")
    (month / "manifest.json").write_text(json.dumps({
        "symbol": "QQQ",
        "files": [
            {"file": trade.name, "rows": 10},
            {"file": quote.name, "rows": 20},
        ],
    }), encoding="utf-8")
    return trade, quote


def test_tick_signature_covers_inventory_and_unbounded_dates(tmp_path):
    tick_root = tmp_path / "wrds_ticks"
    _, quote = tick_inventory(tick_root)
    args = parse_args(["--mode", "tick", "--tick-root", str(tick_root)])

    assert args.start is None and args.end is None
    first = _tick_source_signature(tick_root, args)
    quote.write_bytes(b"replacement quote fixture with a new size")
    assert _tick_source_signature(tick_root, args) != first

    bounded = parse_args([
        "--mode", "tick", "--tick-root", str(tick_root),
        "--from", "2020-01-01", "--to", "2021-01-01",
    ])
    assert bounded.start == "2020-01-01" and bounded.end == "2021-01-01"
    extra = quote.parent / "QQQ_2020-01-03_quotes.parquet"
    extra.write_bytes(b"not declared")
    with pytest.raises(ValueError, match="absent from manifests"):
        _tick_source_signature(tick_root, bounded)


def test_tick_signal_cache_is_built_only_from_explicit_tick_root(tmp_path, monkeypatch):
    tick_root = tmp_path / "wrds_ticks"
    tick_inventory(tick_root)
    sibling_m1 = tmp_path / "wrds_m1"
    sibling_m1.mkdir()
    (sibling_m1 / "must-not-be-read.txt").write_text("m1", encoding="utf-8")
    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / "scripts/wrds_build_m1_local.py").write_text("cache version 1", encoding="utf-8")
    monkeypatch.setattr(run_all, "ROOT", fake_repo)

    calls = []

    def fake_build(root, output, **kwargs):
        calls.append((root, kwargs))
        year = output / "years" / "2020"
        year.mkdir(parents=True)
        (year / "QQQ_2020-01-01_2021-01-01_M1.csv.gz").write_bytes(b"derived from ticks")
        (output / "manifest.json").write_text(json.dumps({
            "source_root": str(root.resolve()), "trading_days": 1,
        }), encoding="utf-8")

    import scripts.wrds_build_m1_local as builder

    monkeypatch.setattr(builder, "build", fake_build)
    args = parse_args(["--mode", "tick", "--tick-root", str(tick_root)])
    years, info = _prepare_tick_signal_bars(args)

    assert calls == [(tick_root.resolve(), {
        "symbol": "QQQ", "start": None, "end": None,
        "start_time": "07:30:00", "end_time": "16:01:00", "point": 0.01,
        "strategy_offset_hours": 7,
    })]
    assert years.is_relative_to(fake_repo / ".cache/wrds_tick_m1")
    assert info["tick_root"] == str(tick_root.resolve())
    assert str(sibling_m1) not in str(years)
    assert _prepare_tick_signal_bars(args)[0] == years
    assert len(calls) == 1


def test_app_environment_removes_opposite_mode_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ORB_WRDS_M1_ROOT", "stale-m1")
    monkeypatch.setenv("ORB_TICK_ROOT", "stale-ticks")
    monkeypatch.setenv("ORB_TICK_LATENCY_MS", "999")
    data_path, output = tmp_path / "signals.csv.gz", tmp_path / "result"
    tick_root = tmp_path / "wrds_ticks"
    tick = parse_args([
        "--mode", "tick", "--tick-root", str(tick_root),
        "--tick-latency-ms", "5", "--tick-max-entry-delay-ms", "20",
        "--tick-max-eod-delay-ms", "30", "--tick-cache-days", "1",
    ])
    tick_env = app_environment(tick, data_path, output)

    assert tick_env["ORB_BACKTEST_MODE"] == "tick"
    assert tick_env["ORB_SOURCE_ROOT"] == str(tick_root.resolve())
    assert tick_env["ORB_WRDS_TICK_ROOT"] == str(tick_root.resolve())
    assert tick_env["ORB_EXECUTION_MODEL"] == "nbbo_top_of_book"
    assert tick_env["ORB_EXECUTION_TIMING"] == "event_level"
    assert tick_env["ORB_TICK_LATENCY_MS"] == "5.0"
    assert tick_env["ORB_TICK_MAX_ENTRY_DELAY_MS"] == "20.0"
    assert tick_env["ORB_TICK_MAX_EOD_DELAY_MS"] == "30.0"
    assert tick_env["ORB_TICK_CACHE_DAYS"] == "1"
    assert "ORB_WRDS_M1_ROOT" not in tick_env

    m1_root = tmp_path / "wrds_m1"
    m1 = parse_args(["--mode", "m1", "--bars", str(m1_root)])
    m1_env = app_environment(m1, data_path, output)
    assert m1_env["ORB_BACKTEST_MODE"] == "m1"
    assert m1_env["ORB_SOURCE_ROOT"] == str(m1_root.resolve())
    assert m1_env["ORB_WRDS_M1_ROOT"] == str(m1_root.resolve())
    assert m1_env["ORB_EXECUTION_MODEL"] == "m1_bar_path"
    assert m1_env["ORB_EXECUTION_TIMING"] == "bar_ohlc"
    assert "ORB_TICK_ROOT" not in m1_env and "ORB_WRDS_TICK_ROOT" not in m1_env
    assert "ORB_TICK_LATENCY_MS" not in m1_env


def test_wrds_date_slice_is_half_open_and_rejects_empty_ranges():
    index = pd.to_datetime([
        "2020-01-01 23:59", "2020-01-02 00:00",
        "2020-01-02 23:59", "2020-01-03 00:00",
    ])
    frame = pd.DataFrame({"close": range(4)}, index=index)

    selected = _slice_wrds_m1(frame, "2020-01-02", "2020-01-03")
    assert list(selected.index) == list(index[1:3])
    with pytest.raises(ValueError, match="half-open date range"):
        _slice_wrds_m1(frame, "2021-01-01", "2022-01-01")


def test_prepared_caches_are_mode_specific_and_input_fingerprinted(tmp_path, monkeypatch):
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(run_all, "ROOT", fake_repo)
    source = tmp_path / "wrds_m1" / "2020" / "QQQ_2020_M1.csv.gz"
    source.parent.mkdir(parents=True)
    frame = make_synthetic_m1(days=2, seed=3)
    frame.to_csv(source, compression={"method": "gzip", "mtime": 0})
    artifact = _input_artifact(source)
    expected_hash = sha256(source.read_bytes()).hexdigest()
    assert artifact == {
        "path": str(source.resolve()), "name": source.name,
        "size_bytes": source.stat().st_size, "sha256": expected_hash,
    }
    common = {"symbol": "QQQ", "start": None, "end": None}
    m1_args = SimpleNamespace(mode="m1", **common)
    tick_args = SimpleNamespace(mode="tick", **common)

    m1_path, m1_signature = _materialize_wrds_m1(frame, source.parent, [artifact], m1_args, {})
    tick_path, tick_signature = _materialize_wrds_m1(
        frame, tmp_path / "wrds_ticks", [artifact], tick_args,
        {"derived_bar_signature": "raw-tick-signature"},
    )
    assert m1_path.is_relative_to(fake_repo / ".cache" / "wrds_prepared" / "m1")
    assert tick_path.is_relative_to(fake_repo / ".cache" / "wrds_prepared" / "tick")
    assert m1_path != tick_path and m1_signature != tick_signature

    frame.iloc[0, frame.columns.get_loc("close")] += 0.01
    frame.iloc[0, frame.columns.get_loc("high")] += 0.01
    frame.to_csv(source, compression={"method": "gzip", "mtime": 0})
    changed = _input_artifact(source)
    changed_path, changed_signature = _materialize_wrds_m1(
        frame, source.parent, [changed], m1_args, {},
    )
    assert changed_signature != m1_signature and changed_path != m1_path


def test_wrds_m1_pipeline_filters_dates_and_reports_unmanifested_input(tmp_path, monkeypatch):
    bars = tmp_path / "wrds_m1" / "QQQ_SAMPLE_M1.csv.gz"
    bars.parent.mkdir()
    raw = make_synthetic_m1(days=5, seed=9)
    raw.to_csv(bars, compression={"method": "gzip", "mtime": 0})
    dates = sorted(raw.index.normalize().unique())
    start = dates[1].date().isoformat()
    end = dates[3].date().isoformat()
    output = tmp_path / "result"
    config = Path(run_all.__file__).parent / "configs" / "qqq.json"
    args = parse_args([
        "--mode", "m1", "--bars", str(bars), "--from", start, "--to", end,
        "--config", str(config), "--output", str(output), "--no-app", "--no-install",
    ])
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(run_all, "ROOT", fake_repo)

    data_path, result_path = pipeline(args)
    selected = load_bars(data_path)
    assert selected.index.min() >= pd.Timestamp(start)
    assert selected.index.max() < pd.Timestamp(end)
    assert data_path.is_relative_to(fake_repo / ".cache" / "wrds_prepared" / "m1")
    assert result_path == output
    source = json.loads((output / "manifest.json").read_text(encoding="utf-8"))["source"]
    assert source["manifest_validation"] == "manifests not supplied"
    assert source["requested_start"] == start and source["requested_end_exclusive"] == end
    assert source["input_artifacts"] == [_input_artifact(bars)]
