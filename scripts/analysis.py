"""Export report-style analysis using the pinned scope-profiler public APIs.

The manifest describes charts; the browser uses the upstream Plotly builders.
Keeping payloads in separate files lets collapsed panels load on demand.
"""

from __future__ import annotations

import json
import math
from numbers import Real
from pathlib import Path

from scope_profiler import EventDataUnavailableError, NestingError, build_call_arrays
from scope_profiler.plotting_scripts import (
    available_likwid_metrics,
    collect_region_statistics,
    plot_duration_histogram,
    plot_duration_timeseries,
    plot_durations,
    plot_flame,
    plot_gantt,
    plot_imbalance,
    plot_likwid,
    plot_rank_heatmap,
    plot_scaling_efficiency,
    plot_speedup,
    plot_weak_scaling_efficiency,
)

METRICS = ["avg", "min", "max", "total", "first", "last"]
AXES = {
    "num_ranks": "MPI ranks",
    "num_nodes": "Nodes",
    "omp_num_threads": "OpenMP threads",
    "total_cores": "MPI ranks × OpenMP threads",
}


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n")


def region_index(readers, files) -> list[dict]:
    """Use the report's shallowest-call depth and largest per-run total."""
    index = {}
    for file in files:
        for name, stats in file["region_statistics"].items():
            entry = index.setdefault(name, {"name": name, "depth": None, "total": 0})
            entry["total"] = max(entry["total"], stats["total_duration_seconds"])
    for reader in readers:
        try:
            calls = build_call_arrays(reader.get_regions(), 0)
        except (NestingError, EventDataUnavailableError):
            continue
        for region, depth in zip(calls.region_index.tolist(), calls.depth.tolist()):
            entry = index.get(calls.names[region])
            if entry is not None:
                entry["depth"] = depth if entry["depth"] is None else min(entry["depth"], depth)
    return sorted(index.values(), key=lambda entry: (-entry["total"], entry["name"]))


def scaling_axes(readers) -> list[str]:
    """Only offer recorded axes with at least two distinct positive values."""
    values = {field: [] for field in AXES}
    for reader in readers:
        threads = reader.metadata.get("omp_num_threads")
        values["num_ranks"].append(reader.num_ranks)
        values["num_nodes"].append(reader.num_nodes)
        values["omp_num_threads"].append(threads)
        values["total_cores"].append(reader.num_ranks * threads if threads else None)
    return [
        field for field, counts in values.items()
        if all(isinstance(value, Real) and math.isfinite(value) and value > 0 for value in counts)
        and len(set(counts)) > 1
    ]


def write_analysis(readers, output_dir: Path, figures_root: Path, case_files=None) -> str:
    """Write one run or case manifest and its native plot-data documents.

    All-rank statistics are separate from the legacy rank-0 exports. Timelines
    remain rank 0, explicitly named; rank diagnostics use every recorded rank.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    statistics = collect_region_statistics(readers)
    files = statistics["files"]
    regions = region_index(readers, files)
    charts, unavailable = [], []

    def relative(path):
        return path.relative_to(figures_root).as_posix()

    def export(identifier, title, kind, plotter, description, *, options=None, opened=False, **kwargs):
        path = output_dir / f"analysis-{identifier}.json"
        try:
            # The report uses this backend to export data without constructing
            # a discarded Python figure. Browser rendering comes from the same pin.
            plotter(
                readers, data_filepath=path, data_format="json",
                backend="data-only", show=False, verbose=False, **kwargs,
            )
        except (ValueError, EventDataUnavailableError, NestingError) as error:
            unavailable.append({"title": title, "reason": str(error)})
            return None
        chart = {
            "id": identifier, "title": title, "kind": kind,
            "description": description, "path": relative(path),
            "options": options or {}, "open": opened,
        }
        charts.append(chart)
        return chart

    comparison = len(readers) > 1
    default_metric = "avg" if len({reader.num_ranks for reader in readers}) > 1 else "total"
    if not comparison:
        export(
            "stacked-durations", "Where time is spent", "durations", plot_durations,
            "Each bar splits a region into its own time and its direct children. Summed over all ranks.",
            stack_children=True, metrics=["total", "avg"], sort_by="total",
            options={"metric": "total"}, opened=True,
        )
    export(
        "durations", "Region durations", "durations", plot_durations,
        "Compare region timings across runs. Totals sum all ranks; average is per call across all ranks.",
        metrics=METRICS, sort_by="total", options={"metric": default_metric}, opened=comparison,
    )

    if len(readers) == 2:
        path = output_dir / "analysis-comparison.json"
        write_json(path, {"plot": "region_statistics", **statistics})
        charts.append({
            "id": "change", "title": "Change from baseline", "kind": "comparison",
            "description": f"Positive change means {files[1]['label']} is slower than {files[0]['label']}. Missing measurements remain gaps.",
            "path": relative(path), "open": True,
            "options": {"comparison": "percent", "metric": default_metric, "commonRegionsOnly": False},
        })

    if comparison:
        axes = scaling_axes(readers)
        # Grid metadata comes from Struphy, not from the profiler. Require it
        # for every run before presenting either efficiency as a scaling study.
        work = [file.get("weak_scaling", {}) for file in (case_files or [])]
        complete = len(work) == len(readers) and all(item.get("total_cells", 0) > 0 for item in work)
        fixed_problem = complete and len({item["total_cells"] for item in work}) == 1
        backends = {file.get("run_parameters", {}).get("backend") for file in (case_files or [])}
        comparable_backends = len(backends) <= 1
        weak_work = [item.get("cells_per_rank", 0) for item in work]
        weak_problem = complete and weak_work[0] > 0 and all(
            math.isclose(value, weak_work[0], rel_tol=1e-9) for value in weak_work
        )
        specs = [(
            "speedup", "Speedup", plot_speedup,
            "Mean-call speedup relative to the smallest scale. Interpret as strong scaling only for fixed work and comparable hardware.", {},
        )]
        if fixed_problem and comparable_backends:
            specs.append((
                "scaling_efficiency", "Strong-scaling efficiency", plot_scaling_efficiency,
                "Speedup divided by the increase in parallel resources. Grid size is fixed; ideal efficiency is 1 for comparable hardware.", {},
            ))
        if weak_problem and comparable_backends:
            specs.append((
                "weak_scaling_efficiency", "Weak-scaling efficiency", plot_weak_scaling_efficiency,
                "Baseline mean-call time divided by current mean-call time. Grid cells per MPI rank are constant; ideal efficiency is 1.",
                {"work_per_rank": weak_work},
            ))
        if not axes:
            unavailable.append({"title": "Scaling", "reason": "No recorded rank, node, thread, or core count varies across these runs. Use the timing comparison instead."})
        for kind, title, plotter, description, kwargs in specs:
            variants = []
            primary = None
            # Work per MPI rank does not establish work per node/thread/core.
            fields = ["num_ranks"] if kind == "weak_scaling_efficiency" else axes
            for field in fields:
                if field not in axes:
                    continue
                chart = export(
                    f"{kind}-{field}", title, kind, plotter, description,
                    x_field=field, opened=kind == "speedup", **kwargs,
                )
                if chart is None:
                    continue
                variants.append({"label": AXES[field], "path": chart["path"]})
                if primary is None:
                    primary = chart
                else:
                    charts.remove(chart)
            if primary and len(variants) > 1:
                primary["variants"] = variants
    else:
        export("timeline", "Timeline · rank 0", "gantt", plot_gantt,
               "Individual calls in time order on rank 0. Depth limits also hide deeper calls.",
               ranks=[0], options={"layout": {"showlegend": False}}, opened=True)
        export("flame", "Flame chart · rank 0", "flame", plot_flame,
               "Nested call durations on rank 0.", ranks=[0])
        export("timeseries", "Duration over time · rank 0", "timeseries", plot_duration_timeseries,
               "Follow per-call durations through the run; the band shows minimum and maximum values.",
               ranks=[0], options={"variability": "band"})
        export("histogram", "Duration distribution · rank 0", "histogram", plot_duration_histogram,
               "Call-duration distributions reveal warmup and unusually slow calls.", ranks=[0], bins=30)
        if readers[0].num_ranks > 1:
            export("imbalance", "Rank imbalance", "imbalance", plot_imbalance,
                   "Total region time on every rank, compared with the mean over ranks.", metric="total")
            export("heatmap", "Rank heatmap · own time", "rank_heatmap", plot_rank_heatmap,
                   "Own (exclusive) region time on every rank, excluding nested calls.", exclusive=True,
                   options={"valueKey": "exclusive_duration_seconds"})

    for index, metric in enumerate(available_likwid_metrics(readers)):
        export(f"likwid-{index}", f"LIKWID: {metric}", "likwid", plot_likwid,
               "Recorded hardware counter metric, across all ranks.", metric=metric)

    manifest = {
        "files": files, "regions": regions, "charts": charts, "unavailable": unavailable,
        "summary": [{"label": reader.display_label, "ranks": reader.num_ranks,
                     "nodes": reader.num_nodes, "wall_time_seconds": reader.time_span} for reader in readers],
    }
    path = output_dir / "analysis.json"
    write_json(path, manifest)
    return relative(path)
