"""The vault and the run ledger (docs/PIPELINE_DESIGN.md, layers 4 and 7).

Sealed date ranges are listed in `vault.json` at the repository root. Loaders call `check()`
before reading data. A read that touches a sealed range is refused unless the experiment has
unsealed it, and unsealing needs a plan file that is committed and unchanged, with no
uncommitted changes to tracked files. Each experiment unseals a range once; every unseal and
every later read is appended to `outputs/vault_log.jsonl`.

`record_run()` appends one line per run to `outputs/ledger.jsonl` (code commit, dirty flag,
config, metrics). Its run count is the number of trials for a deflated Sharpe ratio.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VAULT_FILE = ROOT / "vault.json"
VAULT_LOG = ROOT / "outputs" / "vault_log.jsonl"
LEDGER = ROOT / "outputs" / "ledger.jsonl"


class VaultError(RuntimeError):
    pass


def _git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_log(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def sealed_ranges(dataset: str, vault_file: Path = VAULT_FILE) -> list[dict]:
    spec = json.loads(vault_file.read_text(encoding="utf-8"))
    return [r for r in spec["ranges"] if dataset in r["datasets"]]


def is_unsealed(experiment: str | None, range_name: str, log: Path = VAULT_LOG) -> bool:
    return experiment is not None and any(
        e["event"] == "unseal" and e["experiment"] == experiment and e["range"] == range_name for e in _read_log(log))


def check(dataset: str, start, end, experiment: str | None = None,
          vault_file: Path = VAULT_FILE, log: Path = VAULT_LOG) -> None:
    """Raise VaultError if [start, end] overlaps a sealed range this experiment has not unsealed."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    for r in sealed_ranges(dataset, vault_file):
        if start <= pd.Timestamp(r["end"]) and end >= pd.Timestamp(r["start"]):
            if not is_unsealed(experiment, r["name"], log):
                raise VaultError(f"{dataset} {start.date()}..{end.date()} overlaps the sealed range "
                                 f"'{r['name']}' ({r['start']}..{r['end']}); unseal it with a committed plan")
            _append(log, {"event": "read", "time": _now(), "experiment": experiment, "range": r["name"],
                          "dataset": dataset, "start": str(start.date()), "end": str(end.date())})


def unseal(experiment: str, range_name: str, plan: Path, root: Path = ROOT,
           vault_file: Path = VAULT_FILE, log: Path = VAULT_LOG) -> dict:
    """Unseal a range for one experiment, once. The plan must be committed and unchanged, and
    the working tree must have no uncommitted changes to tracked files."""
    plan = Path(plan).resolve()
    names = {r["name"] for r in json.loads(vault_file.read_text(encoding="utf-8"))["ranges"]}
    if range_name not in names:
        raise VaultError(f"no sealed range named '{range_name}'")
    if is_unsealed(experiment, range_name, log):
        raise VaultError(f"experiment '{experiment}' has already unsealed '{range_name}'")
    rel = plan.relative_to(root).as_posix()
    if _git("ls-files", "--error-unmatch", rel, root=root).returncode != 0:
        raise VaultError(f"plan {rel} is not committed")
    if _git("status", "--porcelain", "--untracked-files=no", root=root).stdout.strip():
        raise VaultError("uncommitted changes to tracked files; commit the code and plan first")
    row = {"event": "unseal", "time": _now(), "experiment": experiment, "range": range_name, "plan": rel,
           "plan_commit": _git("log", "-1", "--format=%H", "--", rel, root=root).stdout.strip(),
           "head": _git("rev-parse", "HEAD", root=root).stdout.strip()}
    _append(log, row)
    return row


def record_run(experiment: str, config: dict, metrics: dict, ledger: Path = LEDGER, root: Path = ROOT) -> dict:
    row = {"time": _now(), "experiment": experiment,
           "commit": _git("rev-parse", "HEAD", root=root).stdout.strip(),
           "dirty": bool(_git("status", "--porcelain", "--untracked-files=no", root=root).stdout.strip()),
           "config": config, "metrics": metrics}
    _append(ledger, row)
    return row


def n_runs(experiment_prefix: str = "", ledger: Path = LEDGER) -> int:
    return sum(1 for r in _read_log(ledger) if r["experiment"].startswith(experiment_prefix))
