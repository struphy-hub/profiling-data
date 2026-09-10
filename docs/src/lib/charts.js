// Chart rendering for the profiling pages.
//
// The figures come from @scope-profiler/plotly, and the bookkeeping around
// them -- tracking which containers hold a figure so a theme toggle can
// rebuild them all, and the comma-separated filter syntax the boxes accept --
// comes from that package's /dashboard entry point. Both are resolved from
// the pinned scope-profiler submodule, i.e. the same commit that generated
// the JSON they draw.
//
// What is left here is only what is specific to this site: which regions the
// filter boxes start out prefilled with, and the names the pages call the
// figures by.
import {
  buildComparisonFigure,
  buildDurationsFigure,
  buildFlameFigure,
  buildGanttFigure,
  buildSpeedupFigure,
  buildWeakScalingEfficiencyFigure,
} from "@scope-profiler/plotly";
import {
  createFigureRegistry,
  matchesRegionFilter,
  parseRegionFilter,
} from "@scope-profiler/plotly/dashboard";

export { matchesRegionFilter, parseRegionFilter };

const plotConfig = {
  responsive: true,
  displaylogo: false,
  modeBarButtonsToRemove: ["lasso2d", "select2d"],
};

// What the region filter boxes are prefilled with: the top-level integration
// loop, the merged setup span, and the propagators. Kernels and other
// fine-grained regions appear once the viewer edits or clears the filter.
// "^prop:" is anchored so the propagators show up without also dragging in
// their "setup prop: X" counterparts; drop the ^ to include those too.
export const DEFAULT_REGION_FILTER = "model.integrate, ^prop:, setup: total";

// The kinds the pages ask for. Most are plot-data kinds the package would
// dispatch on by itself, but the pages name them explicitly because two of
// them -- "flame" over a flame_graph payload, and "comparison", which is a
// reading of region_statistics rather than a plot kind -- cannot be inferred
// from the document alone.
const BUILDERS = {
  gantt: buildGanttFigure,
  flame: buildFlameFigure,
  durations: buildDurationsFigure,
  speedup: buildSpeedupFigure,
  weak_scaling_efficiency: buildWeakScalingEfficiencyFigure,
  comparison: buildComparisonFigure,
};

// One registry for the page. It reads `data-theme` off the document element,
// which is what the header toggle stamps there, and rebuilds every live
// figure when the toggle dispatches "themechanged".
let registry = null;

function figures(Plotly) {
  if (!registry) {
    registry = createFigureRegistry(Plotly, { config: plotConfig });
    registry.watch();
  }
  return registry;
}

export async function renderFigure(Plotly, container, kind, payload, extra) {
  if (!container) return;
  const build = BUILDERS[kind];
  if (!build) throw new Error(`Unknown chart kind: ${kind}`);

  return figures(Plotly).render(container, payload, {
    build,
    // The flame chart is never filtered: it is a call hierarchy, and dropping
    // a region would orphan its children.
    ...(kind === "flame" ? {} : { regionFilter: extra?.regionFilter ?? "" }),
    ...(extra?.metric ? { metric: extra.metric } : {}),
    ...(extra?.files ? { files: extra.files } : {}),
  });
}

/** Rebuild every figure under the current theme. */
export function refreshThemedFigures() {
  return registry?.refresh();
}
