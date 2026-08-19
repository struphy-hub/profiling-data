// Region-name filtering for the statistics tables, using the same
// comma-separated syntax and defaults as the chart filter boxes: each term is
// a case-insensitive substring of the region name, "^" anchors a term to the
// start, and an empty box shows every region.
import { DEFAULT_REGION_FILTER, parseRegionFilter, matchesRegionFilter } from "./charts.js";

export { DEFAULT_REGION_FILTER };

// Markup for the input, so every table gets an identical control. `id` is
// needed because some tables are re-rendered from a template string.
export function regionFilterMarkup(id) {
  return `<input
    id="${id}"
    class="region-filter"
    type="search"
    placeholder="Filter regions, e.g. prop:, setup: total"
    title="Comma-separated, case-insensitive substring match. Prefix a term with ^ to anchor it to the start of the region name."
    aria-label="Filter regions"
    autocomplete="off"
    value="${DEFAULT_REGION_FILTER}"
  />`;
}

// Show only the rows whose `data-region` matches, with a placeholder row when
// nothing does. Rows without the attribute (e.g. an "empty" message) are left
// alone. Returns the apply function so callers can re-run it after a redraw.
export function applyRegionTableFilter(input, tbody, columnCount = 6) {
  if (!input || !tbody) return;
  {
    const terms = parseRegionFilter(input.value);
    let visible = 0;
    let filterable = 0;
    for (const row of tbody.querySelectorAll("tr[data-region]")) {
      filterable += 1;
      const match = !terms.length || matchesRegionFilter(row.dataset.region, terms);
      row.hidden = !match;
      if (match) visible += 1;
    }

    let emptyRow = tbody.querySelector("tr.region-empty-row");
    if (filterable && !visible) {
      if (!emptyRow) {
        emptyRow = document.createElement("tr");
        emptyRow.className = "region-empty-row";
        emptyRow.innerHTML = `<td colspan="${columnCount}" class="empty-state">No regions match the filter.</td>`;
        tbody.appendChild(emptyRow);
      }
    } else if (emptyRow) {
      emptyRow.remove();
    }
  }
}

// As above, plus a debounced listener so typing filters the table live. Use
// on tables whose filter input is recreated with them; for a long-lived input
// call applyRegionTableFilter after each redraw instead.
export function bindRegionTableFilter(input, tbody, columnCount = 6) {
  if (!input || !tbody) return;
  let timer = null;
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => applyRegionTableFilter(input, tbody, columnCount), 150);
  });
  applyRegionTableFilter(input, tbody, columnCount);
}
