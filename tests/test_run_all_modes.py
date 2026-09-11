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
    DEFAULT_SESSION_END,
    DEFAULT_SESSION_START,
    DEFAULT_WRDS_M1_END,
    DEFAULT_WRDS_M1_ROOT,
    DEFAULT_WRDS_TICK_ROOT,
    DEFAULT_WRDS_M1_START,
    _input_artifact,
    _materialize_wrds_m1,
    _prepare_tick_signal_bars,
    _slice_wrds_m1,
    _tick_source_signature,
    _unreplayable_executions,
    leg_args,
    parse_args,
    pipeline,
    run_both,
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
        "start_time": DEFAULT_SESSION_START, "end_time": DEFAULT_SESSION_END, "point": 0.01,
        "strategy_offset_hours": 7,
    })]
    assert years.is_relative_to(fake_repo / ".cache/wrds_tick_m1")
    assert info["tick_root"] == str(tick_root.resolve())
    assert str(sibling_m1) not in str(years)
    assert _prepare_tick_signal_bars(args)[0] == years
    assert len(calls) == 1


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
    dates = sorted(raw.index.normalize().unique())
    rejected_date = dates[1]
    missing = raw.index[(raw.index.normalize() == rejected_date)
                        & (raw.index.hour == 16) & (raw.index.minute == 35)][0]
    raw = raw.drop(missing)
    raw.to_csv(bars, compression={"method": "gzip", "mtime": 0})
    start = dates[1].date().isoformat()
    end = dates[3].date().isoformat()
    output = tmp_path / "result"
    config = Path(run_all.__file__).parent / "configs" / "qqq.json"
    args = parse_args([
        "--mode", "m1", "--bars", str(bars), "--from", start, "--to", end,
        "--config", str(config), "--output", str(output), "--no-install",
    ])
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(run_all, "ROOT", fake_repo)

    data_path, result_path = pipeline(args)
    selected = load_bars(data_path)
    assert selected.index.min() >= pd.Timestamp(start)
    assert selected.index.max() < pd.Timestamp(end)
    assert rejected_date not in selected.index.normalize()
    assert data_path.is_relative_to(fake_repo / ".cache" / "wrds_prepared" / "m1")
    assert result_path == output
    source = json.loads((output / "manifest.json").read_text(encoding="utf-8"))["source"]
    assert source["manifest_validation"] == "manifests not supplied"
    assert source["requested_start"] == start and source["requested_end_exclusive"] == end
    assert source["input_artifacts"] == [_input_artifact(bars)]
    prepared = json.loads((data_path.parent / "manifest.json").read_text(encoding="utf-8"))
    assert prepared["excluded_incomplete_opening_range_dates"] == [
        rejected_date.date().isoformat()
    ]


def test_m1_mode_defaults_to_the_tick_comparable_window():
    """M1 holds 2012-2024; default it to the raw archive's span so modes compare."""
    default = parse_args(["--mode", "m1"])
    assert default.start == DEFAULT_WRDS_M1_START == "2018-01-01"
    assert default.end == DEFAULT_WRDS_M1_END == "2023-01-01"

    explicit = parse_args(["--mode", "m1", "--from", "2019-01-01", "--to", "2020-01-01"])
    assert explicit.start == "2019-01-01" and explicit.end == "2020-01-01"

    partial = parse_args(["--mode", "m1", "--from", "2021-06-01"])
    assert partial.start == "2021-06-01" and partial.end == DEFAULT_WRDS_M1_END

    full = parse_args(["--mode", "m1", "--full-history"])
    assert full.start is None and full.end is None


def test_tick_mode_window_still_follows_its_root():
    """The tick root defines its own span; clipping it would hide a wider archive."""
    tick = parse_args(["--mode", "tick"])
    assert tick.start is None and tick.end is None


@pytest.mark.parametrize("argv, match", [
    (["--mode", "m1", "--full-history", "--from", "2019-01-01"], "cannot be combined"),
    (["--mode", "m1", "--full-history", "--to", "2019-01-01"], "cannot be combined"),
    (["--mode", "tick", "--full-history"], "applies to --mode m1"),
    (["--source", "demo", "--full-history"], "requires WRDS M1 mode"),
])
def test_full_history_is_rejected_where_it_has_no_meaning(argv, match):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_default_window_changes_the_prepared_signature(tmp_path):
    """A windowed run must not reuse a full-history run's prepared artifact."""
    windowed = parse_args(["--mode", "m1"])
    full = parse_args(["--mode", "m1", "--full-history"])
    root = tmp_path / "wrds_m1"
    root.mkdir()
    artifact = root / "QQQ_M1.csv.gz"
    artifact.write_bytes(b"fixture")
    inputs = [_input_artifact(artifact)]
    assert run_all._wrds_prepared_signature(root, inputs, windowed, {}) != \
        run_all._wrds_prepared_signature(root, inputs, full, {})


def test_bare_launcher_defaults_to_fast_m1_mode():
    """`python run_all.py` alone must not start the multi-hour tick replay."""
    args = parse_args([])
    assert args.mode == "m1"
    assert args.bars == DEFAULT_WRDS_M1_ROOT
    assert args.tick_root is None


def test_both_legs_are_isolated_from_each_other(tmp_path):
    args = parse_args(["--mode", "both"])

    m1 = leg_args(args, "m1", tmp_path / "m1")
    assert m1.mode == "m1" and m1.tick_root is None and m1.bars == DEFAULT_WRDS_M1_ROOT
    assert m1.start == DEFAULT_WRDS_M1_START and m1.end == DEFAULT_WRDS_M1_END

    tick = leg_args(args, "tick", tmp_path / "tick")
    assert tick.mode == "tick" and tick.bars is None
    assert tick.tick_root == DEFAULT_WRDS_TICK_ROOT
    # The default M1 window must not clip a tick root that may span more years.
    assert tick.start is None and tick.end is None
    # Neither leg may re-run installs or checks the parent already handled.
    assert m1.no_install and tick.no_install and not m1.check and not tick.check


def test_explicit_dates_apply_to_both_legs(tmp_path):
    args = parse_args(["--mode", "both", "--from", "2019-01-01", "--to", "2020-01-01"])
    assert args.explicit_dates
    for mode in ("m1", "tick"):
        leg = leg_args(args, mode, tmp_path / mode)
        assert leg.start == "2019-01-01" and leg.end == "2020-01-01"


def test_run_both_writes_one_directory_per_mode(tmp_path, monkeypatch):
    seen = []

    def fake_pipeline(leg):
        seen.append((leg.mode, leg.output))
        leg.output.mkdir(parents=True)
        stats = {"trades": 10 if leg.mode == "m1" else 9,
                 "total_r": 1.5 if leg.mode == "m1" else 1.0,
                 "return_pct": 3.0 if leg.mode == "m1" else 2.0}
        (leg.output / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
        return leg.output / "bars.csv", leg.output

    monkeypatch.setattr(run_all, "pipeline", fake_pipeline)
    args = parse_args(["--mode", "both", "--output", str(tmp_path / "run")])
    _, output = run_both(args)

    assert [mode for mode, _ in seen] == ["m1", "tick"]
    assert (tmp_path / "run" / "m1" / "stats.json").exists()
    assert (tmp_path / "run" / "tick" / "stats.json").exists()
    assert output == tmp_path / "run" / "tick"


def test_run_both_refuses_a_populated_run_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(run_all, "pipeline", lambda leg: (leg.output, leg.output))
    existing = tmp_path / "run"
    existing.mkdir()
    (existing / "leftover.txt").write_text("x", encoding="utf-8")
    args = parse_args(["--mode", "both", "--output", str(existing)])
    with pytest.raises(ValueError, match="already exists"):
        run_both(args)


@pytest.mark.parametrize("argv, match", [
    (["--mode", "both", "--ea-log", "x.csv"], None),
])
def test_both_mode_rejects_incoherent_combinations(argv, match):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_both_mode_still_rejects_a_tick_root_inside_the_m1_root(tmp_path, monkeypatch):
    """Running both modes must not weaken the no-overlap guarantee."""
    m1_root = tmp_path / "wrds_m1"
    (m1_root / "nested").mkdir(parents=True)
    monkeypatch.setattr(run_all, "DEFAULT_WRDS_M1_ROOT", m1_root)
    with pytest.raises(SystemExit):
        parse_args(["--mode", "both", "--tick-root", str(m1_root / "nested")])


def test_both_mode_rejects_two_custom_overlapping_roots(tmp_path):
    root = tmp_path / "licensed_data"
    nested = root / "ticks"
    with pytest.raises(SystemExit):
        parse_args(["--mode", "both", "--bars", str(root), "--tick-root", str(nested)])
    with pytest.raises(SystemExit):
        parse_args(["--mode", "both", "--bars", str(nested), "--tick-root", str(root)])


def test_session_window_covers_premarket_through_after_hours():
    """Keep every minute the market quotes, not just the regular session."""
    args = parse_args([])
    assert args.session_start == DEFAULT_SESSION_START == "04:00:00"
    assert args.session_end == DEFAULT_SESSION_END == "20:00:00"

    narrowed = parse_args(["--session-start", "09:30:00", "--session-end", "16:00:00"])
    assert narrowed.session_start == "09:30:00" and narrowed.session_end == "16:00:00"


def test_session_window_reaches_both_legs_and_the_cache_key(tmp_path):
    args = parse_args(["--mode", "both", "--session-start", "08:00:00",
                       "--session-end", "17:00:00"])
    for mode in ("m1", "tick"):
        leg = leg_args(args, mode, tmp_path / mode)
        assert leg.session_start == "08:00:00" and leg.session_end == "17:00:00"

    tick_root = tmp_path / "wrds_ticks"
    tick_inventory(tick_root)
    wide = parse_args(["--mode", "tick", "--tick-root", str(tick_root)])
    narrow = parse_args(["--mode", "tick", "--tick-root", str(tick_root),
                         "--session-start", "09:30:00", "--session-end", "16:00:00"])
    # A different window must not silently reuse the previous derived candles.
    assert _tick_source_signature(tick_root, wide) != _tick_source_signature(tick_root, narrow)


@pytest.mark.parametrize("argv", [
    ["--session-start", "9:30"],
    ["--session-end", "25:00:00"],
    ["--session-start", "16:00:00", "--session-end", "09:30:00"],
])
def test_session_window_rejects_nonsense(argv):
    with pytest.raises(SystemExit):
        parse_args(argv)


def test_unreplayable_executions_are_recorded_not_fatal_by_default():
    """One thin session must not abort a multi-hour replay."""
    assert parse_args(["--mode", "tick"]).tick_strict is False
    assert parse_args(["--mode", "tick", "--tick-strict"]).tick_strict is True


def test_tick_strict_reaches_both_legs(tmp_path):
    args = parse_args(["--mode", "both", "--tick-strict"])
    for mode in ("m1", "tick"):
        assert leg_args(args, mode, tmp_path / mode).tick_strict is True


@pytest.mark.parametrize("flag", [
    "--tick-latency-ms", "--tick-max-entry-delay-ms", "--tick-max-eod-delay-ms",
])
def test_both_mode_validates_tick_numbers_before_starting_m1(flag):
    with pytest.raises(SystemExit):
        parse_args(["--mode", "both", flag, "-1"])


def test_unreplayable_summary_separates_data_failures_from_strategy_skips():
    days = pd.DataFrame(
        {
            "long": [
                "skipped: eod_quote_delayed: first valid EOD quote is 93896.1 ms late",
                "",
                "skipped: entry beyond stop",
                "skipped: missing_entry_quote: none in window",
            ],
            "short": [
                "",
                "skipped: eod_quote_delayed: 40000.0 ms late",
                "skipped: spread too wide",
                "",
            ],
        },
        index=pd.to_datetime(["2019-07-03", "2019-11-29", "2020-01-02", "2020-11-27"]),
    )
    summary = _unreplayable_executions(days)

    # Plain-English strategy skips are decisions, not data gaps, and must not count.
    assert summary["count"] == 3
    assert summary["by_code"] == {"eod_quote_delayed": 2, "missing_entry_quote": 1}
    assert summary["dates"] == ["2019-07-03", "2019-11-29", "2020-11-27"]


def test_unreplayable_summary_is_empty_for_a_clean_run():
    days = pd.DataFrame({"long": ["", "skipped: zero range"], "short": ["", ""]},
                        index=pd.to_datetime(["2020-01-02", "2020-01-03"]))
    assert _unreplayable_executions(days) == {"count": 0, "by_code": {}, "dates": []}
