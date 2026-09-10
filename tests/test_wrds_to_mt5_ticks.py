import json
from datetime import date

import pandas as pd
import pytest

from orb.ticks import TickStore
from scripts.verify_mt5_ticks import main as verify_main
from scripts.wrds_to_mt5_ticks import (
    MT5_COLUMNS,
    convert_day,
    discover_quote_days,
    parse_args,
    price_change_mask,
    verify_mt5_csv,
)


def raw_day(root, day="2020-01-02"):
    month = root / day[:4] / day[:7]
    month.mkdir(parents=True, exist_ok=True)
    path = month / f"QQQ_{day}_quotes.parquet"
    pd.DataFrame({
        "date": [day] * 6,
        "time_m": [
            "00:00:00.000000", "00:00:00.000100", "00:00:00.000200",
            "12:00:00.000000", "13:00:00.000000", "23:59:59.999999",
        ],
        "time_m_nano": [0, 0, 0, 0, 0, 999],
        "best_bid": [100.00, 100.00, 100.01, 0.0, 101.0, 100.05],
        "best_ask": [100.01, 100.01, 100.02, 100.01, 100.0, 100.06],
    }).to_parquet(path, index=False)
    return path


def test_price_change_cleaning_removes_only_unchanged_states():
    bid = pd.Series([100.0, 100.0, 100.01, 100.01]).to_numpy()
    ask = pd.Series([100.01, 100.01, 100.02, 100.03]).to_numpy()
    assert price_change_mask(bid, ask).tolist() == [True, False, True, True]


def test_real_shape_converts_full_new_york_day_to_mt5_ticks(tmp_path):
    root = tmp_path / "wrds_ticks"
    source = raw_day(root)
    store = TickStore(root, strategy_offset_hours=0, symbol="QQQ", max_cache_days=0)
    output = tmp_path / "mt5"

    manifest = convert_day(
        store, date(2020, 1, 2), output, digits=2, chunk_rows=2,
        keep_unchanged=False, overwrite=False,
    )
    target = output / "QQQ_2020-01-02_ticks.csv"
    frame = pd.read_csv(target, sep="\t", dtype=str)

    assert list(frame.columns) == MT5_COLUMNS
    assert frame["<DATE>"].tolist() == ["2020.01.02"] * 3
    assert frame["<TIME>"].tolist() == ["00:00:00.000", "00:00:00.000", "23:59:59.999"]
    assert frame["<BID>"].tolist() == ["100.00", "100.01", "100.05"]
    assert frame["<ASK>"].tolist() == ["100.01", "100.02", "100.06"]
    assert set(frame["<LAST>"]) == {"0.00"} and set(frame["<VOLUME>"]) == {"0"}
    assert manifest["clock"] == "America/New_York wall time; no strategy offset"
    assert manifest["window"].startswith("00:00:00.000")
    assert manifest["source_rows"] == 6 and manifest["valid_nbbo_rows"] == 4
    assert manifest["dropped_invalid"] == 1 and manifest["dropped_crossed"] == 1
    assert manifest["removed_unchanged_price_states"] == 1 and manifest["rows"] == 3
    assert manifest["adjacent_same_millisecond_rows"] == 1
    assert manifest["source_file"] == str(source.resolve())
    assert verify_mt5_csv(target, expected_rows=3)["rows"] == 3
    assert verify_main([str(target)]) == 0


def test_discovery_is_date_bounded_and_duplicate_safe(tmp_path):
    root = tmp_path / "ticks"
    first = raw_day(root, "2020-01-02")
    raw_day(root, "2020-01-03")
    found = discover_quote_days(root, "QQQ", date(2020, 1, 2), date(2020, 1, 3))
    assert found == [(date(2020, 1, 2), first)]


def test_converter_refuses_implicit_multi_month_disk_run():
    args = parse_args(["--from", "2020-01-01", "--to", "2020-02-01"])
    assert args.start == date(2020, 1, 1) and args.end == date(2020, 2, 1)
    with pytest.raises(SystemExit):
        parse_args(["--from", "2018-01-01", "--to", "2023-01-01"])
    allowed = parse_args([
        "--from", "2018-01-01", "--to", "2023-01-01", "--allow-large-range",
    ])
    assert allowed.end == date(2023, 1, 1)


def test_verifier_detects_tampered_manifest(tmp_path):
    root = tmp_path / "ticks"
    raw_day(root)
    output = tmp_path / "mt5"
    convert_day(
        TickStore(root, strategy_offset_hours=0, symbol="QQQ", max_cache_days=0),
        date(2020, 1, 2), output, digits=2, chunk_rows=10,
        keep_unchanged=False, overwrite=False,
    )
    manifest_path = output / "QQQ_2020-01-02_ticks.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rows"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_main([str(output)]) == 1
