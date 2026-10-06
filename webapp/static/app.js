const fields = [
  "session_open", "session_exit", "range_minutes", "signal_minutes", "trade_direction",
  "position_exit", "trades_per_session", "entry_mode", "or_candle", "or_atr_period",
  "or_min_atr", "or_max_atr", "flat_time", "trail_atr_multiple", "trail_activation_r",
  "trail_atr_period", "financing_pct_annual", "risk_percent", "reward_risk",
  "stop_method", "stop_width_multiple", "atr_stop_multiple", "breakout_buffer_atr",
  "initial_equity", "spread_multiplier", "slippage_price", "fallback_spread_price",
  "commission_per_lot_side", "commission_rate_pct_side"
];
const textFields = ["session_open", "session_exit", "stop_method", "trade_direction", "position_exit",
                    "trades_per_session", "entry_mode", "or_candle", "flat_time"];
// A blank box sends null, which switches that rule off.
const optional = new Set(["or_min_atr", "or_max_atr", "trail_atr_multiple", "flat_time"]);
const flatExits = new Set(["end_of_day", "friday_close"]);
const numeric = new Set(fields.filter(name => !textFields.includes(name)));
const $ = id => document.getElementById(id);
let accessKey = sessionStorage.getItem("orb_access_key") || "";
let history = [];
let selectedId = null;
let selectedRun = null;
let activeId = null;
let fetching = false;
let toastTimer;
let selectedTrades = [];
let tradesLoadedFor = null;
let tradeFilter = "all";
let compareIds = new Set();
let presets = [];
let presetId = null;
const reportCache = new Map();
let reportShownFor = null;
const TERMINAL = new Set(["complete", "failed", "interrupted"]);
const compareCache = new Map();
const MAX_COMPARE = 6;
const COMPARE_PALETTE = ["#61e6d2", "#e5bd71", "#f48691", "#7ee1ae", "#8ea9f7", "#e08ef2"];
const COMPARE_METRICS = [
  { label: "Trades", value: r => r.metrics.trades, format: v => v == null ? "—" : Number(v).toLocaleString() },
  { label: "Mean net R", value: r => r.metrics.expectancy_r, format: formatR, signed: true },
  { label: "Net return", value: r => r.metrics.net_return_pct, format: v => formatPercent(v), signed: true },
  { label: "Max closed DD", value: r => r.metrics.max_drawdown_pct, format: v => formatPercent(v, 2, false) },
  { label: "Win rate", value: r => r.metrics.win_rate, format: v => v == null ? "—" : formatPercent(v * 100, 1, false) },
  { label: "Profit factor", value: r => r.metrics.profit_factor, format: v => v == null ? "—" : Number(v).toFixed(3) },
  { label: "Monthly volatility", value: r => r.metrics.monthly_volatility_pct, format: v => v == null ? "—" : `${Number(v).toFixed(2)}%` },
  { label: "Sharpe (monthly, ann.)", value: r => r.metrics.sharpe_monthly_annualized, format: v => v == null ? "—" : Number(v).toFixed(2), signed: true },
];

function setSetupMode(mode) {
  const walk = mode === "walk_forward";
  $("parameterForm").hidden = walk;
  $("walkForwardSetup").hidden = !walk;
  $("singleTab").setAttribute("aria-selected", String(!walk));
  $("walkTab").setAttribute("aria-selected", String(walk));
  $("setupTitle").textContent = walk ? "Fixed walk-forward" : "Parameters";
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4500);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Authorization": `Bearer ${accessKey}`, "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store"
  });
  const data = await response.json();
  if (response.status === 401) {
    $("authBackdrop").classList.remove("hidden");
    $("workspace").hidden = true;
    throw new Error("Access key rejected");
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function setForm(params) {
  for (const name of fields) {
    const input = document.querySelector(`[name="${name}"]`);
    if (input && params[name] !== undefined) input.value = params[name] === null ? "" : params[name];
  }
  updateStopHint();
  updateRuleHints();
}

function currentPreset() {
  return presets.find(preset => preset.id === presetId) || presets[0];
}

function showPresetWindow(preset) {
  if (!preset) return;
  const w = preset.window;
  // The window's end is exclusive; label it by its last day ("2024", or "Sep 2026" mid-year).
  const last = new Date(Date.parse(w.development_end) - 86400000);
  const endLabel = last.getUTCMonth() === 11 && last.getUTCDate() === 31 ? String(last.getUTCFullYear())
    : last.toLocaleDateString(undefined, {month: "short", year: "numeric", timeZone: "UTC"});
  const years = `${w.start.slice(0, 4)}–${endLabel}`;
  const legacy = w.broker_clock === "stored_as_utc";
  $("presetSummary").textContent = preset.summary;
  $("presetWindow").textContent = `${w.symbol} · ${years} development · ${legacy ? "legacy clock" : "NY + 7 h clock"}`;
  $("datasetName").textContent = `${w.server} / ${w.symbol}`;
  $("developmentWindow").textContent = `${years} development`;
  $("clockPill").textContent = legacy ? "Legacy UTC clock" : "NY + 7 h clock";
  // Once the holdout is opened, results through the window's end are no longer out-of-sample.
  const opened = w.holdout_opened && w.development_end > w.holdout_start;
  const lastDay = last.toISOString().slice(0, 10);
  $("holdoutFooter").textContent = opened
    ? `Data to ${lastDay} · holdout opened ${w.holdout_opened} ◇`
    : `${w.holdout_start.slice(0, 4)}+ holdout sealed ◇`;
  $("presetWindow").textContent += opened ? " · includes former holdout" : "";
  $("clockPill").classList.toggle("warn", legacy);
}

function applyPreset(id, parameters = null) {
  presetId = presets.some(preset => preset.id === id) ? id : presets[0].id;
  $("presetSelect").value = presetId;
  const preset = currentPreset();
  setForm(parameters || preset.parameters);
  showPresetWindow(preset);
  markModified();
}

function sameValue(a, b) {
  if (a === null || a === undefined || b === null || b === undefined) return (a ?? null) === (b ?? null);
  return typeof a === "number" || typeof b === "number" ? Number(a) === Number(b) : String(a) === String(b);
}

function markModified() {
  const preset = currentPreset();
  if (!preset) return;
  const form = formParameters();
  const changed = fields.filter(name => !sameValue(form[name], preset.parameters[name]));
  $("presetModified").hidden = !changed.length;
  $("presetReset").hidden = !changed.length;
  $("presetModified").title = changed.length ? `Changed: ${changed.join(", ")}` : "";
  updateChips(form);
}

function updateChips(form) {
  const filters = [];
  if (form.entry_mode === "stop_order") filters.push("stop order");
  if (form.or_candle !== "any") filters.push(`${form.or_candle} range`);
  if (form.or_min_atr != null || form.or_max_atr != null) {
    filters.push(`ATR ${form.or_min_atr ?? "0"}–${form.or_max_atr ?? "∞"}×`);
  }
  $("filtersChip").textContent = filters.length ? filters.join(" · ") : "Close entry, no filter";
  $("filtersChip").classList.toggle("on", filters.length > 0);
  const trailing = form.trail_atr_multiple != null;
  $("trailChip").textContent = trailing ? `${form.trail_atr_multiple}× ATR from ${form.trail_activation_r}R` : "Off";
  $("trailChip").classList.toggle("on", trailing);
  const money = Number(form.initial_equity).toLocaleString(undefined, {maximumFractionDigits: 0});
  $("costsChip").textContent = `$${money}${form.financing_pct_annual ? ` · ${form.financing_pct_annual}% financing` : ""}`;
}

function formParameters() {
  const params = {};
  for (const name of fields) {
    const input = document.querySelector(`[name="${name}"]`);
    if (optional.has(name) && input.value.trim() === "") params[name] = null;
    else params[name] = numeric.has(name) ? Number(input.value) : input.value;
  }
  // flat_time only belongs to the end-of-day and Friday exits.
  if (!flatExits.has(params.position_exit)) params.flat_time = null;
  return params;
}

function updateRuleHints() {
  const value = name => document.querySelector(`[name="${name}"]`).value;
  const flat = flatExits.has(value("position_exit"));
  const flatInput = document.querySelector('[name="flat_time"]');
  flatInput.disabled = !flat;
  if (flat && !flatInput.value) flatInput.value = "16:00";
  const trailing = value("trail_atr_multiple").trim() !== "";
  for (const name of ["trail_activation_r", "trail_atr_period"]) {
    document.querySelector(`[name="${name}"]`).disabled = !trailing;
  }
  const filtering = value("or_min_atr").trim() !== "" || value("or_max_atr").trim() !== "";
  document.querySelector('[name="or_atr_period"]').disabled = !filtering;
  const reentry = document.querySelector('[name="trades_per_session"] option[value="after_close"]');
  reentry.disabled = value("entry_mode") === "stop_order";
  if (reentry.disabled && value("trades_per_session") === "after_close") {
    document.querySelector('[name="trades_per_session"]').value = "one";
  }
  // A trailing stop replaces the fixed target.
  document.querySelector('[name="reward_risk"]').disabled = trailing;
}

function updateStopHint() {
  const method = document.querySelector('[name="stop_method"]').value;
  const hints = {
    opposite_range: "Stop at the far side of the observed range; the two multiples are unused.",
    range_multiple: "Stop distance uses the selected multiple of the observed range width.",
    atr: "Stop distance uses a multiple of the prior 14-day ATR."
  };
  $("stopHint").textContent = hints[method];
  for (const [name, enabled] of [["stop_width_multiple", method === "range_multiple"],
                                 ["atr_stop_multiple", method === "atr"]]) {
    document.querySelector(`[name="${name}"]`).disabled = !enabled;
  }
}

function formatPercent(value, digits = 2, signed = true) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  return `${signed && n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

function formatR(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(3)} R`;
}

function formatMoney(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  return `${n > 0 ? "+" : n < 0 ? "-" : ""}$${Math.abs(n).toFixed(2)}`;
}

function formatDate(value) {
  if (!value) return "—";
  let date = new Date(value);
  if (Number.isNaN(date.valueOf()) && /^\d{8}T\d{6}Z$/.test(value)) {
    date = new Date(`${value.slice(0,4)}-${value.slice(4,6)}-${value.slice(6,8)}T${value.slice(9,11)}:${value.slice(11,13)}:${value.slice(13,15)}Z`);
  }
  return Number.isNaN(date.valueOf()) ? value.slice(0, 16) : date.toLocaleString(undefined, {month:"short",day:"numeric",year:"numeric",hour:"2-digit",minute:"2-digit"});
}

function metricCell(row, text, className = "") {
  const cell = document.createElement("td");
  cell.textContent = text;
  if (className) cell.className = className;
  row.appendChild(cell);
}

function renderHistory() {
  $("runCount").textContent = `${history.length} run${history.length === 1 ? "" : "s"}`;
  const body = $("historyBody");
  body.replaceChildren();
  if (!history.length) {
    const row = document.createElement("tr");
    metricCell(row, "No runs yet. Set your parameters and start the first one.", "empty");
    row.firstChild.colSpan = 8;
    body.appendChild(row);
    return;
  }
  for (const run of history) {
    const row = document.createElement("tr");
    row.dataset.id = run.id;
    if (run.id === selectedId) row.classList.add("selected");
    const metrics = run.metrics || {};
    const p = run.parameters || {};

    const checkCell = document.createElement("td");
    checkCell.className = "check-cell";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = compareIds.has(run.id);
    checkbox.disabled = run.state !== "complete";
    checkbox.setAttribute("aria-label", `Add run ${run.id} to comparison`);
    checkbox.addEventListener("click", event => event.stopPropagation());
    checkbox.addEventListener("change", () => toggleCompare(run.id, checkbox.checked));
    checkCell.appendChild(checkbox);
    row.appendChild(checkCell);

    metricCell(row, formatDate(run.created_at));
    const preset = presets.find(item => item.id === run.preset);
    metricCell(row, run.kind === "walk_forward" ? "Walk-forward · fixed 08:20 / 60m" :
      run.strategy === "tick_scalper" ? `Tick scalper · ${p.range_open} NY range · SL/TP ${p.sl_points} · trail ${p.trail_points} pt · ${p.execution_delay_ms} ms` :
      [`${p.range_minutes || 30}m/${p.signal_minutes || 5}m`,
       p.trail_atr_multiple ? `trail ${p.trail_atr_multiple}×ATR` : `${p.reward_risk || 2}R`,
       String(p.stop_method || "opposite_range").replaceAll("_", " "),
       p.entry_mode === "stop_order" ? "stop entry" : null,
       p.or_min_atr || p.or_max_atr ? "ATR filter" : null,
       p.position_exit && p.position_exit !== "session_exit" ? p.position_exit.replaceAll("_", " ") : null,
      ].filter(Boolean).join(" · "));
    row.lastChild.title = preset ? preset.label : run.kind === "walk_forward" ? "" : "Run before presets existed";
    metricCell(row, metrics.trades === undefined || metrics.trades === null ? "—" : Number(metrics.trades).toLocaleString());
    metricCell(row, formatR(metrics.expectancy_r), Number(metrics.expectancy_r) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(metrics.net_return_pct), Number(metrics.net_return_pct) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(metrics.max_drawdown_pct, 2, false));
    metricCell(row, run.state.toUpperCase());
    row.addEventListener("click", () => selectRun(run.id));
    body.appendChild(row);
  }
}

async function toggleCompare(id, checked) {
  if (checked) {
    if (compareIds.size >= MAX_COMPARE) {
      toast(`Compare up to ${MAX_COMPARE} runs at a time.`);
      renderHistory();
      return;
    }
    compareIds.add(id);
    if (!compareCache.has(id)) {
      try {
        compareCache.set(id, await api(`/api/runs/${encodeURIComponent(id)}`));
      } catch (error) {
        compareIds.delete(id);
        toast(error.message);
        renderHistory();
        return;
      }
    }
  } else {
    compareIds.delete(id);
  }
  renderCompare();
}

function renderCompareChart(ids) {
  const group = $("compareLines");
  group.replaceChildren();
  const curves = ids.map(id => {
    const values = compareCache.get(id).drawdown_curve;
    return values && values.length > 1 ? values : [0, 0];
  });
  const depth = Math.max(1, ...curves.flat().map(v => -Number(v)));
  curves.forEach((values, index) => {
    const points = values.map((v, i) => {
      const x = 800 * i / (values.length - 1);
      const y = 4 + 132 * (-Number(v)) / depth;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", `M${points.join(" L")}`);
    path.setAttribute("class", "compare-line");
    path.style.stroke = COMPARE_PALETTE[index % COMPARE_PALETTE.length];
    group.appendChild(path);
  });
  $("compareChartHint").textContent = `Worst drawdown ${depth.toFixed(2)}% across ${ids.length} runs`;
}

function runLabel(run) {
  return run.kind === "walk_forward" ? "Walk-forward" : formatDate(run.created_at);
}

function renderCompareLegend(ids) {
  const legend = $("compareLegend");
  legend.replaceChildren();
  ids.forEach((id, index) => {
    const run = compareCache.get(id);
    const chip = document.createElement("span");
    chip.className = "legend-chip";
    const dot = document.createElement("i");
    dot.style.background = COMPARE_PALETTE[index % COMPARE_PALETTE.length];
    chip.appendChild(dot);
    chip.append(`${runLabel(run)} · ${run.id}`);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "legend-remove";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Remove ${run.id} from comparison`);
    remove.addEventListener("click", () => { compareIds.delete(id); renderHistory(); renderCompare(); });
    chip.appendChild(remove);
    legend.appendChild(chip);
  });
}

function renderCompareTable(ids) {
  const headRow = document.createElement("tr");
  headRow.appendChild(document.createElement("th"));
  ids.forEach((id, index) => {
    const run = compareCache.get(id);
    const th = document.createElement("th");
    const dot = document.createElement("span");
    dot.className = "legend-dot";
    dot.style.background = COMPARE_PALETTE[index % COMPARE_PALETTE.length];
    th.appendChild(dot);
    th.append(runLabel(run));
    headRow.appendChild(th);
  });
  const head = $("compareHead");
  head.replaceChildren();
  head.appendChild(headRow);

  const body = $("compareBody");
  body.replaceChildren();
  for (const metric of COMPARE_METRICS) {
    const row = document.createElement("tr");
    const rowHead = document.createElement("th");
    rowHead.textContent = metric.label;
    row.appendChild(rowHead);
    for (const id of ids) {
      const run = compareCache.get(id);
      const raw = metric.value(run);
      const cell = document.createElement("td");
      cell.textContent = metric.format(raw);
      if (metric.signed && raw != null) cell.className = Number(raw) < 0 ? "bad" : "good";
      row.appendChild(cell);
    }
    body.appendChild(row);
  }
}

function renderCompare() {
  const ids = [...compareIds].filter(id => compareCache.has(id));
  $("comparePanel").hidden = ids.length < 2;
  if (ids.length < 2) return;
  renderCompareChart(ids);
  renderCompareLegend(ids);
  renderCompareTable(ids);
}

function renderCurve(values) {
  if (!values || values.length < 2) {
    $("drawdownLine").setAttribute("d", "");
    $("drawdownFill").setAttribute("d", "");
    $("chartHint").textContent = "Select a completed run";
    return;
  }
  const depth = Math.max(1, ...values.map(v => -Number(v)));
  const points = values.map((v, i) => {
    const x = 800 * i / (values.length - 1);
    const y = 4 + 132 * (-Number(v)) / depth;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  $("drawdownLine").setAttribute("d", `M${points.join(" L")}`);
  $("drawdownFill").setAttribute("d", `M0,4 L${points.join(" L")} L800,4 Z`);
  $("chartHint").textContent = `Worst closed drawdown ${depth.toFixed(2)}%`;
}

function renderFolds(run) {
  const visible = run && run.kind === "walk_forward" && run.folds && run.folds.length;
  $("foldResults").hidden = !visible;
  const body = $("foldBody");
  body.replaceChildren();
  if (!visible) return;
  for (const fold of run.folds) {
    const row = document.createElement("tr");
    metricCell(row, `${fold.train_start}–${fold.train_end_inclusive}`);
    metricCell(row, String(fold.test_year));
    metricCell(row, `${Number(fold.selected_buffer).toFixed(2)} × ATR`);
    metricCell(row, formatR(fold.selected_train_mean_r));
    metricCell(row, Number(fold.trades).toLocaleString());
    metricCell(row, formatR(fold.mean_r), Number(fold.mean_r) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(fold.return_pct), Number(fold.return_pct) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(fold.max_closed_dd_pct, 2, false));
    body.appendChild(row);
  }
}

function renderSelected() {
  const run = selectedRun;
  $("runButton").disabled = Boolean(activeId);
  $("runWalkForwardButton").disabled = Boolean(activeId);
  if (!run) return;
  const metrics = run.metrics || {};
  const state = run.state || "idle";
  const study = run.kind === "walk_forward";
  $("runTitle").textContent = state === "complete" ? (study ? "Walk-forward result" : "Result ready") : state === "running" ? (study ? "Walk-forward in progress" : "Backtest in progress") : state === "queued" ? "Starting run" : state === "failed" ? "Run failed" : state === "interrupted" ? "Run interrupted" : "Ready to test";
  const kindLabel = study ? "Fixed annual walk-forward" : run.strategy === "tick_scalper" ? "Tick scalper (native ticks)" : "Single backtest";
  $("runSubtitle").textContent = `${kindLabel} · ${formatDate(run.created_at)} · ${run.id}`;
  $("statePill").textContent = state.toUpperCase();
  $("statePill").className = `state-pill ${state}`;
  const progress = Math.min(100, Math.max(0, Number(run.progress) || 0));
  $("progressFill").style.width = `${progress}%`;
  $("progressTrack").setAttribute("aria-valuenow", String(progress));
  $("percentLabel").textContent = `${progress}%`;
  $("stageLabel").textContent = run.stage || "Waiting";
  $("netReturn").textContent = formatPercent(metrics.net_return_pct);
  $("meanR").textContent = formatR(metrics.expectancy_r);
  $("maxDD").textContent = formatPercent(metrics.max_drawdown_pct, 2, false);
  $("tradeCount").textContent = metrics.trades == null ? "—" : Number(metrics.trades).toLocaleString();
  $("monthlyVol").textContent = metrics.monthly_volatility_pct == null ? "—" : `${Number(metrics.monthly_volatility_pct).toFixed(2)}%`;
  $("sharpeRatio").textContent = metrics.sharpe_monthly_annualized == null ? "—" : Number(metrics.sharpe_monthly_annualized).toFixed(2);
  for (const [id, value] of [["netReturn", metrics.net_return_pct], ["meanR", metrics.expectancy_r],
                             ["sharpeRatio", metrics.sharpe_monthly_annualized]]) {
    $(id).classList.toggle("negative", Number(value) < 0);
    $(id).classList.toggle("positive", value != null && Number(value) > 0);
  }
  $("maxDD").classList.toggle("negative", metrics.max_drawdown_pct != null && Number(metrics.max_drawdown_pct) > 0);
  $("logText").textContent = (run.recent_log && run.recent_log.length ? run.recent_log.join("\n") : "No log lines for this run.");
  const reusable = !study && run.strategy !== "tick_scalper" && run.parameters && Object.keys(run.parameters).length;
  $("useSettings").hidden = !reusable;
  $("targetHead").textContent = run.parameters && run.parameters.trail_atr_multiple ? "Trail at" : "Target";
  renderCurve(run.drawdown_curve);
  renderFolds(run);
}

function renderTrades() {
  const body = $("tradeBody");
  body.replaceChildren();
  const filtered = selectedTrades.filter(trade => {
    if (tradeFilter === "win") return Number(trade.PnL) > 0;
    if (tradeFilter === "loss") return Number(trade.PnL) < 0;
    return true;
  });
  $("tradeListCount").textContent = `${filtered.length} trade${filtered.length === 1 ? "" : "s"}`;
  $("tradesDownload").disabled = !selectedTrades.length;
  if (!filtered.length) {
    const row = document.createElement("tr");
    metricCell(row, selectedTrades.length ? "No trades match this filter." : "No trades for this run.", "empty");
    row.firstChild.colSpan = 10;
    body.appendChild(row);
    return;
  }
  for (const trade of filtered) {
    const row = document.createElement("tr");
    metricCell(row, trade.session_date || "—");
    metricCell(row, trade.direction === "long" ? "Long" : "Short");
    metricCell(row, trade.entry != null ? trade.entry.toFixed(2) : "—");
    metricCell(row, trade.stop != null ? trade.stop.toFixed(2) : "—");
    metricCell(row, trade.take_profit != null ? trade.take_profit.toFixed(2) : "—");
    metricCell(row, trade.exit != null ? trade.exit.toFixed(2) : "—");
    metricCell(row, (trade.exit_reason || "—").replaceAll("_", " ") + (trade.ambiguous_m1 ? " *" : ""));
    metricCell(row, formatR(trade.R_result), Number(trade.R_result) < 0 ? "bad" : "good");
    metricCell(row, formatMoney(trade.PnL), Number(trade.PnL) < 0 ? "bad" : "good");
    metricCell(row, trade.equity_after != null ? `$${trade.equity_after.toFixed(2)}` : "—");
    body.appendChild(row);
  }
}

async function loadTrades(id, run) {
  if (!run || run.state !== "complete") {
    if (tradesLoadedFor !== null) {
      selectedTrades = [];
      tradesLoadedFor = null;
      renderTrades();
    }
    return;
  }
  if (tradesLoadedFor === id) return;
  const data = await api(`/api/runs/${encodeURIComponent(id)}/trades`);
  selectedTrades = data.trades || [];
  tradesLoadedFor = id;
  renderTrades();
}

async function selectRun(id) {
  selectedId = id;
  renderHistory();
  try {
    selectedRun = await api(`/api/runs/${encodeURIComponent(id)}`);
    renderSelected();
    await Promise.all([loadTrades(id, selectedRun), loadReport(id, selectedRun)]);
  } catch (error) { toast(error.message); }
}

async function loadReport(id, run) {
  if (!run || run.state !== "complete") {
    reportShownFor = null;
    showReport(null, run && TERMINAL.has(run.state) ? "No report: the run did not complete." : "The report appears when the run completes.");
    return;
  }
  if (reportShownFor === id) return;
  if (!reportCache.has(id)) {
    try {
      reportCache.set(id, await api(`/api/runs/${encodeURIComponent(id)}/report`));
    } catch (error) {
      showReport(null, error.message);
      return;
    }
  }
  if (selectedId !== id) return;
  reportShownFor = id;
  showReport(reportCache.get(id));
}

async function refresh() {
  if (fetching || !accessKey) return;
  fetching = true;
  try {
    const data = await api("/api/runs");
    history = data.history;
    activeId = data.active_id;
    $("connectionLabel").textContent = activeId ? "Worker running" : "Worker online";
    renderHistory();
    if (!selectedId && history.length) selectedId = activeId || history[0].id;
    const listed = history.find(run => run.id === selectedId);
    const stale = !selectedRun || selectedRun.id !== selectedId || !TERMINAL.has(selectedRun.state)
      || (listed && listed.state !== selectedRun.state);
    if (selectedId && stale) await selectRun(selectedId);
    $("runButton").disabled = Boolean(activeId);
    $("runWalkForwardButton").disabled = Boolean(activeId);
  } catch (error) {
    $("connectionLabel").textContent = "Worker unavailable";
  } finally { fetching = false; }
}

async function unlock() {
  try {
    const data = await api("/api/bootstrap");
    sessionStorage.setItem("orb_access_key", accessKey);
    $("authBackdrop").classList.add("hidden");
    $("workspace").hidden = false;
    $("authError").textContent = "";
    presets = data.presets || [];
    const select = $("presetSelect");
    select.replaceChildren(...presets.map(preset => new Option(preset.label, preset.id)));
    applyPreset(data.default_preset);
    history = data.history;
    activeId = data.active_id;
    renderHistory();
    if (history.length) await selectRun(activeId || history[0].id);
    setSetupMode(selectedRun && selectedRun.kind === "walk_forward" ? "walk_forward" : "backtest");
    $("runButton").disabled = Boolean(activeId);
    $("runWalkForwardButton").disabled = Boolean(activeId);
    $("connectionLabel").textContent = activeId ? "Worker running" : "Worker online";
  } catch (error) { $("authError").textContent = error.message; }
}

$("authForm").addEventListener("submit", async event => {
  event.preventDefault();
  accessKey = $("accessKey").value.trim();
  await unlock();
});

$("parameterForm").addEventListener("submit", async event => {
  event.preventDefault();
  if (activeId) return;
  $("runButton").disabled = true;
  try {
    const run = await api("/api/runs", {method:"POST", body:JSON.stringify({
      parameters:formParameters(), tick_check:$("tickCheck").checked, preset:presetId
    })});
    activeId = run.id;
    selectedId = run.id;
    selectedRun = run;
    selectedTrades = [];
    tradesLoadedFor = null;
    history.unshift(run);
    renderHistory();
    renderSelected();
    renderTrades();
    toast("Backtest started. This page will update as it runs.");
  } catch (error) {
    $("runButton").disabled = false;
    toast(error.message);
  }
});

$("runWalkForwardButton").addEventListener("click", async () => {
  if (activeId) return;
  $("runWalkForwardButton").disabled = true;
  try {
    const run = await api("/api/walk-forward", {method:"POST"});
    activeId = run.id;
    selectedId = run.id;
    selectedRun = run;
    selectedTrades = [];
    tradesLoadedFor = null;
    history.unshift(run);
    renderHistory();
    renderSelected();
    renderTrades();
    toast("Walk-forward started. The four folds will appear when complete.");
  } catch (error) {
    $("runWalkForwardButton").disabled = false;
    toast(error.message);
  }
});

$("singleTab").addEventListener("click", () => setSetupMode("backtest"));
$("walkTab").addEventListener("click", () => setSetupMode("walk_forward"));

$("clearCompare").addEventListener("click", () => {
  compareIds.clear();
  renderHistory();
  renderCompare();
});

document.querySelectorAll(".trade-tabs button").forEach(button => {
  button.addEventListener("click", () => {
    tradeFilter = button.dataset.filter;
    document.querySelectorAll(".trade-tabs button").forEach(other =>
      other.setAttribute("aria-selected", String(other === button)));
    renderTrades();
  });
});

$("tradesDownload").addEventListener("click", async () => {
  if (!selectedId || !selectedTrades.length) return;
  try {
    const response = await fetch(`/api/runs/${encodeURIComponent(selectedId)}/trades.csv`, {
      headers: { "Authorization": `Bearer ${accessKey}` }, cache: "no-store"
    });
    if (!response.ok) throw new Error(`Download failed (${response.status})`);
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedId}-trades.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) { toast(error.message); }
});

document.querySelector('[name="stop_method"]').addEventListener("change", updateStopHint);
for (const name of ["position_exit", "entry_mode", "trail_atr_multiple", "or_min_atr", "or_max_atr"]) {
  document.querySelector(`[name="${name}"]`).addEventListener("input", updateRuleHints);
}
$("parameterForm").addEventListener("input", markModified);
$("parameterForm").addEventListener("change", markModified);
$("presetSelect").addEventListener("change", () => applyPreset($("presetSelect").value));
$("presetReset").addEventListener("click", () => applyPreset(presetId));
$("useSettings").addEventListener("click", () => {
  if (!selectedRun || !selectedRun.parameters) return;
  const base = currentPreset();
  applyPreset(selectedRun.preset || presetId, {...base.parameters, ...selectedRun.parameters});
  setSetupMode("backtest");
  toast(selectedRun.preset ? "Loaded this run's settings into the form." :
    "Loaded this run's settings onto the current preset (the run predates presets).");
});
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (reportShownFor) showReport(reportCache.get(reportShownFor)); }, 150);
});
setInterval(refresh, 2000);
if (accessKey) unlock();

// --- Strategy tester report ------------------------------------------------------------
// Small SVG charts drawn at the container's pixel width so text stays crisp. Profit and
// loss use a validated blue/red polarity pair; single-series charts use one hue.
const SVG_NS = "http://www.w3.org/2000/svg";
const VIZ = { profit: "#4d93e6", loss: "#e2685f", series: "#61e6d2", grid: "#22344a", axis: "#8095a9" };

function svgNode(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (parent) parent.appendChild(node);
  return node;
}

function niceTicks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const step0 = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map(m => m * power).find(s => s >= step0) || 10 * power;
  // Ticks always bracket the data: the first at or below min, the last at or above max.
  const ticks = [];
  const end = Math.ceil(max / step - 1e-9) * step;
  for (let v = Math.floor(min / step + 1e-9) * step; v <= end + step * 1e-9; v += step) ticks.push(+v.toFixed(10));
  return ticks;
}

function moneyTick(value) {
  return `${Number(value) < 0 ? "−" : ""}$${compact(Math.abs(Number(value)))}`;
}

function compact(value) {
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e4) return `${(n / 1e3).toFixed(0)}K`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return Number.isInteger(n) ? String(n) : n.toFixed(abs < 10 ? 1 : 0);
}

function money(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const n = Number(value);
  return `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, {minimumFractionDigits: digits, maximumFractionDigits: digits})}`;
}

function tip(event, html) {
  const el = $("vizTip");
  if (!html) { el.hidden = true; return; }
  el.innerHTML = html;
  el.hidden = false;
  const pad = 14;
  const { innerWidth: w, innerHeight: h } = window;
  const rect = el.getBoundingClientRect();
  el.style.left = `${Math.min(event.clientX + pad, w - rect.width - 8)}px`;
  el.style.top = `${Math.min(event.clientY + pad, h - rect.height - 8)}px`;
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

function frame(container, height, margin = { top: 10, right: 10, bottom: 24, left: 44 }) {
  container.replaceChildren();
  const width = Math.max(220, container.clientWidth);
  const svg = svgNode("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img" }, container);
  svg.addEventListener("mouseleave", () => tip(null, null));
  return { svg, width, height, margin, plotW: width - margin.left - margin.right,
           plotH: height - margin.top - margin.bottom };
}

function yAxis(f, ticks, scale, format = compact) {
  for (const t of ticks) {
    const y = scale(t);
    svgNode("line", { x1: f.margin.left, x2: f.width - f.margin.right, y1: y, y2: y,
                      stroke: t === 0 ? "#3a5068" : VIZ.grid, "stroke-width": 1 }, f.svg);
    const label = svgNode("text", { x: f.margin.left - 6, y: y + 3.5, "text-anchor": "end", class: "viz-axis" }, f.svg);
    label.textContent = format(t);
  }
}

// A bar with a 4px rounded data-end and a square base, growing up or down from y0.
function barPath(x, w, y0, y1) {
  const r = Math.min(4, w / 2, Math.abs(y1 - y0));
  if (y1 <= y0) return `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 + r} V${y0} Z`;
  return `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + w - r} Q${x + w},${y1} ${x + w},${y1 - r} V${y0} Z`;
}

function barChart(container, rows, series, { height = 170, format = compact, tipFormat = v => v } = {}) {
  const f = frame(container, height);
  const max = Math.max(0, ...rows.flatMap(row => series.map(s => row[s.key])));
  const ticks = niceTicks(0, max || 1, 3);
  const top = ticks[ticks.length - 1];
  const y = v => f.margin.top + f.plotH * (1 - v / top);
  yAxis(f, ticks, y, format);
  const band = f.plotW / rows.length;
  const gap = 2;
  const barW = Math.min(24, Math.max(2, (band * 0.72 - gap * (series.length - 1)) / series.length));
  const group = barW * series.length + gap * (series.length - 1);
  // Thin the category labels until each has room (about 26px for "Sep" or "23").
  const labelEvery = Math.max(1, Math.ceil(26 / band));
  rows.forEach((row, i) => {
    const left = f.margin.left + band * i + (band - group) / 2;
    series.forEach((s, j) => {
      const value = row[s.key];
      if (value > 0) {
        svgNode("path", { d: barPath(left + j * (barW + gap), barW, y(0), y(value)), fill: s.color }, f.svg);
      }
    });
    const hit = svgNode("rect", { x: f.margin.left + band * i, y: f.margin.top, width: band, height: f.plotH,
                                   fill: "transparent" }, f.svg);
    hit.addEventListener("mousemove", event => tip(event,
      `<strong>${escapeHtml(row.label)}</strong>` + series.map(s =>
        `<span><i style="background:${s.color}"></i>${s.label}: ${escapeHtml(tipFormat(row[s.key], row))}</span>`).join("")));
    if (i % labelEvery === 0) {
      const label = svgNode("text", { x: f.margin.left + band * (i + 0.5), y: height - 7, "text-anchor": "middle",
                                       class: "viz-axis" }, f.svg);
      label.textContent = row.label;
    }
  });
}

function lineChart(container, points, { height = 230 } = {}) {
  const f = frame(container, height, { top: 12, right: 14, bottom: 24, left: 56 });
  const times = points.map(p => Date.parse(p[0]));
  const values = points.map(p => p[1]);
  const t0 = times[0], t1 = times[times.length - 1] || t0 + 1;
  const ticks = niceTicks(Math.min(...values), Math.max(...values), 4);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const x = t => f.margin.left + f.plotW * (t - t0) / Math.max(1, t1 - t0);
  const y = v => f.margin.top + f.plotH * (1 - (v - lo) / (hi - lo || 1));
  yAxis(f, ticks, y, moneyTick);
  const firstYear = new Date(t0).getUTCFullYear(), lastYear = new Date(t1).getUTCFullYear();
  for (let year = firstYear + 1; year <= lastYear; year++) {
    const at = x(Date.UTC(year, 0, 1));
    svgNode("line", { x1: at, x2: at, y1: f.margin.top, y2: f.margin.top + f.plotH, stroke: VIZ.grid }, f.svg);
    const label = svgNode("text", { x: at, y: height - 7, "text-anchor": "middle", class: "viz-axis" }, f.svg);
    label.textContent = year;
  }
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(times[i]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
  const floor = f.margin.top + f.plotH;
  svgNode("path", { d: `${path} L${x(t1).toFixed(1)},${floor} L${x(t0).toFixed(1)},${floor} Z`,
                    fill: VIZ.series, "fill-opacity": 0.1 }, f.svg);
  // The initial deposit as a reference line, labelled at its right end.
  const deposit = y(values[0]);
  svgNode("line", { x1: f.margin.left, x2: f.width - f.margin.right, y1: deposit, y2: deposit, stroke: "#4a6480" }, f.svg);
  const depositLabel = svgNode("text", { x: f.width - f.margin.right - 4, y: deposit - 5, "text-anchor": "end",
                                         class: "viz-axis" }, f.svg);
  depositLabel.textContent = "Initial deposit";
  svgNode("path", { d: path, fill: "none", stroke: VIZ.series, "stroke-width": 2, "stroke-linejoin": "round",
                    "stroke-linecap": "round" }, f.svg);
  const cross = svgNode("line", { y1: f.margin.top, y2: f.margin.top + f.plotH, stroke: "#6d8296", visibility: "hidden" }, f.svg);
  const dot = svgNode("circle", { r: 4, fill: VIZ.series, stroke: "#101c29", "stroke-width": 2, visibility: "hidden" }, f.svg);
  const hit = svgNode("rect", { x: f.margin.left, y: f.margin.top, width: f.plotW, height: f.plotH, fill: "transparent" }, f.svg);
  hit.addEventListener("mousemove", event => {
    const box = f.svg.getBoundingClientRect();
    const t = t0 + (event.clientX - box.left - f.margin.left) / f.plotW * (t1 - t0);
    let i = times.findIndex(v => v >= t);
    if (i < 0) i = times.length - 1;
    if (i > 0 && t - times[i - 1] < times[i] - t) i -= 1;
    const px = x(times[i]), py = y(values[i]);
    cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", px); dot.setAttribute("cy", py); dot.setAttribute("visibility", "visible");
    tip(event, `<strong>${new Date(times[i]).toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"})}</strong>
      <span>Balance ${money(values[i])}</span><span>${i ? `After trade ${i}` : "Initial deposit"}</span>`);
  });
  hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); });
}

function scatterChart(container, points, xKey, yKey, { xLabel, yLabel, xFormat = compact, height = 215 } = {}) {
  const f = frame(container, height, { top: 10, right: 12, bottom: 38, left: 50 });
  const xs = points.map(p => p[xKey]), ys = points.map(p => p[yKey]);
  const xt = niceTicks(Math.min(0, ...xs), Math.max(0, ...xs), 4);
  const yt = niceTicks(Math.min(0, ...ys), Math.max(0, ...ys), 4);
  const x = v => f.margin.left + f.plotW * (v - xt[0]) / (xt[xt.length - 1] - xt[0] || 1);
  const y = v => f.margin.top + f.plotH * (1 - (v - yt[0]) / (yt[yt.length - 1] - yt[0] || 1));
  yAxis(f, yt, y, moneyTick);
  for (const t of xt) {
    svgNode("line", { x1: x(t), x2: x(t), y1: f.margin.top, y2: f.margin.top + f.plotH,
                      stroke: t === 0 ? "#3a5068" : VIZ.grid }, f.svg);
    // Far enough below the plot that the corner x label clears the lowest y label.
    const label = svgNode("text", { x: x(t), y: f.margin.top + f.plotH + 17, "text-anchor": "middle", class: "viz-axis" }, f.svg);
    label.textContent = xFormat(t);
  }
  const caption = svgNode("text", { x: f.margin.left + f.plotW / 2, y: height - 3, "text-anchor": "middle", class: "viz-axis" }, f.svg);
  caption.textContent = xLabel;
  const dots = svgNode("g", {}, f.svg);
  points.forEach(p => {
    svgNode("circle", { cx: x(p[xKey]).toFixed(1), cy: y(p[yKey]).toFixed(1), r: 2.6,
                        fill: p.profit > 0 ? VIZ.profit : VIZ.loss, "fill-opacity": 0.75 }, dots);
  });
  // Nearest-point hover: one hit layer, so 1,000+ dots stay responsive.
  const hit = svgNode("rect", { x: f.margin.left, y: f.margin.top, width: f.plotW, height: f.plotH, fill: "transparent" }, f.svg);
  const ring = svgNode("circle", { r: 5, fill: "none", stroke: "#edf4fa", "stroke-width": 1.5, visibility: "hidden" }, f.svg);
  hit.addEventListener("mousemove", event => {
    const box = f.svg.getBoundingClientRect();
    const mx = event.clientX - box.left, my = event.clientY - box.top;
    let best = null, bestD = 18 * 18;
    for (const p of points) {
      const dx = x(p[xKey]) - mx, dy = y(p[yKey]) - my, d = dx * dx + dy * dy;
      if (d < bestD) { best = p; bestD = d; }
    }
    if (!best) { ring.setAttribute("visibility", "hidden"); tip(event, null); return; }
    ring.setAttribute("cx", x(best[xKey])); ring.setAttribute("cy", y(best[yKey])); ring.setAttribute("visibility", "visible");
    tip(event, `<strong>${best.profit > 0 ? "Winning" : "Losing"} trade</strong><span>${yLabel} ${money(best[yKey])}</span><span>${xLabel} ${xKey === "hold_hours" ? `${best[xKey].toFixed(1)} h` : money(best[xKey])}</span>`);
  });
  hit.addEventListener("mouseleave", () => ring.setAttribute("visibility", "hidden"));
}

function statRows(stats) {
  const s = stats;
  const pct = v => (v === null || v === undefined ? "—" : `${Number(v).toFixed(2)}%`);
  const num = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
  const streak = v => (v ? `${v.count} (${money(v.money)})` : "—");
  const streakMoney = v => (v ? `${money(v.money)} (${v.count})` : "—");
  const bd = s.balance_drawdown, ed = s.equity_drawdown_estimate;
  return [
    ["Results", [
      ["Initial deposit", money(s.initial_deposit)], ["Final balance", money(s.final_balance)],
      ["Total net profit", money(s.total_net_profit), s.total_net_profit],
      ["Gross profit", money(s.gross_profit)], ["Gross loss", money(s.gross_loss)],
      ["Profit factor", num(s.profit_factor)], ["Expected payoff", money(s.expected_payoff)],
      ["Recovery factor", num(s.recovery_factor)], ["Sharpe ratio (per trade)", num(s.sharpe_ratio)],
      ["AHPR", s.ahpr == null ? "—" : `${num(s.ahpr, 4)} (${pct((s.ahpr - 1) * 100)})`],
      ["GHPR", s.ghpr == null ? "—" : `${num(s.ghpr, 4)} (${pct((s.ghpr - 1) * 100)})`],
    ]],
    ["Drawdown & fit", [
      ["Balance DD absolute", money(bd.absolute)],
      ["Balance DD maximal", `${money(bd.maximal)} (${pct(bd.maximal_pct)})`],
      ["Balance DD relative", `${pct(bd.relative_pct)} (${money(bd.relative)})`],
      ["Equity DD maximal (est.)", `${money(ed.maximal)} (${pct(ed.maximal_pct)})`],
      ["LR correlation", num(s.lr_correlation)], ["LR standard error", money(s.lr_standard_error)],
      ["Z-score", s.z_score == null ? "—" : `${num(s.z_score)} (${pct(s.z_confidence_pct)})`],
      ["Holding time min / avg", `${s.holding_time.min} / ${s.holding_time.average}`],
      ["Holding time max", s.holding_time.max],
      ["Commission paid", money(s.commission_paid)], ["Financing paid", money(s.financing_paid)],
    ]],
    ["Trades", [
      ["Total trades / deals", `${s.total_trades.toLocaleString()} / ${s.total_deals.toLocaleString()}`],
      ["Long trades (won %)", s.long_trades ? `${s.long_trades} (${pct(s.long_won_pct)})` : "0"],
      ["Short trades (won %)", s.short_trades ? `${s.short_trades} (${pct(s.short_won_pct)})` : "0"],
      ["Profit trades (% of total)", `${s.profit_trades} (${pct(s.profit_trades_pct)})`],
      ["Loss trades (% of total)", `${s.loss_trades} (${pct(s.loss_trades_pct)})`],
      ["Largest profit / loss", `${money(s.largest_profit_trade)} / ${money(s.largest_loss_trade)}`],
      ["Average profit / loss", `${money(s.average_profit_trade)} / ${money(s.average_loss_trade)}`],
      ["Max consecutive wins ($)", streak(s.max_consecutive_wins)],
      ["Max consecutive losses ($)", streak(s.max_consecutive_losses)],
      ["Maximal consecutive profit (count)", streakMoney(s.maximal_consecutive_profit)],
      ["Maximal consecutive loss (count)", streakMoney(s.maximal_consecutive_loss)],
      ["Average consecutive wins / losses", `${num(s.average_consecutive_wins)} / ${num(s.average_consecutive_losses)}`],
    ]],
  ];
}

function renderReportStats(stats) {
  const root = $("reportStats");
  root.replaceChildren();
  for (const [title, rows] of statRows(stats)) {
    const group = document.createElement("div");
    group.className = `stat-group${title === "Trades" ? " wide" : ""}`;
    const heading = document.createElement("h3");
    heading.textContent = title;
    group.appendChild(heading);
    const list = document.createElement("dl");
    for (const [label, value, sign] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      if (sign !== undefined && sign !== null) dd.className = Number(sign) < 0 ? "bad" : "good";
      list.append(dt, dd);
    }
    group.appendChild(list);
    root.appendChild(group);
  }
}

function corrLabel(value) {
  return value === null || value === undefined ? "" : `correlation ${Number(value).toFixed(2)}`;
}

function showReport(report, message = "") {
  const ready = report && report.trades > 0;
  $("reportBody").hidden = !ready;
  $("reportEmpty").hidden = Boolean(ready);
  if (!ready) {
    $("reportEmpty").textContent = report && report.trades === 0 ? "This run closed no trades." : (message || "The report appears when a completed run is selected.");
    $("reportHint").textContent = "Select a completed run";
    return;
  }
  const s = report.stats;
  $("reportHint").textContent = `${s.total_trades.toLocaleString()} trades · entry times in ${report.timezone.replace("_", " ")}`;
  renderReportStats(s);
  $("balanceHint").textContent = `${money(s.initial_deposit, 0)} → ${money(s.final_balance, 0)}`;
  lineChart($("balanceChart"), report.balance_curve);
  const count = [{ key: "entries", label: "Entries", color: VIZ.series }];
  const pnl = [{ key: "profit", label: "Profit", color: VIZ.profit }, { key: "loss", label: "Loss", color: VIZ.loss }];
  const entriesTip = v => `${v} trade${v === 1 ? "" : "s"}`;
  const hours = trimHours(report.by_hour);
  barChart($("entriesHour"), hours, count, { tipFormat: entriesTip });
  barChart($("entriesWeekday"), report.by_weekday, count, { tipFormat: entriesTip });
  barChart($("entriesMonth"), report.by_month, count, { tipFormat: entriesTip });
  const moneyTip = v => money(v);
  barChart($("pnlHour"), hours, pnl, { format: moneyTick, tipFormat: moneyTip });
  barChart($("pnlWeekday"), report.by_weekday, pnl, { format: moneyTick, tipFormat: moneyTip });
  barChart($("pnlMonth"), report.by_month, pnl, { format: moneyTick, tipFormat: moneyTip });
  const c = report.correlations;
  scatterChart($("scatterMfe"), report.scatter, "mfe", "profit", { xLabel: "MFE", yLabel: "Profit", xFormat: moneyTick });
  scatterChart($("scatterMae"), report.scatter, "mae", "profit", { xLabel: "MAE", yLabel: "Profit", xFormat: moneyTick });
  scatterChart($("scatterMfeMae"), report.scatter, "mae", "mfe", { xLabel: "MAE", yLabel: "MFE", xFormat: moneyTick });
  scatterChart($("scatterHold"), report.scatter, "hold_hours", "profit", { xLabel: "Hours held", yLabel: "Profit", xFormat: v => `${compact(v)}h` });
  $("corrProfitMfe").textContent = corrLabel(c.profit_mfe);
  $("corrProfitMae").textContent = corrLabel(c.profit_mae);
  $("corrMfeMae").textContent = corrLabel(c.mfe_mae);
  $("corrHold").textContent = corrLabel(c.profit_hold);
}

// Session strategies trade a few hours; show the active span (with an hour either side)
// rather than 24 mostly empty slots.
function trimHours(rows) {
  const active = rows.map((row, i) => (row.entries || row.profit || row.loss ? i : -1)).filter(i => i >= 0);
  if (!active.length) return rows;
  const lo = Math.max(0, active[0] - 1), hi = Math.min(23, active[active.length - 1] + 1);
  return rows.slice(lo, hi + 1);
}
