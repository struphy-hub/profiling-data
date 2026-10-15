import Plotly from "plotly.js-dist-min";
import { SUMMARY_METRICS } from "@scope-profiler/plotly";
import { renderFigure, forgetFigure, matchesRegionFilter, parseRegionFilter } from "./charts.js";

export const metricLabels = { total: "Total", avg: "Average", min: "Minimum", max: "Maximum", first: "First call", last: "Last call" };
export const esc = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
export const fmt = (value) => typeof value !== "number" || !Number.isFinite(value) ? "—" : Math.abs(value) >= 1 ? value.toFixed(4) : value.toExponential(3);
export const metricValue = (stats, metric) => stats?.[SUMMARY_METRICS[metric]];
const cache = new Map();
export function fetchAnalysisJson(baseUrl, path) {
  if (!path) return Promise.reject(new Error("Analysis data is not available for this run."));
  const url = `${baseUrl}figures/${path}`;
  if (!cache.has(url)) cache.set(url, fetch(url).then((res) => {
    if (!res.ok) throw new Error(`Could not load analysis (HTTP ${res.status}).`);
    return res.json();
  }).catch((error) => { cache.delete(url); throw error; }));
  return cache.get(url);
}
export function mergeRegions(manifests) {
  const regions = new Map();
  for (const manifest of manifests) {
    for (const region of manifest.regions ?? []) {
      const old = regions.get(region.name);
      const depths = [old?.depth, region.depth].filter(Number.isFinite);
      regions.set(region.name, { name: region.name, depth: depths.length ? Math.min(...depths) : null, total: Math.max(old?.total ?? 0, region.total ?? 0) });
    }
  }
  return [...regions.values()];
}
export function filterRegionRows(regions, { search = "", depth = Infinity, top = Infinity } = {}) {
  const terms = parseRegionFilter(search);
  return regions.filter((region) => (region.depth ?? 0) <= depth && (!terms.length || matchesRegionFilter(region.name, terms)))
    .sort((a, b) => (b.total ?? 0) - (a.total ?? 0) || a.name.localeCompare(b.name)).slice(0, top);
}

/** One predicate and one metric shared by the charts and the page's table. */
export class AnalysisViewer {
  constructor(host, baseUrl, onChange = () => {}) {
    this.host = host;
    this.baseUrl = baseUrl;
    this.onChange = onChange;
    this.version = 0;
    this.chartStates = [];
    this.state = { search: "", top: 30, depth: Infinity, metric: "total" };
    this.defaultMetric = "total";
    this.hasMetricSelection = false;
    host.innerHTML = `<div class="analysis-toolbar card" role="group" aria-label="Analysis filters">
      <label class="analysis-search">Regions<input type="search" data-control="search" aria-label="Filter regions" class="region-filter" placeholder="All regions · prop:, ^setup" autocomplete="off" title="Comma-separated terms match any substring; ^ anchors a term to the start."></label>
      <label>Top regions <output data-output="top">30</output><input type="range" data-control="top" aria-label="Top regions" min="1" max="30" value="30"></label>
      <label>Nesting depth <output data-output="depth">All</output><input type="range" data-control="depth" aria-label="Nesting depth" min="0" max="0" value="0"></label>
      <label>Timing metric<select data-control="metric" aria-label="Timing metric" class="metric-select">${Object.entries(metricLabels).map(([key, label]) => `<option value="${key}">${label}</option>`).join("")}</select></label>
      <button type="button" data-control="reset" class="rank-btn">Reset filters</button>
      <span class="muted analysis-count" aria-live="polite" data-output="count">Loading analysis…</span>
    </div><p class="muted analysis-help">Search matches any comma-separated term. Charts and timing tables share these filters; top regions are ranked by total time. Depth 0 is the outermost call.</p>
    <div class="analysis-summary"></div><div class="analysis-charts"></div><div class="analysis-unavailable muted"></div>`;
    this.controls = Object.fromEntries([...host.querySelectorAll("[data-control]")].map((el) => [el.dataset.control, el]));
    this.chartsHost = host.querySelector(".analysis-charts");
    let timer;
    for (const key of ["search", "top", "depth", "metric"]) {
      this.controls[key].addEventListener(key === "metric" ? "change" : "input", () => {
        this.state[key] = ["top", "depth"].includes(key) ? Number(this.controls[key].value) : this.controls[key].value;
        if (key === "metric") this.hasMetricSelection = true;
        this.updateLabels();
        window.clearTimeout(timer);
        timer = window.setTimeout(() => this.refresh(), key === "metric" ? 0 : 160);
      });
    }
    this.controls.reset.addEventListener("click", () => {
      window.clearTimeout(timer);
      this.state = { search: "", top: Math.min(30, this.manifest?.regions?.length || 1), depth: this.maxDepth ?? 0, metric: this.defaultMetric };
      this.hasMetricSelection = false;
      this.syncControls();
      this.refresh();
    });
  }
  syncControls() {
    for (const key of ["search", "top", "depth", "metric"]) this.controls[key].value = String(this.state[key]);
    this.updateLabels();
  }
  updateLabels() {
    this.host.querySelector('[data-output="top"]').textContent = this.state.top >= (this.manifest?.regions?.length ?? Infinity) ? "All" : this.state.top;
    this.host.querySelector('[data-output="depth"]').textContent = this.state.depth >= this.maxDepth ? "All" : this.state.depth;
  }
  selection() {
    const rows = filterRegionRows(this.manifest?.regions ?? [], this.state);
    return { ...this.state, rows, regions: new Set(rows.map((row) => row.name)), files: this.manifest?.files ?? [] };
  }
  async load(path) {
    this.setLoading();
    try { await this.setManifest(await fetchAnalysisJson(this.baseUrl, path)); }
    catch (error) { this.setError(error); }
  }
  clearCharts() {
    this.version++;
    for (const chart of this.chartStates) forgetFigure(chart.plot);
    this.chartStates = [];
    this.chartsHost.replaceChildren();
  }
  setLoading() {
    this.clearCharts();
    this.manifest = null;
    this.host.querySelector(".analysis-summary").replaceChildren();
    this.host.querySelector(".analysis-unavailable").replaceChildren();
    this.host.querySelector('[data-output="count"]').textContent = "Loading analysis…";
    this.chartsHost.innerHTML = '<p class="muted" role="status">Loading analysis…</p>';
  }
  setError(error) {
    this.clearCharts();
    this.manifest = null;
    this.chartsHost.innerHTML = `<p role="alert">${esc(error.message ?? error)}</p>`;
    this.host.querySelector('[data-output="count"]').textContent = "Analysis unavailable";
  }
  async setManifest(manifest) {
    this.clearCharts();
    this.manifest = manifest;
    const rankCounts = new Set((manifest.files ?? []).map((file) => file.num_ranks).filter(Number.isFinite));
    this.defaultMetric = rankCounts.size > 1 ? "avg" : manifest.charts?.find((chart) => ["durations", "comparison"].includes(chart.kind))?.options?.metric ?? "total";
    if (!this.hasMetricSelection) this.state.metric = this.defaultMetric;
    this.renderSummary(manifest.summary ?? []);
    this.maxDepth = Math.max(0, ...(manifest.regions ?? []).map((region) => region.depth ?? 0));
    this.state.depth = this.maxDepth;
    this.state.top = Math.min(this.state.top, manifest.regions?.length || 1);
    this.controls.top.max = String(manifest.regions?.length || 1);
    this.controls.depth.max = String(this.maxDepth);
    this.controls.depth.disabled = this.maxDepth === 0;
    this.syncControls();
    for (const def of manifest.charts ?? []) this.addChart(def);
    if (!this.chartStates.length) this.chartsHost.innerHTML = '<p class="muted">No charts are available for these recorded measurements.</p>';
    const unavailable = manifest.unavailable ?? [];
    this.host.querySelector(".analysis-unavailable").innerHTML = unavailable.length ? `<details><summary>Unavailable measurements (${unavailable.length})</summary><ul>${unavailable.map((item) => `<li><strong>${esc(item.title)}:</strong> ${esc(item.reason)}</li>`).join("")}</ul></details>` : "";
    await this.refresh();
  }
  renderSummary(rows) {
    this.host.querySelector(".analysis-summary").innerHTML = rows.length ? `<div class="table-wrap"><table><caption>Recorded runs</caption><thead><tr><th>Run</th><th>Ranks</th><th>Nodes</th><th>Wall time (s)</th></tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.label)}</td><td>${esc(row.ranks ?? "—")}</td><td>${esc(row.nodes ?? "—")}</td><td>${fmt(row.wall_time_seconds)}</td></tr>`).join("")}</tbody></table></div>` : "";
  }
  addChart(def) {
    const panel = document.createElement("details");
    panel.className = "card analysis-chart";
    panel.open = Boolean(def.open);
    const comparison = def.kind === "comparison";
    const variants = def.variants ?? [];
    panel.innerHTML = `<summary>${esc(def.title)}</summary><p class="muted analysis-description">${esc(def.description)}</p><div class="analysis-chart-controls"><button type="button" class="rank-btn" data-chart-control="fullscreen" aria-label="Expand ${esc(def.title)} to full screen">Full screen</button>${comparison ? `<label>View<select class="metric-select" data-chart-control="comparison"><option value="side-by-side">Side by side</option><option value="percent">Change (%)</option><option value="absolute">Change (s)</option></select></label>` : ""}${variants.length ? `<label>Resource axis<select class="metric-select" data-chart-control="variant">${variants.map((variant, i) => `<option value="${i}">${esc(variant.label)}</option>`).join("")}</select></label>` : ""}</div><div class="plot-container"></div>`;
    const chart = { def, panel, plot: panel.querySelector(".plot-container"), comparison: def.options?.comparison ?? "side-by-side", variant: 0, request: 0 };
    const fullscreen = panel.querySelector('[data-chart-control="fullscreen"]');
    fullscreen.hidden = !document.fullscreenEnabled;
    fullscreen.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement === panel) await document.exitFullscreen();
        else await panel.requestFullscreen();
      } catch {
        fullscreen.textContent = "Full screen unavailable";
      }
    });
    panel.addEventListener("fullscreenchange", () => {
      fullscreen.textContent = document.fullscreenElement === panel ? "Exit full screen" : "Full screen";
      if (chart.plot.classList.contains("js-plotly-plot")) Plotly.Plots.resize(chart.plot);
    });
    const compareSelect = panel.querySelector('[data-chart-control="comparison"]');
    if (compareSelect) {
      compareSelect.value = chart.comparison;
      compareSelect.addEventListener("change", () => { chart.comparison = compareSelect.value; this.draw(chart); });
    }
    panel.querySelector('[data-chart-control="variant"]')?.addEventListener("change", (event) => { chart.variant = Number(event.target.value); this.draw(chart); });
    panel.addEventListener("toggle", () => { if (panel.open) this.draw(chart); });
    this.chartsHost.appendChild(panel);
    this.chartStates.push(chart);
  }
  async refresh() {
    if (!this.manifest) return;
    const selection = this.selection();
    this.updateLabels();
    this.host.querySelector('[data-output="count"]').textContent = `${selection.regions.size} of ${this.manifest.regions.length} regions shown`;
    this.onChange(selection);
    await Promise.all(this.chartStates.filter((chart) => chart.panel.open).map((chart) => this.draw(chart)));
  }
  async draw(chart) {
    const request = ++chart.request;
    const version = this.version;
    const { def } = chart;
    try {
      const variant = def.variants?.[chart.variant];
      const payload = def.payload ?? await fetchAnalysisJson(this.baseUrl, variant?.path ?? def.path);
      if (version !== this.version || request !== chart.request || !chart.panel.open) return;
      const selection = this.selection();
      const metric = this.state.metric;
      // Stacked child exports support only total and average. Make that
      // limitation explicit instead of silently showing a different metric.
      const metrics = payload.metrics ?? (payload.bars ? [...new Set(payload.bars.map((bar) => bar.metric).filter(Boolean))] : []);
      if (def.kind === "durations" && metrics.length && !metrics.includes(metric)) {
        forgetFigure(chart.plot);
        chart.plot.innerHTML = `<p class="empty-state">${esc(metricLabels[metric])} is unavailable for this chart. Select ${metrics.map((item) => esc(metricLabels[item] ?? item)).join(" or ")} in the shared toolbar.</p>`;
        return;
      }
      const options = {
        ...def.options,
        ...variant?.options,
        filterRegion: (region) => selection.regions.has(region),
        topN: Infinity,
        ...(def.kind === "gantt" && this.state.depth < this.maxDepth ? { maxDepth: this.state.depth } : {}),
        ...(["durations", "comparison", "region_statistics"].includes(def.kind) ? { metric } : {}),
        ...(def.kind === "comparison" ? { comparison: chart.comparison, commonRegionsOnly: false } : {}),
        layout: { uirevision: def.id, height: 520, margin: { l: 95, r: 32, t: 35, b: 160 }, ...def.options?.layout },
      };
      if (!chart.plot.classList.contains("js-plotly-plot")) chart.plot.replaceChildren();
      await renderFigure(Plotly, chart.plot, def.kind, payload, options);
    } catch (error) {
      if (version !== this.version || request !== chart.request) return;
      forgetFigure(chart.plot);
      chart.plot.innerHTML = `<p role="alert">Could not render ${esc(def.title)}: ${esc(error.message ?? error)}</p>`;
    }
  }
}

export function renderStatisticsTable(tbody, file, selection) {
  const stats = file?.region_statistics ?? {};
  tbody.innerHTML = selection.rows.filter((region) => stats[region.name]).map(({ name }) => {
    const s = stats[name];
    return `<tr data-region="${esc(name)}"><td>${esc(name)}</td><td>${s.count ?? "—"}</td><td>${fmt(s.average_duration_seconds)}</td><td>${fmt(s.min_duration_seconds)}</td><td>${fmt(s.max_duration_seconds)}</td><td>${fmt(s.total_duration_seconds)}</td></tr>`;
  }).join("") || '<tr><td colspan="6" class="empty-state">No recorded regions match these filters.</td></tr>';
}
