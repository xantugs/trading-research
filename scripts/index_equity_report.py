#!/usr/bin/env python3
"""Self-contained HTML page of balance curves for the index comparison runs.

Reads the newest orb-framework --configs comparison for each index (trades_<config>.csv
plus comparison.csv) and draws, per index, the video-EA curves (with the creator's US100
curve digitised from his video, when available) and the live-bot variants.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INDICES = (("ftmo_us100", "US100 (Nasdaq 100) · FTMO-Demo US100.cash"),
           ("ftmo_us500", "US500 (S&P 500) · FTMO-Demo US500.cash"))
GROUPS = (("Video EA rules", ("orb_framework_15_15", "orb_framework_15_5")),
          ("His live-bot presets", ("trading_steady_bot", "trading_steady_trailing",
                                    "trading_steady_stop_entry", "trading_steady_end_of_day")))
LABELS = {"orb_framework_15_15": "EA 15m range / 15m entry", "orb_framework_15_5": "EA 15m range / 5m entry",
          "trading_steady_bot": "Live bot (ATR filter, long)", "trading_steady_trailing": "+ ATR trailing stop",
          "trading_steady_stop_entry": "Stop-order entry, 2R", "trading_steady_end_of_day": "Flat at 16:00"}


def _curve(path: Path, start: str, equity: float, limit: int = 700) -> list[list]:
    trades = pd.read_csv(path, usecols=["exit_time", "equity_after"]).sort_values("exit_time")
    points = [[start, equity]] + [[t[:10], round(float(v), 2)] for t, v in
                                  zip(trades.exit_time.astype(str), trades.equity_after)]
    if len(points) > limit:  # keep the first, the last and an even sample between
        step = len(points) / limit
        points = [points[int(i * step)] for i in range(limit)] + [points[-1]]
    return points


def build(output: Path) -> Path:
    sections = []
    video = json.loads((ROOT / "outputs/indices/video_us100_15_5_curve.json").read_text(encoding="utf-8"))
    for slug, title in INDICES:
        runs = sorted((ROOT / "outputs/indices" / slug / "comparisons").glob("*/comparison.csv"))
        if not runs:
            continue
        folder = runs[-1].parent
        params = json.loads((folder / "parameters.json").read_text(encoding="utf-8"))
        table = pd.read_csv(runs[-1])
        any_config = next(iter(params.values()))
        charts = []
        for group, names in GROUPS:
            series = []
            for name in names:
                key = f"{slug}_{name}"
                if key in params:
                    series.append({"label": LABELS[name], "points": _curve(
                        folder / f"trades_{key}.csv", params[key]["start"], params[key]["initial_equity"])})
            if slug == "ftmo_us100" and group == "Video EA rules":
                series.append({"label": "His video: US100 15m / 5m (OANDA)", "points": video["points"],
                               "reference": True})
            charts.append({"title": group, "series": series})
        rows = []
        for row in table.itertuples(index=False):
            name = row.label.removeprefix(f"{slug}_")
            rows.append([LABELS.get(name, name), f"{row.trades:,}", f"{100 * row.win_rate:.1f}%",
                         f"{row.profit_factor:.2f}", f"${row.final_balance:,.0f}",
                         f"{row.net_return_pct:+.0f}%", f"{row.max_drawdown_pct:.1f}%"])
        sections.append({"title": title, "window": f"{any_config['start']} to {any_config['development_end']} (exclusive)",
                         "charts": charts, "rows": rows, "folder": str(folder.relative_to(ROOT))})
    page = TEMPLATE.replace("__DATA__", json.dumps({"sections": sections, "video_note": video["source"]}))
    output.write_text(page, encoding="utf-8")
    return output


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Index equity curves</title>
<style>
:root{--bg:#f6f8fb;--surface:#ffffff;--ink:#16202c;--muted:#5d6b7b;--line:#e3e8ef;--grid:#e9edf3;
  --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#0b121d;--surface:#121c2a;--ink:#edf4fa;
  --muted:#8fa3b7;--line:#253548;--grid:#1d2b3c;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0b121d;--surface:#121c2a;--ink:#edf4fa;--muted:#8fa3b7;--line:#253548;--grid:#1d2b3c;
  --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,"Segoe UI",Arial,sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 48px}h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
.lede{color:var(--muted);margin:0 0 26px;max-width:860px}section{margin-bottom:34px}
h2{font-size:18px;margin:0 0 2px}.window{color:var(--muted);font-size:12px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.card{background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:14px 14px 8px;min-width:0}.card h3{font-size:13px;margin:0 0 8px}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:12px;color:var(--muted);margin-bottom:6px}
.legend i{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}
svg{display:block;width:100%;overflow:visible}.axis{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}
.end{fill:var(--ink);font-size:11px;font-weight:600}table{width:100%;border-collapse:collapse;margin-top:14px;
  background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden;font-size:13px}
th,td{padding:8px 12px;text-align:right;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--muted);font-weight:600;font-size:12px}
tr:last-child td{border-bottom:0}.scroll{overflow-x:auto}.tip{position:fixed;pointer-events:none;background:var(--surface);
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 8px 22px #0003;display:none;z-index:5}
.tip b{display:block;margin-bottom:3px}.tip span{display:flex;align-items:center;gap:6px;color:var(--muted)}
.tip i{width:8px;height:8px;border-radius:2px;display:inline-block}.note{color:var(--muted);font-size:12px;margin-top:10px}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Index equity curves</h1>
<p class="lede">The same opening-range rules as the gold tests, on FTMO index CFDs: $10,000 start, 09:30 New York range,
New York + 7 h broker clock confirmed from the cash-open volume step. These runs read the former 2025+ holdout, opened
on 6 October 2026, so they are not out-of-sample. Hover a chart for values; each table repeats the final numbers.</p>
<div id="root"></div><p class="note" id="videoNote"></p></main><div class="tip" id="tip"></div>
<script>
const DATA = __DATA__;
const COLORS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.appendChild(n); return n; };
const money = v => "$" + Math.round(v).toLocaleString();
function ticks(lo, hi, n) { const raw = (hi - lo) / n, p = 10 ** Math.floor(Math.log10(raw)); const s = [1, 2, 2.5, 5, 10].map(m => m * p).find(v => v >= raw);
  const out = []; for (let v = Math.floor(lo / s) * s; v <= Math.ceil(hi / s) * s + 1e-9; v += s) out.push(v); return out; }
function chart(card, spec) {
  const width = Math.max(300, card.clientWidth - 28), height = 260, m = {t: 10, r: 112, b: 24, l: 58};
  const W = width - m.l - m.r, H = height - m.t - m.b;
  const series = spec.series.map((s, i) => ({...s, color: COLORS[i], pts: s.points.map(p => [Date.parse(p[0]), p[1]])}));
  const all = series.flatMap(s => s.pts);
  const t0 = Math.min(...all.map(p => p[0])), t1 = Math.max(...all.map(p => p[0]));
  const yt = ticks(Math.min(0, ...all.map(p => p[1])), Math.max(...all.map(p => p[1])), 4);
  const x = t => m.l + W * (t - t0) / (t1 - t0), y = v => m.t + H * (1 - (v - yt[0]) / (yt[yt.length - 1] - yt[0]));
  const svg = el("svg", {viewBox: `0 0 ${width} ${height}`, height, role: "img", "aria-label": spec.title});
  for (const v of yt) { el("line", {x1: m.l, x2: m.l + W, y1: y(v), y2: y(v), stroke: "var(--grid)"}, svg);
    el("text", {x: m.l - 8, y: y(v) + 4, "text-anchor": "end", class: "axis"}, svg).textContent = v >= 1000 ? "$" + (v / 1000) + "K" : "$" + v; }
  for (let yr = new Date(t0).getUTCFullYear() + 1; yr <= new Date(t1).getUTCFullYear(); yr++) { const at = x(Date.UTC(yr, 0, 1));
    el("text", {x: at, y: height - 6, "text-anchor": "middle", class: "axis"}, svg).textContent = yr; }
  el("line", {x1: m.l, x2: m.l + W, y1: y(10000), y2: y(10000), stroke: "var(--muted)", "stroke-opacity": .55}, svg);
  const ends = [];
  for (const s of series) {
    el("path", {d: s.pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" "), fill: "none",
      stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round"}, svg);
    const last = s.pts[s.pts.length - 1]; ends.push({y: y(last[1]), x: x(last[0]), text: money(last[1]), color: s.color});
  }
  ends.sort((a, b) => a.y - b.y); for (let i = 1; i < ends.length; i++) ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 14);
  for (const e of ends) { el("circle", {cx: e.x, cy: Math.min(e.y, m.t + H), r: 4, fill: e.color, stroke: "var(--surface)", "stroke-width": 2}, svg);
    el("text", {x: m.l + W + 10, y: e.y + 4, class: "end"}, svg).textContent = e.text; }
  const cross = el("line", {y1: m.t, y2: m.t + H, stroke: "var(--muted)", visibility: "hidden"}, svg);
  const hit = el("rect", {x: m.l, y: m.t, width: W, height: H, fill: "transparent"}, svg);
  const tip = document.getElementById("tip");
  hit.addEventListener("mousemove", ev => { const box = svg.getBoundingClientRect(); const t = t0 + (ev.clientX - box.left - m.l) / W * (t1 - t0);
    cross.setAttribute("x1", x(t)); cross.setAttribute("x2", x(t)); cross.setAttribute("visibility", "visible");
    const rows = series.map(s => { let v = null; for (const p of s.pts) { if (p[0] > t) break; v = p[1]; }
      return `<span><i style="background:${s.color}"></i>${s.label}: ${v == null ? "—" : money(v)}</span>`; }).join("");
    tip.innerHTML = `<b>${new Date(t).toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"})}</b>${rows}`;
    tip.style.display = "block"; tip.style.left = Math.min(ev.clientX + 14, innerWidth - tip.offsetWidth - 8) + "px";
    tip.style.top = Math.min(ev.clientY + 14, innerHeight - tip.offsetHeight - 8) + "px"; });
  hit.addEventListener("mouseleave", () => { tip.style.display = "none"; cross.setAttribute("visibility", "hidden"); });
  return svg;
}
function render() {
  const root = document.getElementById("root"); root.replaceChildren();
  for (const sec of DATA.sections) {
    const s = document.createElement("section");
    s.innerHTML = `<h2>${sec.title}</h2><div class="window">${sec.window} · ${sec.folder}</div><div class="grid"></div>
      <div class="scroll"><table><thead><tr><th>Configuration</th><th>Trades</th><th>Win</th><th>PF</th><th>$10k →</th><th>Return</th><th>Max DD</th></tr></thead><tbody></tbody></table></div>`;
    root.appendChild(s);
    const grid = s.querySelector(".grid");
    for (const spec of sec.charts) { const card = document.createElement("div"); card.className = "card";
      card.innerHTML = `<h3>${spec.title}</h3><div class="legend">${spec.series.map((x, i) => `<span><i style="background:${COLORS[i]}"></i>${x.label}</span>`).join("")}</div>`;
      grid.appendChild(card); card.appendChild(chart(card, spec)); }
    s.querySelector("tbody").innerHTML = sec.rows.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("");
  }
  document.getElementById("videoNote").textContent = "His curve: " + DATA.video_note;
}
render(); let timer; addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(render, 150); });
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "indices" / "equity_curves.html")
    args = parser.parse_args(argv)
    print(build(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
