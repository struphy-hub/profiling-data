// The site owns navigation and controls; scope-profiler owns chart construction.
import { buildFigure, buildComparisonFigure } from "@scope-profiler/plotly";
import { createFigureRegistry, matchesRegionFilter, parseRegionFilter } from "@scope-profiler/plotly/dashboard";
export { matchesRegionFilter, parseRegionFilter };
export const DEFAULT_REGION_FILTER = "";
let registry;
function figures(Plotly) {
  if (!registry) {
    registry = createFigureRegistry(Plotly, {
      config: { responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] },
    });
    registry.watch();
  }
  return registry;
}
export function renderFigure(Plotly, container, kind, payload, options = {}) {
  return figures(Plotly).render(container, payload, {
    ...options,
    ...(kind === "comparison" ? { build: buildComparisonFigure } : { build: buildFigure, plot: kind }),
  });
}
export function forgetFigure(container) { registry?.forget(container); }
export function refreshThemedFigures() { return registry?.refresh(); }
