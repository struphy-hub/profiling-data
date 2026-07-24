// Shared Plotly chart builders for the profiling figures pages.
//
// Colors come from the validated 8-slot categorical palette (light mode) -
// see the dataviz skill's references/palette.md. Assigned in fixed order to
// the first 8 distinct series names encountered; anything past that folds to
// a neutral muted gray rather than repeating a hue.
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
const OVERFLOW_COLOR = "#898781";

const TEXT_COLOR = "#111827";
const MUTED_COLOR = "#6b7280";
const GRID_COLOR = "#e1e0d9";
const FONT_FAMILY =
  "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif";

export function assignColors(names) {
  const colors = new Map();
  let slot = 0;
  for (const name of names) {
    if (colors.has(name)) continue;
    colors.set(name, slot < CATEGORICAL_PALETTE.length ? CATEGORICAL_PALETTE[slot] : OVERFLOW_COLOR);
    slot += 1;
  }
  return colors;
}

const baseLayout = (overrides = {}) => ({
  font: { family: FONT_FAMILY, color: TEXT_COLOR, size: 12 },
  paper_bgcolor: "transparent",
  plot_bgcolor: "transparent",
  margin: { l: 160, r: 24, t: 16, b: 48 },
  hoverlabel: { bgcolor: "#ffffff", bordercolor: GRID_COLOR, font: { color: TEXT_COLOR } },
  legend: { orientation: "h", y: -0.18, font: { color: MUTED_COLOR, size: 11 } },
  ...overrides,
});

const axisStyle = (overrides = {}) => ({
  gridcolor: GRID_COLOR,
  zerolinecolor: GRID_COLOR,
  linecolor: GRID_COLOR,
  tickfont: { color: MUTED_COLOR, size: 11 },
  title: { font: { color: MUTED_COLOR, size: 12 } },
  ...overrides,
});

const plotConfig = { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

async function render(Plotly, container, data, layout) {
  await Plotly.newPlot(container, data, layout, plotConfig);
}

// Gantt: one horizontal-bar trace per region so the legend gets one entry
// per region and every call for that region shares its color.
export function buildGanttFigure(intervals) {
  const order = [];
  for (const interval of intervals) {
    if (!order.includes(interval.region)) order.push(interval.region);
  }

  // Sort regions by their first start time
  const regionFirstStartMap = new Map();
  for (const interval of intervals) {
    if (interval.region) {
      const existing = regionFirstStartMap.get(interval.region);
      if (existing === undefined || interval.start_seconds < existing) {
        regionFirstStartMap.set(interval.region, interval.start_seconds);
      }
    }
  }
  order.sort((a, b) => (regionFirstStartMap.get(a) ?? 0) - (regionFirstStartMap.get(b) ?? 0));

  const colors = assignColors(order);

  const data = order.map((region) => {
    const rows = intervals.filter((interval) => interval.region === region);
    return {
      type: "bar",
      orientation: "h",
      name: region,
      y: rows.map(() => region),
      x: rows.map((row) => row.end_seconds - row.start_seconds),
      base: rows.map((row) => row.start_seconds),
      marker: { color: colors.get(region) },
      hovertemplate:
        "<b>%{y}</b><br>start: %{base:.4f}s<br>duration: %{x:.4f}s<extra></extra>",
    };
  });

  const layout = baseLayout({
    height: Math.max(260, 60 * order.length + 140),
    margin: { l: 160, r: 24, t: 16, b: 110 },
    barmode: "overlay",
    showlegend: order.length > 1,
    legend: { orientation: "h", y: -0.3, font: { color: MUTED_COLOR, size: 11 } },
    xaxis: axisStyle({ title: { text: "Time (s)", standoff: 12 } }),
    yaxis: axisStyle({ autorange: "reversed", categoryorder: "array", categoryarray: order }),
  });

  return { data, layout };
}

// Flame: icicle chart showing call hierarchy by region and depth
export function buildFlameFigure(calls) {
  const regions = [];
  for (const call of calls) {
    if (!regions.includes(call.region)) regions.push(call.region);
  }
  const colors = assignColors(regions);

  const labels = ["All"];
  const parents = [""];
  const values = [calls.reduce((sum, call) => sum + (call.end_seconds - call.start_seconds), 0)];
  const markerColors = [OVERFLOW_COLOR];
  const hoverTexts = ["All calls"];

  // First level: regions
  for (const region of regions) {
    const regionCalls = calls.filter((c) => c.region === region);
    const regionTotal = regionCalls.reduce((sum, c) => sum + (c.end_seconds - c.start_seconds), 0);
    labels.push(region);
    parents.push("All");
    values.push(regionTotal);
    markerColors.push(colors.get(region));
    hoverTexts.push(`<b>${region}</b><br>total: ${regionTotal.toFixed(4)}s`);
  }

  // Second level: individual calls
  const sortedCalls = [...calls].sort((a, b) => a.start_seconds - b.start_seconds);
  for (let i = 0; i < sortedCalls.length; i++) {
    const call = sortedCalls[i];
    const duration = call.end_seconds - call.start_seconds;
    labels.push(`${i}`);
    parents.push(call.region);
    values.push(duration);
    markerColors.push(colors.get(call.region));
    hoverTexts.push(
      `<b>${call.region}</b><br>depth: ${call.depth}<br>duration: ${duration.toFixed(4)}s`
    );
  }

  const data = [
    {
      type: "icicle",
      labels: labels,
      parents: parents,
      values: values,
      marker: { colors: markerColors, line: { color: GRID_COLOR, width: 1 } },
      textfont: { color: TEXT_COLOR, size: 11 },
      hovertext: hoverTexts,
      hoverinfo: "text",
    },
  ];

  const layout = baseLayout({
    height: 500,
    margin: { l: 24, r: 24, t: 16, b: 24 },
  });

  return { data, layout };
}

const METRIC_LABELS = {
  avg: "Average duration per call (s)",
  min: "Minimum duration per call (s)",
  max: "Maximum duration per call (s)",
  total: "Total duration (s)",
};

export function buildDurationsFigure(bars, metric) {
  // Check if data includes per-rank information
  const hasRankData = bars.some((bar) => bar.rank !== undefined);

  let groupBy, colors;
  if (hasRankData) {
    // Group by rank
    const ranks = [];
    for (const bar of bars) {
      if (!ranks.includes(bar.rank)) ranks.push(bar.rank);
    }
    ranks.sort((a, b) => a - b);
    groupBy = ranks;
    colors = assignColors(ranks.map((r) => `Rank ${r}`));
  } else {
    // Group by file (original behavior)
    const files = [];
    for (const bar of bars) {
      if (!files.includes(bar.file)) files.push(bar.file);
    }
    groupBy = files;
    colors = assignColors(files);
  }

  const regions = [];
  for (const bar of bars) {
    if (!regions.includes(bar.region)) regions.push(bar.region);
  }

  // Extract MPI rank count from filename and sort regions by rank count
  const extractRankCount = (filename) => {
    const match = filename.match(/ranks(\d+)/);
    return match ? parseInt(match[1], 10) : 0;
  };
  const fileRankMap = new Map(
    [...new Set(bars.map((b) => b.file))]
      .map((file) => [file, extractRankCount(file)])
  );
  const regionRankMap = new Map();
  for (const bar of bars) {
    if (bar.region && bar.file) {
      const rankCount = fileRankMap.get(bar.file);
      if (rankCount !== undefined) {
        const existing = regionRankMap.get(bar.region);
        if (existing === undefined || rankCount < existing) {
          regionRankMap.set(bar.region, rankCount);
        }
      }
    }
  }
  regions.sort((a, b) => (regionRankMap.get(a) ?? 0) - (regionRankMap.get(b) ?? 0));

  const data = groupBy.map((group) => {
    let rows;
    if (hasRankData) {
      rows = bars.filter((bar) => bar.rank === group && bar.metric === metric);
    } else {
      rows = bars.filter((bar) => bar.file === group && bar.metric === metric);
    }
    const byRegion = new Map(rows.map((row) => [row.region, row.value_seconds]));
    const label = hasRankData ? `Rank ${group}` : group;
    return {
      type: "bar",
      name: label,
      x: regions,
      y: regions.map((region) => byRegion.get(region) ?? null),
      marker: { color: colors.get(label) },
      hovertemplate: "<b>%{x}</b><br>" + label.replace(/[&<>]/g, "") + ": %{y:.4f}s<extra></extra>",
    };
  });

  const layout = baseLayout({
    height: Math.max(360, 42 * regions.length + 180),
    margin: { l: 160, r: 24, t: groupBy.length > 1 ? 56 : 16, b: 140 },
    barmode: "group",
    showlegend: groupBy.length > 1,
    legend: { orientation: "h", y: 1.12, x: 0, font: { color: MUTED_COLOR, size: 11 } },
    xaxis: axisStyle({ tickangle: -35 }),
    yaxis: axisStyle({ title: { text: METRIC_LABELS[metric] ?? "Duration (s)" } }),
  });

  return { data, layout };
}

export function buildSpeedupFigure(points) {
  const regions = [];
  for (const point of points) {
    if (!regions.includes(point.region)) regions.push(point.region);
  }
  const colors = assignColors(regions);
  const rankCounts = [...new Set(points.map((point) => point.num_ranks))].sort((a, b) => a - b);
  const baseline = rankCounts[0] ?? 1;

  const data = regions.map((region) => {
    const rows = points
      .filter((point) => point.region === region)
      .sort((a, b) => a.num_ranks - b.num_ranks);
    return {
      type: "scatter",
      mode: "lines+markers",
      name: region,
      x: rows.map((row) => row.num_ranks),
      y: rows.map((row) => row.speedup),
      line: { color: colors.get(region), width: 2 },
      marker: { color: colors.get(region), size: 8 },
      hovertemplate: "<b>%{x} ranks</b><br>" + region.replace(/[&<>]/g, "") + ": %{y:.2f}x<extra></extra>",
    };
  });

  data.push({
    type: "scatter",
    mode: "lines",
    name: "Optimal speedup",
    x: rankCounts,
    y: rankCounts.map((count) => count / baseline),
    line: { color: MUTED_COLOR, width: 1.5, dash: "dash" },
    hoverinfo: "skip",
  });

  const layout = baseLayout({
    height: 420,
    margin: { l: 64, r: 24, t: 16, b: 48 },
    xaxis: axisStyle({ title: { text: "MPI ranks" }, tickvals: rankCounts }),
    yaxis: axisStyle({ title: { text: "Speedup" } }),
  });

  return { data, layout };
}

export async function renderFigure(Plotly, container, kind, payload, extra) {
  if (!container) return;
  let figure;
  if (kind === "gantt") figure = buildGanttFigure(payload.intervals);
  else if (kind === "flame") figure = buildFlameFigure(payload.calls);
  else if (kind === "durations") figure = buildDurationsFigure(payload.bars, extra?.metric ?? "total");
  else if (kind === "speedup") figure = buildSpeedupFigure(payload.points);
  else throw new Error(`Unknown chart kind: ${kind}`);
  await render(Plotly, container, figure.data, figure.layout);
}
