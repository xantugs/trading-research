from pathlib import Path

import pytest

import run_all


def test_mt5_prepare_requires_an_end_exclusive_range():
    with pytest.raises(SystemExit):
        run_all.parse_mt5_args([])
    args = run_all.parse_mt5_args(["--from", "2018-01-02", "--to", "2018-01-03"])
    assert args.start == "2018-01-02"
    assert args.end == "2018-01-03"


def test_mt5_verify_is_independent_of_raw_data():
    args = run_all.parse_mt5_args(["--verify-only", "--output-dir", "C:/prepared"])
    assert args.verify_only
    assert args.output_dir == Path("C:/prepared")
    with pytest.raises(SystemExit):
        run_all.parse_mt5_args(["--verify-only", "--from", "2018-01-02"])


def test_mt5_prepare_delegates_every_step_to_setup_script(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(run_all, "command", lambda argv, **kwargs: seen.append(argv))
    args = run_all.parse_mt5_args([
        "--from", "2018-01-02", "--to", "2018-01-03",
        "--input-root", str(tmp_path / "raw"),
        "--output-dir", str(tmp_path / "prepared"),
        "--mql5-root", str(tmp_path / "MQL5"),
        "--overwrite", "--skip-dependencies", "--skip-compile",
    ])
    assert run_all.run_mt5(args) == 0
    rendered = [str(value) for value in seen[0]]
    assert rendered[:5] == ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]
    assert str(run_all.ROOT / "scripts" / "setup_mt5_wrds.ps1") in rendered
    assert "-InputRoot" in rendered and str(tmp_path / "raw") in rendered
    assert "-OutputDir" in rendered and str(tmp_path / "prepared") in rendered
    assert "-Mql5Root" in rendered and str(tmp_path / "MQL5") in rendered
    assert rendered[-3:] == ["-Overwrite", "-SkipDependencies", "-SkipCompile"]


def test_mt5_verify_calls_the_archive_verifier(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(run_all, "command", lambda argv, **kwargs: seen.append(argv))
    args = run_all.parse_mt5_args(["--verify-only", "--output-dir", str(tmp_path)])
    assert run_all.run_mt5(args) == 0
    rendered = [str(value) for value in seen[0]]
    assert str(run_all.ROOT / "scripts" / "verify_mt5_ticks.py") in rendered
    assert str(tmp_path) in rendered
    assert rendered[-2:] == ["--pattern", "QQQ_*_ticks.bin"]
