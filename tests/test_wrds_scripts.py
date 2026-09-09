import json
import sys

import pandas as pd

from scripts.verify_tick_download import main as verify_main
from scripts.wrds_extract_taq import parse_args as parse_tick_args


def test_tick_extractor_accepts_complete_daily_endpoint():
    args = parse_tick_args(["--from", "2022-01-03", "--to", "2022-01-04",
                            "--end-time", "24:00:00"])
    assert args.end_time == "24:00:00"


def test_tick_verifier_requires_files_and_monthly_manifests(tmp_path, monkeypatch, capsys):
    month = tmp_path / "2018" / "2018-01"
    month.mkdir(parents=True)
    tick = month / "QQQ_2018-01-02_quotes.parquet"
    pd.DataFrame({"best_bid": [100.0], "best_ask": [100.01]}).to_parquet(tick)
    (month / "manifest.json").write_text(json.dumps({"files": [{"file": tick.name, "rows": 1}]}))

    monkeypatch.setattr(sys, "argv", ["verify_tick_download.py", str(tmp_path),
                                      "--expected", "1", "--expected-manifests", "1"])
    assert verify_main() == 0
    assert "1 present, 1 declared" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["verify_tick_download.py", str(tmp_path),
                                      "--expected", "2", "--expected-manifests", "2"])
    assert verify_main() == 1
    output = capsys.readouterr().out
    assert "1 file(s) never arrived" in output
    assert "1 monthly manifest(s) never arrived" in output
