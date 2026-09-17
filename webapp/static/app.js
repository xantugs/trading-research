const fields = [
  "session_open", "session_exit", "range_minutes", "risk_percent", "reward_risk",
  "stop_method", "stop_width_multiple", "atr_stop_multiple", "breakout_buffer_atr",
  "initial_equity", "spread_multiplier", "slippage_price", "fallback_spread_price",
  "commission_per_lot_side", "commission_rate_pct_side"
];
const numeric = new Set(fields.filter(name => !["session_open", "session_exit", "stop_method"].includes(name)));
const $ = id => document.getElementById(id);
let accessKey = sessionStorage.getItem("orb_access_key") || "";
let history = [];
let selectedId = null;
let selectedRun = null;
let activeId = null;
let fetching = false;
let toastTimer;

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
    if (input && params[name] !== undefined) input.value = params[name];
  }
  updateStopHint();
}

function formParameters() {
  const params = {};
  for (const name of fields) {
    const input = document.querySelector(`[name="${name}"]`);
    params[name] = numeric.has(name) ? Number(input.value) : input.value;
  }
  return params;
}

function updateStopHint() {
  const method = document.querySelector('[name="stop_method"]').value;
  const hints = {
    opposite_range: "Stop at the far side of the observed range. The two multiples above are inactive.",
    range_multiple: "Stop distance uses the selected multiple of the observed range width.",
    atr: "Stop distance uses a multiple of the prior 14-day ATR."
  };
  $("stopHint").textContent = hints[method];
  for (const [name, enabled] of [["stop_width_multiple", method === "range_multiple"],
                                 ["atr_stop_multiple", method === "atr"]]) {
    document.querySelector(`[name="${name}"]`).closest("label").style.opacity = enabled ? "1" : ".5";
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
    row.firstChild.colSpan = 7;
    body.appendChild(row);
    return;
  }
  for (const run of history) {
    const row = document.createElement("tr");
    row.dataset.id = run.id;
    if (run.id === selectedId) row.classList.add("selected");
    const metrics = run.metrics || {};
    const p = run.parameters || {};
    metricCell(row, formatDate(run.created_at));
    metricCell(row, `${p.range_minutes || 30}m · ${p.reward_risk || 2}R · ${String(p.stop_method || "opposite_range").replaceAll("_", " ")}`);
    metricCell(row, metrics.trades === undefined || metrics.trades === null ? "—" : Number(metrics.trades).toLocaleString());
    metricCell(row, formatR(metrics.expectancy_r), Number(metrics.expectancy_r) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(metrics.net_return_pct), Number(metrics.net_return_pct) < 0 ? "bad" : "good");
    metricCell(row, formatPercent(metrics.max_drawdown_pct, 2, false));
    metricCell(row, run.state.toUpperCase());
    row.addEventListener("click", () => selectRun(run.id));
    body.appendChild(row);
  }
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

function renderSelected() {
  const run = selectedRun;
  $("runButton").disabled = Boolean(activeId);
  if (!run) return;
  const metrics = run.metrics || {};
  const state = run.state || "idle";
  $("runTitle").textContent = state === "complete" ? "Result ready" : state === "running" ? "Backtest in progress" : state === "queued" ? "Starting run" : state === "failed" ? "Run failed" : state === "interrupted" ? "Run interrupted" : "Ready to test";
  $("runSubtitle").textContent = `${formatDate(run.created_at)} · ${run.id}`;
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
  for (const [id, value] of [["netReturn", metrics.net_return_pct], ["meanR", metrics.expectancy_r]]) {
    $(id).classList.toggle("negative", Number(value) < 0);
    $(id).classList.toggle("positive", value != null && Number(value) > 0);
  }
  $("maxDD").classList.toggle("negative", metrics.max_drawdown_pct != null && Number(metrics.max_drawdown_pct) > 0);
  $("logText").textContent = (run.recent_log && run.recent_log.length ? run.recent_log.join("\n") : "No log lines for this run.");
  renderCurve(run.drawdown_curve);
}

async function selectRun(id) {
  selectedId = id;
  renderHistory();
  try {
    selectedRun = await api(`/api/runs/${encodeURIComponent(id)}`);
    renderSelected();
  } catch (error) { toast(error.message); }
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
    if (selectedId) await selectRun(selectedId);
    $("runButton").disabled = Boolean(activeId);
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
    $("datasetName").textContent = `${data.server} / ${data.symbol}`;
    $("developmentWindow").textContent = `${data.start.slice(0,4)}–${Number(data.development_end.slice(0,4))-1} development`;
    setForm(data.parameters);
    history = data.history;
    activeId = data.active_id;
    renderHistory();
    if (history.length) await selectRun(activeId || history[0].id);
    $("runButton").disabled = Boolean(activeId);
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
      parameters:formParameters(), tick_check:$("tickCheck").checked
    })});
    activeId = run.id;
    selectedId = run.id;
    selectedRun = run;
    history.unshift(run);
    renderHistory();
    renderSelected();
    toast("Backtest started. This page will update as it runs.");
  } catch (error) {
    $("runButton").disabled = false;
    toast(error.message);
  }
});

document.querySelector('[name="stop_method"]').addEventListener("change", updateStopHint);
setInterval(refresh, 2000);
if (accessKey) unlock();
