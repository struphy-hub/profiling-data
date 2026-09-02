// Shared Plotly chart builders for the profiling figures pages.
import {
  buildDurationsFigure as buildScopeDurationsFigure,
  buildFlameFigure as buildScopeFlameFigure,
  buildGanttFigure as buildScopeGanttFigure,
  buildSpeedupFigure as buildScopeSpeedupFigure,
  renderFigure as renderScopeFigure,
} from "@scope-profiler/plotly";
//
// Colors come from the validated 8-slot categorical palette (light mode) -
// see the dataviz skill's references/palette.md. Assigned in fixed order to
// the first 8 distinct series names encountered; series past that get
// generated hues (see overflowColor) rather than repeating or folding to gray.
const CATEGORICAL_PALETTE = [
  "#2a78d6", // blue
  "#eb6834", // orange
  "#1baf7a", // aqua
  "#eda100", // yellow
  "#e87ba4", // magenta
  "#008300", // green
  "#4a3aa7", // violet
  "#e34948", // red
];
const FONT_FAMILY =
  "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif";

// Chrome colors (text, gridlines, hover surface) follow the site's active theme
// (the `data-theme` attribute set by the header toggle) so the charts stay
// legible in dark mode. The categorical series palette above is vivid enough to
// read on both backgrounds, so it is not themed.
function themeColors() {
  const dark =
    typeof document !== "undefined" &&
    document.documentElement.dataset.theme === "dark";
  return dark
    ? { text: "#e5e7eb", muted: "#9ca3af", grid: "#2a2f3a", hoverBg: "#171a21" }
    : { text: "#111827", muted: "#6b7280", grid: "#e1e0d9", hoverBg: "#ffffff" };
}

// Past the 8 validated hues (a gantt chart of a deeply instrumented run can
// need far more), colors are generated instead of folding to one gray. Hues
// advance by the golden angle so neighbouring slots land far apart on the
// wheel, and lightness/saturation alternate so hues that do come close still
// differ in tone. Mid lightness keeps them legible on both backgrounds.
function overflowColor(index) {
  const hue = (24 + 137.508 * (index + 1)) % 360;
  const lightness = [46, 62, 38, 54][index % 4];
  const saturation = index % 3 === 0 ? 70 : 52;
  return `hsl(${hue.toFixed(1)}, ${saturation}%, ${lightness}%)`;
}

export function assignColors(names) {
  const colors = new Map();
  let slot = 0;
  for (const name of names) {
    if (colors.has(name)) continue;
    colors.set(
      name,
      slot < CATEGORICAL_PALETTE.length
        ? CATEGORICAL_PALETTE[slot]
        : overflowColor(slot - CATEGORICAL_PALETTE.length),
    );
    slot += 1;
  }
  return colors;
}

const baseLayout = (overrides = {}) => {
  const c = themeColors();
  return {
    font: { family: FONT_FAMILY, color: c.text, size: 12 },
    paper_bgcolor: "transparent",
    plot_bgcolor: "transparent",
    margin: { l: 160, r: 24, t: 16, b: 48 },
    hoverlabel: { bgcolor: c.hoverBg, bordercolor: c.grid, font: { color: c.text } },
    legend: { orientation: "h", y: -0.18, font: { color: c.muted, size: 11 } },
    ...overrides,
  };
};

const axisStyle = (overrides = {}) => {
  const c = themeColors();
  return {
    gridcolor: c.grid,
    zerolinecolor: c.grid,
    linecolor: c.grid,
    tickfont: { color: c.muted, size: 11 },
    title: { font: { color: c.muted, size: 12 } },
    ...overrides,
  };
};

const plotConfig = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

const METRIC_LABELS = {
  avg: "Average duration per call (s)",
  min: "Minimum duration per call (s)",
  max: "Maximum duration per call (s)",
  total: "Total duration (s)",
  first: "First call duration (s)",
  last: "Last call duration (s)",
};

// What the region filter boxes are prefilled with: the top-level integration
// loop, the merged setup span, and the propagators. Kernels and other
// fine-grained regions appear once the viewer edits or clears the filter.
// "^prop:" is anchored so the propagators show up without also dragging in
// their "setup prop: X" counterparts; drop the ^ to include those too.
export const DEFAULT_REGION_FILTER = "model.integrate, ^prop:, setup: total";

// A region filter is a comma-separated list of terms, each matched as a
// case-insensitive substring of the region name: "prop:" keeps every
// propagator, "prop: push, setup: total" keeps those two groups. Empty terms
// (a trailing comma while typing) are ignored.
export function parseRegionFilter(text) {
  return String(text ?? "")
    .split(",")
    .map((term) => term.trim().toLowerCase())
    .filter(Boolean);
}

export function matchesRegionFilter(region, terms) {
  const name = String(region ?? "").toLowerCase();
  // A leading "^" anchors a term to the start of the name: "prop:" also
  // matches "setup prop: X", "^prop:" does not.
  return terms.some((term) =>
    term.startsWith("^") ? name.startsWith(term.slice(1)) : name.includes(term),
  );
}

// Pick the rows a chart should draw: everything when the filter is empty,
// otherwise exactly what the terms match - an empty result stays empty rather
// than silently showing something else.
export function filterRegionRows(rows, selection, getRegion = (row) => row.region) {
  if (!rows) return rows;
  const terms = parseRegionFilter(selection?.text);
  if (!terms.length) return rows;
  return rows.filter((row) => matchesRegionFilter(getRegion(row), terms));
}

// Shown in place of an empty plot when a filter matches no region.
const EMPTY_FILTER_ANNOTATION = {
  text: "No regions match the filter.",
  showarrow: false,
  xref: "paper",
  yref: "paper",
  x: 0.5,
  y: 0.5,
};

function emptyStateLayout(layout, isEmpty) {
  if (!isEmpty) return layout;
  return {
    ...layout,
    annotations: [{ ...EMPTY_FILTER_ANNOTATION, font: { color: themeColors().muted, size: 13 } }],
  };
}

// Compare: grouped bar chart with exactly two series (case A vs case B),
// one bar pair per region. Used by the compare page to show a chosen metric
// side by side for two different case instances/runs.
export function buildComparisonFigure(regions, labelA, valuesA, labelB, valuesB, metric, regionSelection) {
  // The three arrays are parallel, so filter them by a shared index list.
  const keep = filterRegionRows(
    regions.map((region, index) => ({ region, index })),
    regionSelection,
  ).map((entry) => entry.index);
  regions = keep.map((index) => regions[index]);
  valuesA = keep.map((index) => valuesA[index]);
  valuesB = keep.map((index) => valuesB[index]);

  const colors = assignColors([labelA, labelB]);

  const data = [
    {
      type: "bar",
      name: labelA,
      x: regions,
      y: valuesA,
      marker: { color: colors.get(labelA) },
      hovertemplate: "<b>%{x}</b><br>" + labelA.replace(/[&<>]/g, "") + ": %{y:.4f}s<extra></extra>",
    },
    {
      type: "bar",
      name: labelB,
      x: regions,
      y: valuesB,
      marker: { color: colors.get(labelB) },
      hovertemplate: "<b>%{x}</b><br>" + labelB.replace(/[&<>]/g, "") + ": %{y:.4f}s<extra></extra>",
    },
  ];

  const layout = baseLayout({
    height: Math.max(360, 42 * regions.length + 180),
    margin: { l: 160, r: 24, t: 16, b: 140 },
    barmode: "group",
    showlegend: true,
    legend: { orientation: "h", y: 1.08, x: 0, font: { color: themeColors().muted, size: 11 } },
    xaxis: axisStyle({ tickangle: -35 }),
    yaxis: axisStyle({ title: { text: METRIC_LABELS[metric] ?? "Duration (s)" } }),
  });

  return { data, layout: emptyStateLayout(layout, regions.length === 0) };
}

export function buildWeakScalingEfficiencyFigure(points, regionSelection) {
  points = filterRegionRows(points, regionSelection);

  const regions = [];
  for (const point of points) {
    if (!regions.includes(point.region)) regions.push(point.region);
  }
  const colors = assignColors(regions);
  const rankCounts = [...new Set(points.map((point) => point.num_ranks))].sort((a, b) => a - b);

  const data = regions.map((region) => {
    const rows = points
      .filter((point) => point.region === region)
      .sort((a, b) => a.num_ranks - b.num_ranks);
    return {
      type: "scatter",
      mode: "lines+markers",
      name: region,
      x: rows.map((row) => row.num_ranks),
      y: rows.map((row) => row.efficiency * 100),
      customdata: rows.map((row) => [
        row.duration_seconds,
        row.baseline_duration_seconds,
        row.cells_per_rank,
        row.total_cells,
      ]),
      line: { color: colors.get(region), width: 2 },
      marker: { color: colors.get(region), size: 8 },
      hovertemplate:
        "<b>%{x} ranks</b><br>" +
        region.replace(/[&<>]/g, "") +
        ": %{y:.1f}%<br>duration: %{customdata[0]:.4f}s<br>baseline: %{customdata[1]:.4f}s<br>cells/rank: %{customdata[2]:.0f}<br>total cells: %{customdata[3]:.0f}<extra></extra>",
    };
  });

  data.push({
    type: "scatter",
    mode: "lines",
    name: "Ideal efficiency",
    x: rankCounts,
    y: rankCounts.map(() => 100),
    line: { color: themeColors().muted, width: 1.5, dash: "dash" },
    hoverinfo: "skip",
  });

  const layout = baseLayout({
    height: 420,
    margin: { l: 72, r: 24, t: 16, b: 48 },
    xaxis: axisStyle({ title: { text: "MPI ranks" }, tickvals: rankCounts }),
    yaxis: axisStyle({ title: { text: "Efficiency (%)" }, rangemode: "tozero" }),
  });

  return { data, layout: emptyStateLayout(layout, regions.length === 0) };
}

// Track rendered figures so they can be re-themed when the user toggles dark
// mode. Each container remembers its last render spec (including the current
// metric), so a refresh re-renders with the latest state under the new theme.
const themedFigures = new Set();
let lastPlotly = null;

export async function renderFigure(Plotly, container, kind, payload, extra) {
  if (!container) return;
  lastPlotly = Plotly;
  container.__figureSpec = { kind, payload, extra };
  themedFigures.add(container);

  // How each chart picks its regions: a comma-separated filter, empty for all.
  const regionSelection = { text: extra?.regionFilter ?? "" };

  let figure;
  // Flame always shows every region: it is a call hierarchy, where dropping
  // regions would orphan their children.
  const filterTerms = parseRegionFilter(regionSelection.text);
  // The package deliberately knows nothing about this site's filter syntax.
  // Do not supply a predicate for an empty box: empty means every region.
  const packageOptions = filterTerms.length
    ? { filterRegion: (region) => matchesRegionFilter(region, filterTerms) }
    : {};
  if (kind === "gantt") figure = buildScopeGanttFigure(payload, packageOptions);
  else if (kind === "flame") figure = buildScopeFlameFigure(payload, packageOptions);
  else if (kind === "durations")
    figure = buildScopeDurationsFigure(payload, { ...packageOptions, metric: extra?.metric ?? "total" });
  else if (kind === "speedup") figure = buildScopeSpeedupFigure(payload, packageOptions);
  else if (kind === "weak_scaling_efficiency")
    figure = buildWeakScalingEfficiencyFigure(payload.points, regionSelection);
  else if (kind === "comparison")
    figure = buildComparisonFigure(
      payload.regions,
      payload.labelA,
      payload.valuesA,
      payload.labelB,
      payload.valuesB,
      extra?.metric,
      regionSelection,
    );
  else throw new Error(`Unknown chart kind: ${kind}`);
  await renderScopeFigure(Plotly, container, figure, plotConfig);
}

// Re-render every known figure using the current theme colors.
export function refreshThemedFigures() {
  if (!lastPlotly) return;
  for (const container of themedFigures) {
    if (!container.isConnected) {
      themedFigures.delete(container);
      continue;
    }
    const spec = container.__figureSpec;
    if (spec) renderFigure(lastPlotly, container, spec.kind, spec.payload, spec.extra);
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("themechanged", refreshThemedFigures);
}
