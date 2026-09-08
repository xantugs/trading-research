"""Load strict, reviewable strategy configurations and CLI overrides."""
import json
from dataclasses import fields
from pathlib import Path

from .engine import Params


def load_params(path: str | Path | None = None, overrides: list[str] | None = None) -> Params:
    values = json.loads(Path(path).read_text(encoding="utf-8-sig")) if path else {}
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object of Params fields")
    valid = {f.name for f in fields(Params)}
    for override in overrides or []:
        if "=" not in override:
            raise ValueError(f"Expected key=value, got {override!r}")
        key, value = override.split("=", 1)
        try:
            values[key] = json.loads(value)
        except json.JSONDecodeError:
            values[key] = value
    unknown = set(values) - valid
    if unknown:
        raise ValueError(f"Unknown parameters: {', '.join(sorted(unknown))}")
    return Params(**values)
