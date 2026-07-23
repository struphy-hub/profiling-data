// Shared helpers for grouping profiling-case instances (individual run
// folders, e.g. one per submission) under a single logical case (e.g. the
// "diocotron_poisson_scaling" test case), and for keeping URLs consistent
// across the overview / instances / case-explorer / run pages.

export function groupKeyForCase(entry) {
  const meta = entry?.case_metadata ?? {};
  return String(
    meta.testcase || meta.test_case_identifier || entry?.title || entry?.id || "unknown",
  );
}

export function groupLabelForCase(entry) {
  return String(entry?.title || groupKeyForCase(entry));
}

export function groupCases(cases) {
  const groups = new Map();
  for (const entry of cases) {
    const key = groupKeyForCase(entry);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(entry);
  }
  return groups;
}
