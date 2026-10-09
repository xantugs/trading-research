"""Decile portfolios for the JKX models and benchmarks, against the paper's numbers.

    python -m scripts.jkx_evaluate --benchmarks-only        # gate 1: MOM, STR, WSTR from the I5 metadata
    python -m scripts.jkx_evaluate                          # every model in <data>/predictions

Writes outputs/jkx/report.md and one ledger row per evaluation.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from orb_research.jkx_images import FREQ_OF_HORIZON
from orb_research.jkx_portfolio import PAPER_HL_SHARPE, decile_returns, hl_turnover, summarize
from orb_research.vault import record_run

BENCHMARKS = {"MOM": "mom", "STR": "str", "WSTR": "wstr"}


def evaluate(frame: pd.DataFrame, score: str, label: str, freq: str, rows: list[dict]) -> None:
    for scheme, weight, top in (("ew", None, None), ("vw", "mcap", None), ("ew500", None, 500), ("vw500", "mcap", 500)):
        table = decile_returns(frame, score, weight=weight, top=top)
        s = summarize(table["H-L"], freq)
        paper = PAPER_HL_SHARPE.get((label, scheme))
        rows.append({"model": label, "weights": scheme, "periods": s["periods"], "ann_ret": s["ann_ret"],
                     "sharpe": s["sharpe"], "t_nw": s["t_nw"], "paper_sharpe": paper,
                     "ratio": s["sharpe"] / paper if paper else None,
                     "low": table[1].mean(), "high": table[10].mean(),
                     "turnover": hl_turnover(frame, score, freq, weight=weight) if top is None else None})


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=Path("C:/data/jkx"))
    p.add_argument("--test-years", default="2001-2019")
    p.add_argument("--benchmarks-only", action="store_true")
    p.add_argument("--out", type=Path, default=Path("outputs/jkx"))
    p.add_argument("--suffix", default="", help="evaluate predictions/I<d>R<h><suffix>.parquet only")
    a = p.parse_args()
    s0, s1 = map(int, a.test_years.split("-"))
    rows: list[dict] = []

    for horizon, freq in FREQ_OF_HORIZON.items():
        folder = a.data / f"I5_{freq}"
        files = [folder / f"{y}_meta.parquet" for y in range(s0, s1 + 1) if (folder / f"{y}_meta.parquet").exists()]
        if not files:
            continue
        meta = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        for name, col in BENCHMARKS.items():
            evaluate(meta, col, f"{name}/R{horizon}", freq, rows)
    if not a.benchmarks_only:
        for path in sorted((a.data / "predictions").glob(f"I*R*{a.suffix}.parquet")):
            stem = path.stem[: len(path.stem) - len(a.suffix)] if a.suffix else path.stem
            if not stem[1:].replace("R", "").isdigit():
                continue                                    # a file with another suffix
            d, h = stem[1:].split("R")
            pred = pd.read_parquet(path)
            pred = pred[(pred["date"].dt.year >= s0) & (pred["date"].dt.year <= s1)]
            evaluate(pred, "prob", f"I{d}/R{h}", FREQ_OF_HORIZON[int(h)], rows)

    report = pd.DataFrame(rows)
    a.out.mkdir(parents=True, exist_ok=True)
    pd.set_option("display.width", 200)
    print(report.round(3).to_string(index=False))
    report.to_csv(a.out / "report.csv", index=False)
    (a.out / "report.md").write_text(f"# JKX replication, test years {a.test_years}\n\n```\n"
                                     + report.round(3).to_string(index=False) + "\n```\n", encoding="utf-8")
    record_run("jkx/evaluate" + ("_benchmarks" if a.benchmarks_only else ""), {"test_years": a.test_years},
               {f"{r['model']}|{r['weights']}": round(r["sharpe"], 4) for r in rows})


if __name__ == "__main__":
    main()
