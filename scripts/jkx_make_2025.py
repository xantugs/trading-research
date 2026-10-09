"""Write C:/data/crsp/dsf_2025.parquet in the legacy layout from CRSP's new format, so the Gate-3
image builder reads 2025 like any other year. Refuses unless the 2019 parity check passed
(scripts/jkx_parity_v2.py, docs/JKX_REPLICATION_PLAN.md).

    .venv/Scripts/python -m scripts.jkx_make_2025
"""
from __future__ import annotations

from orb_research import crsp, crsp_v2
from orb_research.crsp import DTYPES


def main() -> None:
    if not (crsp.DEFAULT_DIR / "v2" / "PARITY_PASSED").exists():
        raise SystemExit("2019 parity has not passed; 2025 stays out of the test")
    out = crsp.DEFAULT_DIR / "dsf_2025.parquet"
    if out.exists():
        raise SystemExit(f"{out} exists; raw caches are not overwritten")
    frame = crsp_v2.load_year(2025)
    frame = frame.astype({k: v for k, v in DTYPES.items() if k in frame.columns}).sort_values(["date", "permno"])
    frame.to_parquet(out, index=False)
    print(f"{out}: {len(frame):,} rows from the new format")


if __name__ == "__main__":
    main()
