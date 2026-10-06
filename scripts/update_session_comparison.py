"""Compare published and research-sizing A/B results without changing either run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _metrics(row: dict) -> str:
    return (f"{row['trades']} / {row['mean_r']:+.4f} / {row['profit_factor']:.3f} / "
            f"{row['return_pct']:+.2f}% / {row['max_closed_dd_pct']:.2f}%")


def _sweep_caveats(sweep_dir: Path, sweep: dict) -> str:
    """Describe why the sweep's positive headline mean is not evidence."""
    trades = pd.read_csv(sweep_dir / "oos_trades.csv")
    total = float(trades.R_result.sum())
    top = trades.R_result.nlargest(5)
    rest = trades.R_result.drop(top.index)
    cells = sweep["stability"]["positive_cells_by_fold"]
    counts = [cells[str(fold)] if str(fold) in cells else cells[fold] for fold in range(1, 5)]
    order = ", ".join(str(value) for value in counts[:-1]) + f" and {counts[-1]} of 16"
    return (
        f"Two descriptions keep the sweep's positive mean in proportion. Its total of {total:+.2f} R over "
        f"{len(trades)} trades includes {float(top.sum()):+.2f} R from the five largest winners alone; the "
        f"other {len(rest)} trades average {float(rest.mean()):+.4f} R and the median trade loses "
        f"({float(trades.R_result.median()):+.4f} R). And the training surface moves as an era rather than a "
        f"parameter structure: cells with positive mean R run {order} across the four training windows, so the "
        f"selection statistic largely measures which years are in the window. Training mean R did not predict "
        f"the following year. See `robustness.md` beside the sweep report."
    )


def write_comparison(sizing_dir: Path, sweep_dir: Path, output: Path) -> Path:
    published = json.loads((ROOT / "configs" / "published_baselines.json").read_text(encoding="utf-8"))
    sizing = json.loads((sizing_dir / "research_sizing_summary.json").read_text(encoding="utf-8"))
    a_dir = ROOT / "outputs" / "strategy_a_research_sizing" / sizing_dir.name
    a_research = json.loads((a_dir / "summary.json").read_text(encoding="utf-8"))
    sweep = json.loads((sweep_dir / "summary.json").read_text(encoding="utf-8"))
    if sizing["holdout_used"] or sweep["holdout_used"] or published["holdout_used"]:
        raise ValueError("comparison includes final-holdout outcomes")
    a = published["strategy_a"]
    b = published["strategy_b"]
    br = sizing["variants"]
    lines = ["# Strategy A and B — clock and research-sizing comparison", "",
             "The original $5,000 outputs and configurations are preserved. The $100,000 balance is a "
             "research sampling convention at the same 0.5% risk, lot minimum/step and modeled costs; "
             "it is not a recommended trading account size. All outcomes below use 2018–2024 development data only.", "",
             "| Hypothesis | Actual opening range (New York) | Exit (New York) | Sizing | "
             "Trades / mean R / PF / return / max closed DD |",
             "| --- | --- | --- | --- | --- |",
             f"| A legacy | 06:20–07:20 EST; 05:20–06:20 EDT | 14:00 EST; 13:00 EDT | "
             f"$5k published | {_metrics(a)} |",
             f"| A legacy | same | same | $100k sensitivity | {_metrics(a_research)} |",
             f"| B1 true 08:20, 60m | 08:20–09:20 EST/EDT | 16:00 EST/EDT | "
             f"$5k published | {_metrics(b['base_1600_fixed'])} |",
             f"| B1 true 08:20, 60m | same | same | $100k research | "
             f"{_metrics(br['base_1600_fixed'])} |",
             f"| B2 one-time filter transfer | same | same | $5k published | "
             f"{_metrics(b['both_1600_fixed'])} |",
             f"| B2 one-time filter transfer | same | same | $100k research | "
             f"{_metrics(br['both_1600_fixed'])} |",
             f"| B selected 4×4 path | 08:20 NY; range length selected inside training | 16:00 NY | "
             f"$100k research | {_metrics(sweep)} |", "",
             "The $5,000 B1 fold below-minimum-lot skips were 12/9/9/31; the $100,000 B1 "
             "run had 0/0/0/0. B2 likewise moved from 9/7/6/19 to 0/0/0/0. "
             "The $5,000 feasibility share remains a descriptive statistic, never an entry filter. "
             "B2's timing and OR-width rules are rejected for Strategy B and absent from the 4×4 sweep.", "",
             "The unchanged daily ATR routine groups bars at its input's UTC-labelled day boundary. "
             "A uses the stored server-clock day; B uses real UTC after clock decoding, so their "
             "ATR inputs are not identical. A's actual opening starts at 09:20 UTC in summer "
             "and 11:20 UTC in winter. A live version needs an explicit real-clock anchor; "
             "choosing that anchor would be a new test, not a relabel of Strategy A.", "",
             "Neither A nor B is superior on the basis of development-selected results. "
             f"The prespecified Strategy B sweep support verdict is "
             f"**{'YES' if sweep['supported'] else 'NO'}**; see its training surface, "
             "selected path and stability report. No 2025+ outcome was accessed.", "",
             _sweep_caveats(sweep_dir, sweep), "",
             f"Research-sizing B report: `{sizing_dir / 'research_sizing_report.md'}`.  ",
             f"Strategy A sensitivity: `{(a_dir / 'report.md').relative_to(ROOT)}`.  ",
             f"Strategy B sweep: `{sweep_dir / 'report.md'}`.", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-sizing-dir", type=Path, required=True)
    parser.add_argument("--sweep-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/session_comparison.md"))
    args = parser.parse_args()
    print(write_comparison(args.research_sizing_dir, args.sweep_dir, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
