"use strict";
// Mergemon Designer: tabs -> state -> /api/preview (design screen), /api/check
// (what blocks Optimize), /api/optimize + /api/job (the run), /api/results,
// /api/sweep + /api/sweeps + /api/sweep_results (the Sweep tab).

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const PHI0_RED2 = Math.pow(2.067833848e-15 / (2 * Math.PI), 2), H = 6.62607015e-34;
// Figures-of-merit inputs: element id -> state.fom key
const FOM_INPUTS = [["lj", "lj_total_nH"], ["cj", "cj_total_fF"], ["fr", "fr_GHz"], ["fr-tol", "fr_tol_GHz"],
                    ["chi", "chi_MHz"], ["chi-tol", "chi_tol_MHz"], ["kappa", "kappa_MHz"]];

const DEFAULT_STATE = {
  substrate: { soi: null, thickness_um: 750 },
  design: { qubit: null, resonator: null, res_cap: "three_finger", res_ind: "meander_fixed", coupler: null,
            feedline: "none", flux_line: "none" },
  fom: { lj_total_nH: "", cj_total_fF: "", fr_GHz: "", fr_tol_GHz: 0.1, chi_MHz: "", chi_tol_MHz: "", kappa_MHz: "" },
};
// The design simulated so far (fr_4_72GHz_chi_2MHz): "Load example", or ?preset=example.
const EXAMPLE_STATE = {
  substrate: { soi: "bulk", thickness_um: 750 },
  design: { qubit: "squid", resonator: "lumped", res_cap: "three_finger", res_ind: "meander_fixed", coupler: "t",
            feedline: "none", flux_line: "none" },
  fom: { lj_total_nH: 18, cj_total_fF: 90, fr_GHz: 4.72, fr_tol_GHz: 0.1, chi_MHz: 2, chi_tol_MHz: 0.2 },
};
let OPTIONS = null;
let state = load();
let viewKnobs = null, viewName = null;        // geometry shown: nominal, or a result's knobs
let activeTab = new URLSearchParams(location.search).get("tab") || "substrate";
let pollTimer = null;

function load() {
  const s = loadRaw();
  s.design = Object.assign({ feedline: "none", flux_line: "none" }, s.design);
  return s;
}
function loadRaw() {
  const preset = new URLSearchParams(location.search).get("preset");
  if (preset === "example") return structuredClone(EXAMPLE_STATE);
  if (preset === "new") return structuredClone(DEFAULT_STATE);
  try {                                     // ?state=<json>: a design as a link
    const s = JSON.parse(new URLSearchParams(location.search).get("state"));
    if (s && s.substrate && s.design && s.fom) return s;
  } catch (e) { /* not given / not JSON */ }
  try {
    const s = JSON.parse(localStorage.getItem("mergemon-designer-state"));
    if (s && s.substrate && s.design && s.fom) return s;
  } catch (e) { /* storage unavailable: start fresh */ }
  return structuredClone(DEFAULT_STATE);
}
function save() {
  try { localStorage.setItem("mergemon-designer-state", JSON.stringify(state)); } catch (e) { /* ignore */ }
}

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return r.json();
}

// ------------------------------------------------------------------ cards ---
function cards(containerId, options, current, onPick) {
  const box = $(containerId);
  box.replaceChildren();
  for (const o of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `card ${o.status}` + (o.id === current ? " selected" : "");
    const t = document.createElement("span"); t.className = "title"; t.textContent = o.label;
    const badge = document.createElement("span"); badge.className = `badge ${o.status}`; badge.textContent = o.status;
    const n = document.createElement("span"); n.className = "note"; n.textContent = o.note || "";
    b.append(t, badge, n);
    b.addEventListener("click", () => onPick(o.id));
    box.append(b);
  }
}

function selectOptions(selId, options, current, onPick) {
  const sel = $(selId);
  sel.replaceChildren();
  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o.id;
    opt.textContent = `${o.label}${o.status === "simulated" ? "" : ` (${o.status})`}`;
    opt.selected = o.id === current;
    sel.append(opt);
  }
  sel.onchange = () => onPick(sel.value);
}

function render() {
  const s = state;
  cards("opt-soi", OPTIONS.substrate.soi, s.substrate.soi, (id) => { s.substrate.soi = id; changed(); });
  cards("opt-qubit", OPTIONS.qubit, s.design.qubit, (id) => { s.design.qubit = id; changed(); });
  cards("opt-resonator", OPTIONS.resonator, s.design.resonator, (id) => { s.design.resonator = id; changed(); });
  cards("opt-coupler", OPTIONS.coupler, s.design.coupler, (id) => { s.design.coupler = id; changed(); });
  cards("opt-feedline", OPTIONS.feedline, s.design.feedline, (id) => { s.design.feedline = id; changed(); });
  cards("opt-flux", OPTIONS.flux_line, s.design.flux_line, (id) => { s.design.flux_line = id; changed(); });
  selectOptions("res_cap", OPTIONS.res_cap, s.design.res_cap, (id) => { s.design.res_cap = id; changed(); });
  selectOptions("res_ind", OPTIONS.res_ind, s.design.res_ind, (id) => { s.design.res_ind = id; changed(); });
  $("sub-single").classList.toggle("hidden", s.design.qubit !== "single");
  $("sub-lumped").classList.toggle("hidden", s.design.resonator !== "lumped");
  $("soi-stack").classList.toggle("hidden", s.substrate.soi !== "soi");

  const ind = OPTIONS.res_ind.find((o) => o.id === s.design.res_ind);
  const cap = OPTIONS.res_cap.find((o) => o.id === s.design.res_cap);
  const lh = $("lumped-hint");
  lh.textContent = [cap, ind].filter((o) => o && o.status !== "simulated").map((o) => `${o.label}: ${o.note}`).join(" · ");
  lh.className = "hint" + (lh.textContent ? " warn" : "");

  $("thickness").value = s.substrate.thickness_um;
  const th = $("thickness-hint");
  const simT = OPTIONS.substrate.thickness_um.simulated;
  const okT = simT.some((v) => Math.abs(v - Number(s.substrate.thickness_um)) < 1e-6);
  th.textContent = okT ? "simulated (HFSS substrate = 750 µm Si)" : `planned: only ${simT.join(", ")} µm is simulated so far`;
  th.className = "hint" + (okT ? "" : " warn");

  const f = s.fom;
  for (const [id, k] of FOM_INPUTS) {
    if (document.activeElement !== $(id)) $(id).value = f[k] ?? "";
  }
  const fullLine = s.design.feedline === "met181_full";
  $("kappa").disabled = !fullLine && !f.kappa_MHz;     // keep a stale value visible (and flagged)
  if (!fullLine) { $("kappa-hint").textContent = "needs the whole feedline (Design tab: Readout feedline -> Whole feedline)"; $("kappa-hint").className = "hint"; $("kappa-note").hidden = true; }
  const lj = Number(f.lj_total_nH), cj = Number(f.cj_total_fF);
  $("lj-hint").textContent = lj > 0
    ? `per junction ${(2 * lj).toFixed(3).replace(/\.?0+$/, "")} nH (2 in parallel) · E_J = ${(PHI0_RED2 / (lj * 1e-9) / H / 1e9).toFixed(2)} GHz`
    : "SQUID: two junctions in parallel";
  $("cj-hint").textContent = cj > 0 ? `per junction ${(cj / 2).toFixed(3).replace(/\.?0+$/, "")} fF` : "";
  drawStack();
}

let previewTimer = null, checkTimer = null;
function changed() {
  save();
  render();
  clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 60);
  clearTimeout(checkTimer); checkTimer = setTimeout(refreshCheck, 120);
}

function bindInputs() {
  $("thickness").addEventListener("input", (e) => { state.substrate.thickness_um = e.target.value; changed(); });
  for (const [id, k] of FOM_INPUTS) {
    $(id).addEventListener("input", (e) => { state.fom[k] = e.target.value; changed(); });
  }
}

// kappa target -> the finger length the server derived (or why it can't)
function showKappa(k) {
  const hint = $("kappa-hint"), note = $("kappa-note");
  if (state.design.feedline !== "met181_full") return;
  if (!k) { hint.textContent = "empty: no κ target (the finger stays at its nominal length)"; hint.className = "hint"; note.hidden = true; return; }
  if (k.error) { hint.textContent = k.error; hint.className = "hint warn"; note.hidden = true; return; }
  hint.textContent = `→ feedline finger length ${k.finger_um} µm (fixed for the optimization)`;
  hint.className = "hint";
  note.textContent = k.note; note.hidden = false;
}

// --------------------------------------------------------------- checklist ---
async function refreshCheck() {
  const r = await api("/api/check", state);
  showKappa(r.kappa);
  const ul = $("checklist");
  ul.replaceChildren();
  const busy = jobRunning;
  if (!r.issues.length) {
    const li = document.createElement("li"); li.className = "good";
    li.textContent = "everything picked is simulated: ready to optimize";
    ul.append(li);
  }
  for (const m of r.issues) {
    const li = document.createElement("li"); li.className = "bad"; li.textContent = m; ul.append(li);
  }
  $("btn-opt").disabled = !!r.issues.length || busy;
  $("btn-dry").disabled = !!r.issues.length || busy;
}

// ---------------------------------------------------------- design screen ---
function svgEl(tag, attrs, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (parent) parent.append(e);
  return e;
}

const LAYERS = [
  ["resonator", "--res", "readout resonator (C_r + L_r)"],
  ["squid", "--squid", "SQUID"],
  ["coupler", "--coupler", "coupling capacitor"],
  ["junctions", "--jj", "junctions (lumped L_J, C_J)"],
  ["feedline", "--feed", "feedline (own conductor)"],
  ["flux_line", "--flux", "flux line (shorted to ground)"],
];

function drawPlan(svg, data, box, small) {
  svg.replaceChildren();
  const [x0, y0, x1, y1] = box;
  const pad = (x1 - x0) * 0.02;
  svg.setAttribute("viewBox", `${x0 - pad} ${-y1 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`);
  const css = getComputedStyle(document.documentElement);
  const g = svgEl("g", { transform: "scale(1,-1)" }, svg);
  svgEl("path", { d: data.ground, fill: css.getPropertyValue("--ground"), "fill-rule": "evenodd" }, g);
  for (const [key, color] of LAYERS) {
    if (data.shapes[key]) svgEl("path", { d: data.shapes[key], fill: css.getPropertyValue(color), "fill-rule": "evenodd" }, g);
  }
  const w = (x1 - x0);
  for (const ph of data.placeholders) {
    const [a, b, c, d] = ph.box;
    svgEl("rect", { x: a, y: b, width: c - a, height: d - b, fill: "none", stroke: css.getPropertyValue("--muted"),
      "stroke-width": w / 300, "stroke-dasharray": `${w / 80} ${w / 120}` }, g);
    if (c - a < w * 0.12) continue;          // too small to label at this zoom
    const fs = small ? w / 24 : w / 40;
    // svg y is down: -d is the box top, -b its bottom
    const y = { top: -d + 1.4 * fs, below: -b + 1.3 * fs, center: -(b + d) / 2 }[ph.anchor || "center"];
    const t = svgEl("text", { x: (a + c) / 2, y, "text-anchor": "middle", "dominant-baseline": "middle",
      fill: css.getPropertyValue("--ink-2"), "font-size": fs }, svg);
    t.textContent = ph.label;
  }
  if (!small) {                               // the 50 ohm ends of the lines
    for (const tm of data.terms || []) {
      svgEl("circle", { cx: tm.x, cy: -tm.y, r: w / 90, fill: "none", stroke: css.getPropertyValue("--ink"),
        "stroke-width": w / 400 }, svg);
      const t = svgEl("text", { x: tm.x, y: -tm.y - w / 55, "text-anchor": "middle",
        fill: css.getPropertyValue("--ink-2"), "font-size": w / 55 }, svg);
      t.textContent = "50 Ω";
    }
  }
}

async function refreshPreview() {
  const data = await api("/api/preview", { state, knobs: viewKnobs });
  if (data.error) { $("screen-note").textContent = data.error; return; }
  drawPlan($("svg-full"), data, data.bounds, false);
  drawPlan($("svg-zoom"), data, data.zoom, true);
  const legend = $("legend");
  legend.replaceChildren();
  const css = getComputedStyle(document.documentElement);
  for (const [key, color, label] of [["ground", "--ground", "ground plane / pocket"], ...LAYERS]) {
    if (key !== "ground" && !data.shapes[key]) continue;
    const s = document.createElement("span");
    const i = document.createElement("i"); i.style.background = css.getPropertyValue(color);
    s.append(i, label); legend.append(s);
  }
  const k = data.info.knobs;
  $("cap-full").textContent = `device · simulated region ${data.info.chip_um[0]} × ${data.info.chip_um[1]} µm`
    + (data.info.meander ? ` · meander ${data.info.meander}` : "");
  $("screen-note").textContent = (viewName ? `geometry of result ${viewName}: ` : "nominal start geometry: ")
    + `ind_length ${k.ind_length} · cap_length ${k.cap_length} · t_length ${k.t_length}`
    + (viewName ? "  (click Design to keep picking; Reset in Results returns to nominal)" : "  · the optimizer moves these three");
}

function drawStack() {
  const svg = $("svg-stack");
  svg.replaceChildren();
  const css = getComputedStyle(document.documentElement);
  const ink2 = css.getPropertyValue("--ink-2"), metal = css.getPropertyValue("--res");
  const soi = state.substrate.soi === "soi";
  const t = Number(state.substrate.thickness_um) || 0;
  const label = (x, y, s) => { const e = svgEl("text", { x, y, fill: ink2, "font-size": 11 }, svg); e.textContent = s; };
  svgEl("rect", { x: 20, y: 20, width: 190, height: 6, fill: metal, rx: 1 }, svg);
  label(220, 24, "Al metal (layer 1)");
  if (soi) {                                 // layers spaced for their labels, not to scale
    svgEl("rect", { x: 20, y: 26, width: 190, height: 16, fill: "#b9c3cf" }, svg);
    label(220, 40, `Si device ${$("soi-device").value} µm`);
    svgEl("rect", { x: 20, y: 42, width: 190, height: 16, fill: "#e7d9b8" }, svg);
    label(220, 54, `BOX ${$("soi-box").value} µm`);
    svgEl("rect", { x: 20, y: 58, width: 190, height: 72, fill: "#9aa7b6" }, svg);
    label(220, 98, `Si handle ${t} µm`);
    $("cap-stack").textContent = "SOI cross-section (planned) — not to scale";
  } else {
    svgEl("rect", { x: 20, y: 26, width: 190, height: 104, fill: "#9aa7b6" }, svg);
    label(220, 82, `Si ${t} µm`);
    label(220, 96, state.substrate.soi ? "bulk, ε_r 11.9" : "pick a wafer");
    $("cap-stack").textContent = "substrate cross-section — not to scale";
  }
}

// ------------------------------------------------------------------- jobs ---
let jobRunning = false;
async function startJob(dry) {
  if (!dry && !confirm("Run the optimizer in HFSS?\n\n~40 min per iteration (typically 1–3), then the delivered design is re-solved in its own AEDT project (~35 min). AEDT is busy until it finishes.")) return;
  const r = await api("/api/optimize", { state, dry_run: dry });
  if (!r.ok) { alert(r.error); return; }
  showTab("results");
  pollJob();
}

async function pollJob() {
  clearTimeout(pollTimer);
  const j = await api("/api/job");
  jobRunning = !!j.running;
  const pill = $("job-pill");
  if (!j.kind) { pill.className = "pill idle"; pill.textContent = "idle"; }
  else if (j.running) { pill.className = "pill running"; pill.textContent = `${(JOB_NAMES[j.kind] || j.kind).toLowerCase()} · ${j.elapsed_min} min`; }
  else { pill.className = "pill " + (j.returncode === 0 ? "done" : "failed"); pill.textContent = `${(JOB_NAMES[j.kind] || j.kind).toLowerCase()} ${j.returncode === 0 ? "finished" : "failed"}`; }
  renderJob(j);
  refreshCheck();
  if (j.sweep_label && activeTab === "sweep") showSweep(j.sweep_label, true);
  if (j.running) pollTimer = setTimeout(pollJob, 4000);
  else if (j.kind && activeTab === "results") loadResults();
}

const JOB_NAMES = { dry_run: "Dry run", optimize: "Optimize", sweep: "Sweep", sweep_preview: "Sweep preview (AEDT)" };

function renderJob(j) {
  if (!j.kind) return;
  for (const id of ["job-box", "job-box-sweep"]) {      // Results and Sweep tabs
    const box = $(id);
    box.replaceChildren();
    const meta = document.createElement("div"); meta.className = "meta";
    meta.textContent = `${JOB_NAMES[j.kind] || j.kind}${j.sweep_label ? " " + j.sweep_label : ""} · ${j.running ? "running" : "finished, exit " + j.returncode} · ${j.elapsed_min} min · log ${j.log}`;
    const pre = document.createElement("pre");
    pre.textContent = (j.key.length ? j.key : j.tail).join("\n");
    box.append(meta, pre);
    if (j.running) {
      const stop = document.createElement("button"); stop.className = "secondary"; stop.textContent = "Stop";
      stop.onclick = async () => { if (confirm("Stop the run? The HFSS solve in progress is lost.")) { await api("/api/stop", {}); pollJob(); } };
      box.append(stop);
    }
  }
}

// ---------------------------------------------------------------- results ---
const FOMS = [
  ["f01_GHz", "Qubit frequency f01", "GHz", 4], ["f_r_GHz", "Resonator frequency f_r", "GHz", 4],
  ["alpha_MHz", "Anharmonicity α", "MHz", 1], ["chi_MHz", "Dispersive shift χ_qr", "MHz", 3],
  ["g_MHz", "Coupling g", "MHz", 1], ["EJ_over_EC", "E_J / E_C", "", 1], ["delta_GHz", "Detuning Δ", "GHz", 3],
  ["kappa_MHz", "Linewidth κ/2π", "MHz", 3], ["Q_r", "Resonator Q", "", 0], ["T1_purcell_us", "Purcell T1", "µs", 1],
];

async function loadResults() {
  const list = await api("/api/results");
  const root = $("results");
  root.replaceChildren();
  if (!list.length) { root.textContent = "No delivered designs yet."; return; }
  for (const r of list) root.append(resultCard(r));
}

function resultCard(r) {
  const card = document.createElement("article"); card.className = "result";
  const head = document.createElement("div"); head.className = "result-head";
  const h = document.createElement("h3"); h.textContent = r.name;
  const when = document.createElement("span"); when.className = "muted";
  when.textContent = `${r.created || ""} · L_J ${r.Lj_nH / 2} nH, C_J ${r.Cj_fF * 2} fF total · GDS check ${r.gds_ok ? "OK" : "—"}`;
  head.append(h, when);
  card.append(head);

  const tbl = document.createElement("table");
  const hr = tbl.insertRow(); for (const s of ["", "target", "solved", ""]) { const th = document.createElement("th"); th.textContent = s; hr.append(th); }
  const fom = r.fom || {};
  for (const [k, label, unit, d] of FOMS) {
    if (fom[k] === undefined || fom[k] === null) continue;
    const row = tbl.insertRow();
    row.insertCell().textContent = label;
    const t = r.targets && r.targets[k];
    row.insertCell().textContent = t !== undefined ? `${t} ${unit}` : "";
    const v = row.insertCell(); v.className = "num"; v.textContent = `${Number(fom[k]).toFixed(d)} ${unit}`;
    const st = row.insertCell();
    if (t !== undefined && r.tolerances && r.tolerances[k]) {
      const [lo, hi] = r.tolerances[k]; const ok = fom[k] >= lo && fom[k] <= hi;
      st.className = ok ? "ok" : "off"; st.textContent = ok ? "✓ in band" : "✗ off target";
    }
  }
  card.append(tbl);

  const links = document.createElement("div"); links.className = "links";
  const btn = (label, fn, disabled, title) => { const b = document.createElement("button"); b.className = "secondary"; b.textContent = label; b.disabled = !!disabled; if (title) b.title = title; b.onclick = fn; links.append(b); };
  btn("Open .aedt (fields solved)", () => api("/api/open", { name: r.name, what: "aedt" }).then((x) => x.ok || alert(x.error)),
      !r.aedt_solved, r.aedt_solved ? r.paths.aedt : "re-solve not finished");
  btn("Show folder", () => api("/api/open", { name: r.name, what: "folder" }));
  btn("Show geometry in design screen", () => { viewKnobs = r.knobs; viewName = r.name; showTab("design"); refreshPreview(); });
  btn("Reset design screen to nominal", () => { viewKnobs = null; viewName = null; refreshPreview(); });
  card.append(links);
  const path = document.createElement("div"); path.className = "path"; path.textContent = r.paths.aedt; card.append(path);
  const note = document.createElement("p"); note.className = "fieldnote";
  note.textContent = "Fields: in AEDT open the design → Field Overlays → Plot Fields → E → Mag_E; the eigenmode (qubit / resonator) is picked in the plot's solution settings.";
  card.append(note);

  if (r.summary) {
    const img = document.createElement("img"); img.className = "summary"; img.src = r.summary + "?t=" + Date.now(); img.alt = `summary of ${r.name}`;
    img.onclick = () => { $("zoom-img").src = img.src; $("zoom-dialog").showModal(); };
    card.append(img);
  }
  return card;
}

// ------------------------------------------------------------------- tabs ---
function showTab(name) {
  activeTab = name;
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const p of document.querySelectorAll(".tabpage")) p.classList.toggle("hidden", p.id !== `tab-${name}`);
  const res = name === "results", sw = name === "sweep";
  $("screen").classList.toggle("hidden", res || sw);
  $("results").classList.toggle("hidden", !res);
  $("sweepview").classList.toggle("hidden", !sw);
  $("actions").classList.toggle("hidden", sw);
  if (res) { loadResults(); pollJob(); }
  if (sw) { renderSweepForm(); loadSweeps(); pollJob(); }
}

// What this computer can run (capabilities.py): a banner naming what is off and why.
async function showCapabilities() {
  let c;
  try { c = await api("/api/capabilities"); } catch (e) { return; }
  const off = Object.values(c.features || {}).filter((f) => !f.ok);
  const box = $("caps-banner");
  if (!off.length) { box.hidden = true; return; }
  box.replaceChildren();
  const head = document.createElement("b");
  head.textContent = "Limited on this computer. ";
  box.append(head);
  for (const f of off) {
    const s = document.createElement("span");
    s.textContent = `${f.label}: off (missing ${f.missing.join(", ")}). `;
    s.title = f.hint;
    box.append(s);
  }
  const py = document.createElement("code");
  py.textContent = c.python;
  box.append("Python: ", py);
  box.hidden = false;
}

async function main() {
  OPTIONS = await api("/api/options");
  showCapabilities();
  for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => showTab(t.dataset.tab));
  $("btn-opt").onclick = () => startJob(false);
  $("btn-dry").onclick = () => startJob(true);
  $("btn-example").onclick = () => { state = structuredClone(EXAMPLE_STATE); changed(); };
  bindSweep();
  $("btn-new").onclick = () => { state = structuredClone(DEFAULT_STATE); viewKnobs = viewName = null; changed(); };
  bindInputs();
  render();
  refreshPreview();
  refreshCheck();
  showTab(activeTab);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { refreshPreview(); drawStack(); });
}
main();


// ------------------------------------------------------------------ sweep ---
// One knob, a range of values, around nominal (or the result shown in the
// design screen); every point is an ordinary solve in data/, kappa included
// when a feedline is picked. Charts: one FoM each vs the knob (small multiples).
let SWEEP_KNOBS = null, currentSweep = null, lastPreview = null;
const SWEEP_CHARTS = [
  ["f_r_GHz", "Resonator f_r", "GHz", 4], ["chi_MHz", "Dispersive shift χ_qr", "MHz", 3],
  ["kappa_MHz", "Linewidth κ/2π", "MHz", 3], ["g_MHz", "Coupling g", "MHz", 1],
  ["f01_GHz", "Qubit f01", "GHz", 4], ["T1_purcell_us", "Purcell T1", "µs", 1],
];

function sweepValues() {
  const a = Number($("sw-start").value), b = Number($("sw-stop").value), n = Math.round(Number($("sw-n").value));
  if (!(a > 0 && b > 0 && n >= 1 && n <= 15)) return [];
  if (n === 1) return [a];
  return Array.from({ length: n }, (_, i) => Math.round((a + (b - a) * i / (n - 1)) * 10) / 10);
}

async function renderSweepForm() {
  if (!SWEEP_KNOBS) SWEEP_KNOBS = await api("/api/sweep_knobs");
  lastPreview = await api("/api/preview", { state, knobs: viewKnobs });
  const sel = $("sw-knob"), cur = sel.value;
  sel.replaceChildren();
  for (const [k, info] of Object.entries(SWEEP_KNOBS)) {
    const o = document.createElement("option");
    const missing = info.needs && (state.design[info.needs] || "none") === "none";
    o.value = k; o.disabled = missing;
    o.textContent = info.label + (missing ? ` (pick a ${info.needs} first)` : "");
    sel.append(o);
  }
  if (cur && !sel.querySelector(`option[value="${cur}"]`).disabled) sel.value = cur;
  else if (sel.selectedOptions[0] && sel.selectedOptions[0].disabled) sel.value = "t_length";
  sweepKnobChanged(!cur);
}

function knobUm(k) {
  const v = (lastPreview && lastPreview.info.knobs[k]) || "";
  const m = String(v).match(/^([-\d.eE+]+)\s*(um|mm)?$/);
  return m ? Number(m[1]) * (m[2] === "mm" ? 1000 : 1) : NaN;
}

function sweepKnobChanged(fill) {
  const k = $("sw-knob").value, base = knobUm(k);
  $("sw-note").textContent = `${SWEEP_KNOBS[k].note}. Now ${isNaN(base) ? "?" : base + " µm"}`
    + (viewName ? ` (result ${viewName})` : " (nominal)") + ".";
  if (fill && !isNaN(base)) {                  // a sensible default: 75 % .. 125 %
    $("sw-start").value = Math.round(base * 0.75 * 10) / 10;
    $("sw-stop").value = Math.round(base * 1.25 * 10) / 10;
    $("sw-n").value = 5;
  }
  sweepPlanText();
}

function sweepPlanText() {
  const vals = sweepValues(), lines = (state.design.feedline || "none") !== "none" || (state.design.flux_line || "none") !== "none";
  const per = lines ? "~60–80" : "~40";
  $("sw-plan").textContent = vals.length
    ? `values (µm): ${vals.join(", ")} + the base point. Around ${viewName || "nominal"}; L_J ${state.fom.lj_total_nH || "?"} nH, C_J ${state.fom.cj_total_fF || "?"} fF (Figures of merit tab); parts from the Design tab. About ${per} min per new point${lines ? " (lines enlarge the simulated region)" : ""}; solved points are reused.`
    : "enter a start, a stop and 1–15 points";
}

function bindSweep() {
  $("sw-knob").onchange = () => sweepKnobChanged(true);
  for (const id of ["sw-start", "sw-stop", "sw-n"]) $(id).oninput = sweepPlanText;
  $("sw-check").onclick = () => launchSweep(true);
  $("sw-run").onclick = () => launchSweep(false);
  $("sw-list").onchange = () => showSweep($("sw-list").value);
}

async function launchSweep(dry) {
  const vals = sweepValues();
  if (dry && !confirm(`Render ${vals.length} values + the base point into a preview AEDT project (about a minute each, no solve)?

AEDT is busy until it finishes; then "Open preview in AEDT".`)) return;
  if (!dry && !confirm(`Run this sweep in HFSS?\n\n${vals.length} values + the base point; solved points are reused. AEDT is busy until it finishes.`)) return;
  const r = await api("/api/sweep", { state, knob: $("sw-knob").value, values_um: vals,
    base_knobs: viewKnobs, base_name: viewName, dry_run: dry });
  if (!r.ok) { alert(r.error); return; }
  await loadSweeps(r.label);
  pollJob();
}

async function loadSweeps(select) {
  const list = await api("/api/sweeps"), sel = $("sw-list");
  sel.replaceChildren();
  for (const m of list) {
    const o = document.createElement("option"); o.value = m.label;
    o.textContent = `${m.label} · ${m.knob} ${m.values_um[0]}–${m.values_um[m.values_um.length - 1]} µm · ${Object.keys(m.parts).length ? Object.entries(m.parts).map(([a, b]) => a).join(" + ") : "no lines"}`;
    sel.append(o);
  }
  if (!list.length) { $("sw-view").textContent = "No sweeps yet: set one up on the left."; return; }
  sel.value = select || currentSweep || list[0].label;
  showSweep(sel.value);
}

async function showSweep(label, quiet) {
  if (!label) return;
  currentSweep = label;
  const r = await fetch(`/api/sweep_results?label=${encodeURIComponent(label)}`).then((x) => x.json());
  if (r.error) { if (!quiet) $("sw-view").textContent = r.error; return; }
  const root = $("sw-view");
  root.replaceChildren();
  const solved = r.rows.filter((x) => x.solved).length;
  const head = document.createElement("p"); head.className = "sw-head";
  head.textContent = `${r.knob_label} (${r.meta.knob}) · around ${r.meta.base_name} (base ${r.base_um} µm, ringed) · L_J ${r.meta.lj_total_nH} nH, C_J ${r.meta.cj_total_fF} fF · parts: ${Object.keys(r.meta.parts).length ? JSON.stringify(r.meta.parts) : "default"} · ${solved}/${r.rows.length} solved${r.running ? " · running" : ""}`;
  root.append(head);
  if (r.preview_exists || r.meta.preview_project) {
    const bar = document.createElement("div"); bar.className = "links";
    const b = document.createElement("button"); b.className = "secondary";
    b.textContent = "Open preview in AEDT"; b.disabled = !r.preview_exists || jobRunning;
    b.title = r.preview_exists ? `${r.meta.preview_project}: every point rendered and set up, not solved`
      : "click Preview in AEDT first";
    b.onclick = async () => { const x = await api("/api/open_preview", { label: r.meta.label }); if (!x.ok) alert(x.error); };
    const pv = document.createElement("button"); pv.className = "secondary";
    pv.textContent = r.preview_exists ? "Re-preview" : "Preview in AEDT"; pv.disabled = jobRunning;
    pv.title = "render every point of THIS sweep into its preview project (no solve)";
    pv.onclick = async () => { const x = await api("/api/sweep_rerun", { label: r.meta.label, preview: true }); if (!x.ok) alert(x.error); else pollJob(); };
    const run = document.createElement("button"); run.className = "primary";
    run.textContent = "Run this sweep"; run.disabled = jobRunning || solved === r.rows.length;
    run.title = solved === r.rows.length ? "every point is solved" : `solve the ${r.rows.length - solved} unsolved point(s) in HFSS`;
    run.onclick = async () => {
      if (!confirm(`Solve the ${r.rows.length - solved} unsolved point(s) of ${r.meta.label} in HFSS?

AEDT is busy until it finishes.`)) return;
      const x = await api("/api/sweep_rerun", { label: r.meta.label }); if (!x.ok) alert(x.error); else pollJob();
    };
    const note = document.createElement("span"); note.className = "hint";
    note.textContent = r.preview_exists ? `${r.meta.preview_project} — opens in the AEDT window the automation uses` : "no preview yet";
    bar.append(b, pv, run, note); root.append(bar);
  }
  const grid = document.createElement("div"); grid.className = "sw-grid";
  for (const [k, label, unit, d] of SWEEP_CHARTS) grid.append(sweepChart(r, k, label, unit, d));
  root.append(grid);
  root.append(sweepTable(r));
}

function sweepChart(r, k, label, unit, d) {
  const fig = document.createElement("figure"); fig.className = "sw-chart";
  const cap = document.createElement("figcaption"); cap.textContent = `${label}${unit ? " (" + unit + ")" : ""}`;
  fig.append(cap);
  const pts = r.rows.filter((x) => x.solved && x.fom[k] !== null && x.fom[k] !== undefined && isFinite(x.fom[k]));
  if (!pts.length) {
    const p = document.createElement("p"); p.className = "muted";
    const lossy = Object.keys(r.meta.parts || {}).some((x) => x === "feedline" || x === "flux_line");
    p.textContent = (k === "kappa_MHz" || k === "T1_purcell_us") && !lossy
      ? "needs a feedline / flux line (50 Ω ends): pick one in the Design tab" : "no solved points yet";
    fig.append(p); return fig;
  }
  const W = 300, Hh = 170, L = 48, R = 10, T = 10, B = 30;
  const xs = r.rows.map((x) => x.x_um), ys = pts.map((x) => x.fom[k]);
  let [x0, x1] = [Math.min(...xs), Math.max(...xs)], [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  const pad = (y1 - y0) * 0.12 || Math.abs(y0) * 0.02 || 1; y0 -= pad; y1 += pad;
  const X = (v) => L + (v - x0) / (x1 - x0) * (W - L - R), Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (Hh - T - B);
  const css = getComputedStyle(document.documentElement);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${Hh}`, class: "sw-svg" });
  for (let i = 0; i <= 3; i++) {                 // recessive grid + y ticks
    const v = y0 + (y1 - y0) * i / 3, yy = Y(v);
    svgEl("line", { x1: L, x2: W - R, y1: yy, y2: yy, stroke: css.getPropertyValue("--rule"), "stroke-width": 1 }, svg);
    const t = svgEl("text", { x: L - 6, y: yy + 4, "text-anchor": "end", "font-size": 10, fill: css.getPropertyValue("--muted") }, svg);
    t.textContent = v.toFixed(Math.max(0, d - 1));
  }
  for (const v of [x0, x1]) {
    const t = svgEl("text", { x: X(v), y: Hh - 10, "text-anchor": v === x0 ? "start" : "end", "font-size": 10, fill: css.getPropertyValue("--muted") }, svg);
    t.textContent = `${v} µm`;
  }
  svgEl("polyline", { points: pts.map((p) => `${X(p.x_um)},${Y(p.fom[k])}`).join(" "), fill: "none",
    stroke: css.getPropertyValue("--accent"), "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const tip = $("sw-tip");
  for (const p of pts) {
    const c = svgEl("circle", { cx: X(p.x_um), cy: Y(p.fom[k]), r: 4, fill: css.getPropertyValue("--accent"),
      stroke: css.getPropertyValue("--surface"), "stroke-width": 2 }, svg);
    if (p.base) svgEl("circle", { cx: X(p.x_um), cy: Y(p.fom[k]), r: 7, fill: "none", stroke: css.getPropertyValue("--ink-2"), "stroke-width": 1 }, svg);
    const hit = svgEl("circle", { cx: X(p.x_um), cy: Y(p.fom[k]), r: 12, fill: "transparent" }, svg);
    hit.addEventListener("mouseenter", (e) => {
      tip.textContent = `${r.meta.knob} ${p.x_um} µm${p.base ? " (base)" : ""}: ${label} ${Number(p.fom[k]).toFixed(d)} ${unit}`;
      tip.classList.remove("hidden"); tip.style.left = `${e.clientX + 12}px`; tip.style.top = `${e.clientY + 12}px`;
    });
    hit.addEventListener("mouseleave", () => tip.classList.add("hidden"));
    void c;
  }
  fig.append(svg);
  return fig;
}

function sweepTable(r) {
  const tbl = document.createElement("table"); tbl.className = "sw-table";
  const cols = [["x", `${r.meta.knob} (µm)`], ["f_r_GHz", "f_r (GHz)", 4], ["f01_GHz", "f01 (GHz)", 4],
    ["chi_MHz", "χ_qr (MHz)", 3], ["g_MHz", "g (MHz)", 1], ["kappa_MHz", "κ/2π (MHz)", 3], ["Q_r", "Q_r", 0],
    ["T1_purcell_us", "T1 Purcell (µs)", 1], ["p_q_total", "p_q", 3], ["status", ""]];
  const hr = tbl.insertRow();
  for (const [, h] of cols) { const th = document.createElement("th"); th.textContent = h; hr.append(th); }
  for (const p of r.rows) {
    const row = tbl.insertRow();
    for (const [k, , d] of cols) {
      const c = row.insertCell();
      if (k === "x") c.textContent = `${p.x_um}${p.base ? " (base)" : ""}`;
      else if (k === "status") { c.textContent = p.solved ? (p.warnings.length ? "⚠ " + p.warnings.length + " warning(s)" : "solved") : "pending"; if (p.warnings.length) c.title = p.warnings.join("\n"); }
      else { const v = p.fom[k]; c.className = "num"; c.textContent = v === null || v === undefined ? "–" : Number(v).toFixed(d); }
    }
  }
  return tbl;
}
