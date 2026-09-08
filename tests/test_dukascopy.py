import lzma
from datetime import datetime
from urllib.error import HTTPError

import pandas as pd
import pytest

import fetch_dukascopy as duk


def payload(second=0, volume=1.5):
    return lzma.compress(duk.CANDLE.pack(second, 100000, 101000, 99000, 102000, volume))


def test_binary_layout_and_zero_volume():
    frame = duk.decode_candles(payload(volume=0), datetime(2025, 1, 1), 1000)
    assert frame.iloc[0].to_dict() == {
        "open": 100, "high": 102, "low": 99, "close": 101, "tick_volume": 0, "spread": 0}


def test_truncated_record_rejected():
    with pytest.raises(ValueError, match="Truncated"):
        duk.decode_candles(lzma.compress(b"x"), datetime(2025, 1, 1), 1000)


def test_tick_layout():
    raw = lzma.compress(duk.TICK.pack(1000, 102000, 101000, 3, 2))
    frame = duk.decode_ticks(raw, datetime(2025, 1, 1), 1000)
    assert frame.index[0] == pd.Timestamp("2025-01-01 00:00:01")
    assert frame.iloc[0].to_dict() == {"ask": 102, "bid": 101, "ask_volume": 3, "bid_volume": 2}


def test_zero_based_month():
    assert duk.day_url("EURUSD", datetime(2025, 1, 5), "x").endswith("/2025/00/05/x")


def test_cache_reused_and_missing_not_cached(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return payload()
    calls = []
    def open_url(request, **kw):
        calls.append(request.full_url)
        return Response()
    monkeypatch.setattr(duk, "urlopen", open_url)
    assert duk.fetch("https://example.com/a", cache=tmp_path) == payload()
    assert duk.fetch("https://example.com/a", cache=tmp_path) == payload()
    assert len(calls) == 1
    def missing(request, **kw):
        raise HTTPError(request.full_url, 404, "missing", {}, None)
    monkeypatch.setattr(duk, "urlopen", missing)
    assert duk.fetch("https://example.com/b", cache=tmp_path) is None
    assert len(list(tmp_path.glob("*.bi5"))) == 1


def test_retries_do_not_turn_failures_into_missing(monkeypatch):
    calls = []
    def unavailable(request, **kw):
        calls.append(1)
        raise HTTPError(request.full_url, 503, "busy", {}, None)
    monkeypatch.setattr(duk, "urlopen", unavailable)
    monkeypatch.setattr(duk.time, "sleep", lambda _: None)
    with pytest.raises(HTTPError):
        duk.fetch("https://example.com/a", retries=2)
    assert len(calls) == 2


def test_download_includes_sunday_and_end_exclusive(tmp_path, monkeypatch):
    urls = []
    def fetch(url, **kwargs):
        urls.append(url)
        return payload(second=14 * 3600)
    monkeypatch.setattr(duk, "fetch", fetch)
    meta = duk.download("USATECHIDXUSD", "2025-03-08", "2025-03-11", tmp_path, workers=1)
    assert len(urls) == 3
    assert any("/08/" in url for url in urls)
    assert any("/09/" in url for url in urls)
    assert any("/10/" in url for url in urls)
    assert not any("/11/" in url for url in urls)
    assert meta["rows_m1"] == 3
    assert meta["spread_model"] == "constant assumption"
    assert (tmp_path / "USATECHIDXUSD_metadata.json").exists()
    assert meta["format_version"] == 1
    assert {"m1_sha256", "m15_sha256", "mt5_sha256"} <= set(meta)


def test_exact_download_is_verified_and_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(duk, "fetch", lambda *args, **kwargs: payload(second=14 * 3600))
    first = duk.download("USATECHIDXUSD", "2025-03-10", "2025-03-12", tmp_path, workers=1)
    monkeypatch.setattr(duk, "fetch", lambda *args, **kwargs: pytest.fail("network must not run"))
    second = duk.download("USATECHIDXUSD", "2025-03-10", "2025-03-12", tmp_path, workers=1)
    assert first["m1_sha256"] == second["m1_sha256"]
    assert second["reused_local_dataset"] is True


def test_modified_dataset_is_not_reused(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(duk, "fetch", lambda *args, **kwargs: payload(second=14 * 3600))
    duk.download("USATECHIDXUSD", "2025-03-10", "2025-03-12", tmp_path, workers=1)
    with (tmp_path / "USATECHIDXUSD_M1.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    monkeypatch.setattr(duk, "fetch", lambda *args, **kwargs: calls.append(1) or payload(second=14 * 3600))
    duk.download("USATECHIDXUSD", "2025-03-10", "2025-03-12", tmp_path, workers=1)
    assert calls


def test_failed_download_only_submits_one_worker_batch(tmp_path, monkeypatch):
    calls = []

    def fail(url, **kwargs):
        calls.append(url)
        raise ConnectionError("blocked")

    monkeypatch.setattr(duk, "fetch", fail)
    with pytest.raises(ConnectionError, match="blocked"):
        duk.download("USATECHIDXUSD", "2025-03-01", "2025-03-11", tmp_path, workers=2)
    assert len(calls) == 2


@pytest.mark.parametrize("kwargs", [
    {"start": "2025-03-10", "end": "2025-03-09"}, {"scale": -1},
    {"symbol": "../bad"}, {"symbol": "UNKNOWN"}, {"workers": 0}, {"spread": -1},
])
def test_invalid_request_before_network(tmp_path, monkeypatch, kwargs):
    monkeypatch.setattr(duk, "fetch", lambda *a, **k: pytest.fail("network must not run"))
    defaults = dict(symbol="USATECHIDXUSD", start="2025-03-08", end="2025-03-11", out=tmp_path)
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        duk.download(**defaults)
