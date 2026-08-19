#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from scope_profiler import (
    ProfilingResults,
    export_prof,
    export_speedscope,
    plot_duration_timeseries,
    plot_durations,
    plot_flame,
    plot_gantt,
    plot_speedup,
    read_h5,
    write_region_statistics_json,
)

# scope-profiler's durations bar plot only exports "total" by default; the
# durations page lets visitors switch between avg/min/max/total, so all four
# need to be present in the export.
DURATIONS_METRICS = ["avg", "min", "max", "total"]

# Duration of a region's first and last call: how much of a region's cost is
# one-off warmup (JIT, allocation, first GPU transfer) versus steady state.
# scope-profiler does not export these yet, so they are derived from the call
# timings in the gantt export - see add_first_last_metrics.
FIRST_LAST_METRICS = ["first", "last"]
FIRST_LAST_STAT_FIELDS = {
    "first": "first_duration_seconds",
    "last": "last_duration_seconds",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate scope-profiler figures from every profiling case folder."
    )
    parser.add_argument(
        "--pattern",
        default="*",
        help=(
            "Glob pattern used to select profiling directories in the repository root. "
            "The default selects every case folder; pass e.g. '*-poisson_*' to restrict "
            "the run to a single test case."
        ),
    )
    parser.add_argument(
        "--output",
        default="docs/public/figures",
        help="Output directory passed to scope-profiler pproc.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command and selected files without running scope-profiler pproc.",
    )
    return parser.parse_args()


def find_case_metadata_path(case_dir: Path) -> Path | None:
    preferred = case_dir / "case_metadata.json"
    legacy = case_dir / "metadata.json"
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    return None


def resolve_case_metadata_path(case_dir: Path) -> Path:
    metadata_path = find_case_metadata_path(case_dir)
    if metadata_path is None:
        raise SystemExit(f"Missing case metadata in {case_dir}: expected case_metadata.json")
    return metadata_path


def is_case_dir(path: Path) -> bool:
    """A profiling case folder holds a case metadata file next to its .h5 runs.

    Test cases are not distinguished by name here: diocotron, poisson and any
    future case are all picked up by the same layout check. The .h5 files
    themselves may live directly in the case folder or nested under a
    results-runNN subfolder, so this looks recursively rather than assuming
    either layout.
    """
    return path.is_dir() and find_case_metadata_path(path) is not None and any(path.rglob("*.h5"))


def extract_title(metadata: dict, case_dir: Path) -> str:
    general_info = metadata.get("general_information", {})
    return str(
        general_info.get("test_case_name")
        or general_info.get("simulation_name")
        or case_dir.name
    )


def extract_description(metadata: dict) -> str:
    general_info = metadata.get("general_information", {})
    return str(
        general_info.get("test_case_description")
        or general_info.get("simulation_description")
        or ""
    )


def extract_case_details(metadata: dict) -> dict:
    general_info = metadata.get("general_information", {})
    hardware_info = metadata.get("hardware_information", {})
    return {
        "datetime_utc": str(general_info.get("time_date_utc") or ""),
        "struphy_model_used": str(general_info.get("struphy_model_used") or ""),
        "physics_problem": str(general_info.get("physics_problem") or ""),
        "cluster_name": str(hardware_info.get("cluster_name") or ""),
    }


def extract_case_metadata_summary(metadata: dict) -> dict:
    general_info = metadata.get("general_information", {})
    hardware_info = metadata.get("hardware_information", {})
    software_info = metadata.get("software_information", {})
    struphy_commit = str(software_info.get("struphy_commit") or "")
    return {
        "datetime_utc": general_info.get("time_date_utc") or "",
        "datetime_token": general_info.get("datetime_token") or "",
        "commit": struphy_commit,
        "commit_short": struphy_commit[:8],
        "testcase": general_info.get("test_case_identifier") or "",
        "language": software_info.get("pyccel_language") or "",
        "source_results_root": general_info.get("results_root") or "",
        "source_parameters_file": software_info.get("parameter_file_source") or "",
        "cluster_name": hardware_info.get("cluster_name") or "",
        "cluster_hostnames": hardware_info.get("node_hostnames") or [],
        "struphy_model_used": general_info.get("struphy_model_used") or "",
        "physics_problem": general_info.get("physics_problem") or "",
        "test_case_identifier": general_info.get("test_case_identifier") or "",
        "test_case_name": general_info.get("test_case_name") or "",
        "test_case_description": general_info.get("test_case_description") or "",
        "pyccel_language": software_info.get("pyccel_language") or "",
        "pyccel_compiler_family": software_info.get("pyccel_compiler_family") or "",
        "struphy_commit": struphy_commit,
    }


def index_by_key(entries: object, key: str) -> dict:
    """Index a metadata list (e.g. case_metadata.json's `runs[]`) by one of its own fields."""
    if not isinstance(entries, list):
        return {}
    return {
        entry[key]: entry
        for entry in entries
        if isinstance(entry, dict) and entry.get(key) is not None
    }


def load_case_metadata(case_dir: Path) -> tuple[str, str, dict, dict, dict, Path]:
    metadata_path = resolve_case_metadata_path(case_dir)

    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    title = extract_title(metadata, case_dir)
    description = extract_description(metadata)

    # case_metadata.json's `runs[]` is just an index: for each launch, where
    # to find that run's own metadata file. That file (loaded below, per
    # run) is the actual source of truth for everything about the run - its
    # .h5 file(s), result/gallery files, job, and simulation parameters.
    runs_by_launch_id = index_by_key(metadata.get("runs"), "launch_id")

    case_details = extract_case_details(metadata)
    case_metadata_summary = extract_case_metadata_summary(metadata)
    return (
        title,
        description,
        runs_by_launch_id,
        case_details,
        case_metadata_summary,
        metadata_path,
    )


def load_run_metadata(case_dir: Path, run_entry: dict) -> dict | None:
    """Load a run's own metadata JSON (e.g. results-run01/run01.json).

    Its `packaged_files` section gives the run's profiling .h5 file(s) and
    result/gallery files as paths relative to case_dir, and its `job` section
    carries the Slurm submission for that specific launch - all relative to
    this one file, regardless of which folder layout produced it.
    """
    run_metadata_rel = run_entry.get("run_metadata")
    if not run_metadata_rel:
        return None
    run_metadata_path = case_dir / run_metadata_rel
    if not run_metadata_path.is_file():
        return None
    with run_metadata_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def resolve_run_h5_files(case_dir: Path, run_parameters: dict) -> list[Path]:
    packaged_files = run_parameters.get("packaged_files") or {}
    relative_paths = []
    if packaged_files.get("profiling_data"):
        relative_paths.append(packaged_files["profiling_data"])
    relative_paths.extend(packaged_files.get("additional_profiling_data") or [])
    return [path for path in (case_dir / rel for rel in relative_paths) if path.is_file()]


def resolve_gallery_files(case_dir: Path, run_parameters: dict) -> list[Path]:
    packaged_files = run_parameters.get("packaged_files") or {}
    candidates = (case_dir / rel for rel in packaged_files.get("results") or [])
    return [path for path in candidates if path.suffix.lower() in GALLERY_EXTENSIONS and path.is_file()]


def get_reader(
    h5_file: Path, reader_cache: dict[str, ProfilingResults | None]
) -> ProfilingResults | None:
    """Load (and cache) the scope-profiler reader for one HDF5 file.

    Case-level and run-level post-processing both read some of the same
    files (a run's own file is one of its case's files too), so caching
    avoids parsing the same HDF5 file twice.

    Returns None for a file that cannot be read (truncated or otherwise
    corrupt HDF5, e.g. a run that died mid-write). The failure is cached too,
    so a broken file is reported once and then skipped everywhere.
    """
    key = str(h5_file.resolve())
    if key in reader_cache:
        return reader_cache[key]
    try:
        reader = read_h5(h5_file)
    except Exception as error:  # h5py raises OSError, readers may raise others
        print(f"Skipping unreadable profiling file {h5_file}: {error}")
        reader = None
    reader_cache[key] = reader
    return reader


def run_pproc(
    h5_files: list[Path],
    reader_cache: dict[str, ProfilingResults | None],
    output_dir: Path,
    dry_run: bool,
    ranks: list[int] | None = None,
    export_prof_files: bool = False,
    export_speedscope_files: bool = False,
) -> None:
    """Post-process a set of HDF5 profiling files, in place of shelling out
    to `scope-profiler pproc`: read them, then write the same JSON exports
    (region_statistics.json, durations/gantt/flame/timeseries/speedup data,
    plus optional .prof and speedscope files) via scope-profiler's Python
    API directly.
    """
    if dry_run:
        print(f"Dry run: would post-process {[str(f) for f in h5_files]} into {output_dir}")
        return

    ranks = [0] if ranks is None else ranks
    readers = [reader for h5_file in h5_files if (reader := get_reader(h5_file, reader_cache))]
    if not readers:
        print(f"No readable profiling files for {output_dir}, skipping post-processing.")
        return
    output_dir.mkdir(parents=True, exist_ok=True)

    if export_prof_files:
        export_prof(readers, output_dir / "profile.prof", ranks=ranks)
    if export_speedscope_files:
        export_speedscope(readers, output_dir / "profile.speedscope.json", ranks=ranks)

    plot_gantt(readers, ranks=ranks, data_filepath=output_dir / "gantt_data.json", data_format="json")
    plot_flame(readers, ranks=ranks, data_filepath=output_dir / "flame_data.json", data_format="json")
    plot_durations(
        readers,
        ranks=ranks,
        metrics=DURATIONS_METRICS,
        data_filepath=output_dir / "durations_data.json",
        data_format="json",
    )
    plot_duration_timeseries(
        readers,
        ranks=ranks,
        data_filepath=output_dir / "duration_timeseries_data.json",
        data_format="json",
    )
    if len(readers) > 1:
        plot_speedup(readers, ranks=ranks, data_filepath=output_dir / "speedup_data.json", data_format="json")

    write_region_statistics_json(readers, output_dir / "region_statistics.json", ranks=ranks)

    # Order matters: the setup region picks up whatever metrics exist by then.
    add_first_last_metrics(output_dir)
    add_setup_total(output_dir)


def load_gantt_intervals(output_dir: Path) -> list[dict]:
    """The gantt export is the only one carrying per-call start/end times."""
    gantt_path = output_dir / "gantt_data.json"
    if not gantt_path.is_file():
        return []
    with gantt_path.open("r", encoding="utf-8") as file:
        return json.load(file).get("intervals") or []


def add_first_last_metrics(output_dir: Path) -> None:
    """Add `first` and `last` duration metrics to the exports in output_dir.

    They are the duration of a region's first and last call, per file and
    rank, taken from the gantt intervals. If a future scope-profiler release
    exports them itself, whatever is already in the payload is left alone.
    """
    durations_path = output_dir / "durations_data.json"
    intervals = load_gantt_intervals(output_dir)
    if not intervals or not durations_path.is_file():
        return

    # (file, rank, region) -> [(start, duration), ...]
    calls: dict[tuple, list[tuple[float, float]]] = {}
    for interval in intervals:
        key = (interval.get("file"), interval.get("rank"), interval.get("region"))
        start = float(interval["start_seconds"])
        calls.setdefault(key, []).append((start, float(interval["end_seconds"]) - start))

    values: dict[str, dict[tuple, float]] = {"first": {}, "last": {}}
    for key, spans in calls.items():
        spans.sort()
        values["first"][key] = spans[0][1]
        values["last"][key] = spans[-1][1]

    with durations_path.open("r", encoding="utf-8") as file:
        durations = json.load(file)
    bars = durations.get("bars") or []
    metrics_present = {bar.get("metric") for bar in bars}
    missing = [metric for metric in FIRST_LAST_METRICS if metric not in metrics_present]
    if not bars or not missing:
        return

    has_rank = any("rank" in bar for bar in bars)
    extra = []
    for metric in missing:
        for (file_label, rank, region), value in values[metric].items():
            bar = {"file": file_label, "region": region, "metric": metric, "value_seconds": value}
            if has_rank:
                bar["rank"] = rank
            elif rank not in (None, 0):
                # Without a per-rank axis, only rank 0 is plotted (as elsewhere).
                continue
            extra.append(bar)
    durations["bars"] = bars + extra
    with durations_path.open("w", encoding="utf-8") as file:
        json.dump(durations, file, indent=1)

    stats_path = output_dir / "region_statistics.json"
    if not stats_path.is_file():
        return
    with stats_path.open("r", encoding="utf-8") as file:
        stats = json.load(file)
    for entry in stats.get("files") or []:
        for region, record in (entry.get("region_statistics") or {}).items():
            for metric in missing:
                per_rank = {
                    str(rank): value
                    for (file_label, rank, name), value in values[metric].items()
                    if file_label == entry.get("label") and name == region
                }
                if not per_rank:
                    continue
                field = FIRST_LAST_STAT_FIELDS[metric]
                record[field] = per_rank.get("0", next(iter(per_rank.values())))
                for rank_key, rank_record in (record.get("per_rank") or {}).items():
                    if rank_key in per_rank:
                        rank_record[field] = per_rank[rank_key]
    with stats_path.open("w", encoding="utf-8") as file:
        json.dump(stats, file, indent=1)


SETUP_TOTAL_REGION = "setup: total"


def is_setup_region(region: str) -> bool:
    """Setup regions are named `setup: x`, `setup prop: x` or `setup var: x`."""
    return region.startswith("setup:") or region.startswith("setup ")


def merged_duration(intervals: list[tuple[float, float]]) -> float:
    """Total wall time covered by a set of (start, end) intervals.

    Setup regions nest (`setup: feec` runs inside `setup: allocate`, and so
    on), so their durations cannot simply be summed - overlapping intervals
    are merged first and only the union is counted.
    """
    total = 0.0
    current_start, current_end = None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        total += current_end - current_start
    return total


def add_setup_total(output_dir: Path) -> None:
    """Add a synthetic `setup: total` region to the exports in output_dir.

    Setup is split across many small regions, so the combined cost of getting
    a run to its first time step is not visible in any single bar. This walks
    the gantt intervals (the only export carrying start/end times), merges
    every setup region per file and rank, and writes the result back into
    durations_data.json and region_statistics.json as one extra region.
    """
    durations_path = output_dir / "durations_data.json"
    intervals = load_gantt_intervals(output_dir)
    if not intervals:
        return

    # (file label, rank) -> merged setup wall time
    by_file_rank: dict[tuple[str, int], list[tuple[float, float]]] = {}
    for interval in intervals:
        if not is_setup_region(str(interval.get("region", ""))):
            continue
        key = (interval.get("file"), interval.get("rank"))
        by_file_rank.setdefault(key, []).append(
            (float(interval["start_seconds"]), float(interval["end_seconds"]))
        )
    setup_totals = {key: merged_duration(spans) for key, spans in by_file_rank.items()}
    if not setup_totals:
        return

    # Per file: the longest rank, i.e. how long setup held the whole run up.
    by_file: dict[str, float] = {}
    for (file_label, _rank), value in setup_totals.items():
        by_file[file_label] = max(by_file.get(file_label, 0.0), value)

    if durations_path.is_file():
        with durations_path.open("r", encoding="utf-8") as file:
            durations = json.load(file)
        bars = durations.get("bars") or []
        # Mirror the shape of the existing bars: some exports carry a per-rank
        # breakdown, others are per file only.
        has_rank = any("rank" in bar for bar in bars)
        metrics = list(dict.fromkeys(bar["metric"] for bar in bars)) or DURATIONS_METRICS
        extra = []
        for metric in metrics:
            if has_rank:
                for (file_label, rank), value in sorted(setup_totals.items(), key=lambda kv: str(kv[0])):
                    extra.append(
                        {
                            "file": file_label,
                            "rank": rank,
                            "region": SETUP_TOTAL_REGION,
                            "metric": metric,
                            "value_seconds": value,
                        }
                    )
            else:
                for file_label, value in sorted(by_file.items()):
                    extra.append(
                        {
                            "file": file_label,
                            "region": SETUP_TOTAL_REGION,
                            "metric": metric,
                            "value_seconds": value,
                        }
                    )
        # Regenerating is idempotent, but guard against a stale export.
        durations["bars"] = [bar for bar in bars if bar.get("region") != SETUP_TOTAL_REGION] + extra
        with durations_path.open("w", encoding="utf-8") as file:
            json.dump(durations, file, indent=1)

    stats_path = output_dir / "region_statistics.json"
    if not stats_path.is_file():
        return
    with stats_path.open("r", encoding="utf-8") as file:
        stats = json.load(file)

    for entry in stats.get("files") or []:
        value = by_file.get(entry.get("label"))
        if value is None:
            continue
        per_rank = {
            str(rank): summarize_setup_total(total)
            for (file_label, rank), total in setup_totals.items()
            if file_label == entry.get("label")
        }
        entry.setdefault("region_statistics", {})[SETUP_TOTAL_REGION] = {
            **summarize_setup_total(value),
            "per_rank": per_rank,
        }

    common = stats.get("common_regions")
    if isinstance(common, list) and SETUP_TOTAL_REGION not in common:
        # Only "common" when every file actually has setup timings.
        if all(
            SETUP_TOTAL_REGION in (entry.get("region_statistics") or {})
            for entry in stats.get("files") or []
        ):
            common.append(SETUP_TOTAL_REGION)

    with stats_path.open("w", encoding="utf-8") as file:
        json.dump(stats, file, indent=1)


def summarize_setup_total(value: float) -> dict:
    """Region-statistics record for the one merged setup span."""
    return {
        "count": 1,
        "average_duration_seconds": value,
        "min_duration_seconds": value,
        "max_duration_seconds": value,
        "std_duration_seconds": 0.0,
        "total_duration_seconds": value,
        # One merged span, so every metric is that same span.
        **{field: value for field in FIRST_LAST_STAT_FIELDS.values()},
    }


SVG_HEADER_PATTERN = re.compile(
    r'^.*?<svg version="1.1" width="(?P<width>[\d.]+)" height="(?P<height>[\d.]+)"',
    re.DOTALL,
)


def render_flamegraph(prof_path: Path, output_path: Path) -> bool:
    """Render a .prof file to an SVG flame graph with flameprof.

    The console script shipped by flameprof has an unusable shebang, so it is
    invoked as a module. The SVG it emits is a fixed 1200px wide document; the
    header is rewritten to carry a viewBox instead so the page can scale it.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "flameprof", str(prof_path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, OSError) as error:
        print(f"Could not render a flame graph for {prof_path}: {error}")
        return False

    svg = result.stdout
    match = SVG_HEADER_PATTERN.match(svg)
    if match is None:
        print(f"Unexpected flameprof SVG header for {prof_path}; leaving it as-is.")
    else:
        # Drop the XML prolog and DOCTYPE too: the SVG is inlined into the run
        # page, where only the <svg> element itself is meaningful.
        svg = (
            f'<svg version="1.1" viewBox="0 0 {match["width"]} {match["height"]}"'
            + svg[match.end() :]
        )

    with output_path.open("w", encoding="utf-8") as file:
        file.write(svg)
    return True


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "run"


GALLERY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf"}


def copy_gallery_files(gallery_files: list[Path], run_output_dir: Path) -> list[str]:
    """Copy a run's simulation-produced plots into its output folder and return their names."""
    if not gallery_files:
        return []
    gallery_dir = run_output_dir / "gallery"
    gallery_dir.mkdir(parents=True, exist_ok=True)
    for path in gallery_files:
        shutil.copy2(path, gallery_dir / path.name)
    return sorted(path.name for path in gallery_files)


def load_region_stats(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data.get("files"), list):
        raise SystemExit(f"Unexpected region statistics format in {path}")
    return data


def build_case_summary(
    folder_name: str,
    title: str,
    description: str,
    case_stats: dict,
    metadata_file: str,
    case_details: dict,
    case_metadata_summary: dict,
) -> dict:
    files = case_stats["files"]
    ranks = sorted(
        {
            int(entry["num_ranks"])
            for entry in files
            if isinstance(entry.get("num_ranks"), (int, float))
        }
    )
    return {
        "id": folder_name,
        "title": title,
        "description": description,
        "metadata_file": metadata_file,
        "datetime_utc": case_details["datetime_utc"],
        "struphy_model_used": case_details["struphy_model_used"],
        "physics_problem": case_details["physics_problem"],
        "cluster_name": case_details["cluster_name"],
        "case_metadata": case_metadata_summary,
        "runs": len(files),
        "ranks": ranks,
        "common_regions": case_stats.get("common_regions", []),
        "plot_data": {
            "durations": f"cases/{folder_name}/durations_data.json",
            "speedup": f"cases/{folder_name}/speedup_data.json",
            "gantt": f"cases/{folder_name}/gantt_data.json",
            "flame": f"cases/{folder_name}/flame_data.json",
        },
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = (repo_root / args.output).resolve()

    case_dirs = sorted(
        directory for directory in repo_root.glob(args.pattern) if is_case_dir(directory)
    )
    if not case_dirs:
        raise SystemExit(f"No profiling case folders with .h5 files match '{args.pattern}'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_output_dir = output_dir / "cases"
    # Everything under cases/ is regenerated below. Clearing it first keeps
    # artifacts from cases (or layouts) that no longer exist out of the site.
    if not args.dry_run and cases_output_dir.exists():
        shutil.rmtree(cases_output_dir)
    cases_output_dir.mkdir(parents=True, exist_ok=True)

    aggregated_files: list[dict] = []
    case_summaries: list[dict] = []
    total_files = 0
    reader_cache: dict[str, ProfilingResults | None] = {}

    for case_dir in case_dirs:
        (
            title,
            description,
            runs_by_launch_id,
            case_details,
            case_metadata_summary,
            metadata_path,
        ) = load_case_metadata(case_dir)

        # case_metadata.json only indexes launches; each run's own metadata
        # file is the source of truth for its .h5 file(s), so it's loaded and
        # resolved up front. Launches whose metadata or .h5 is missing on
        # disk (e.g. a partially synced case) are skipped rather than
        # aborting the whole case.
        resolved_runs = []
        for launch_id, run_entry in sorted(runs_by_launch_id.items()):
            run_parameters = load_run_metadata(case_dir, run_entry)
            if run_parameters is None:
                print(f"{case_dir.name}: skipping launch {launch_id}, no run metadata file found.")
                continue
            run_h5_files = resolve_run_h5_files(case_dir, run_parameters)
            if not run_h5_files:
                print(f"{case_dir.name}: skipping launch {launch_id}, no profiling .h5 file found.")
                continue
            # Drop files that cannot be parsed (broken runs) up front, so they
            # never reach the plotting calls.
            run_h5_files = [
                path for path in run_h5_files if get_reader(path, reader_cache) is not None
            ]
            if not run_h5_files:
                print(f"{case_dir.name}: skipping launch {launch_id}, profiling .h5 file unreadable.")
                continue
            resolved_runs.append(
                {
                    "launch_id": launch_id,
                    "run_entry": run_entry,
                    "run_parameters": run_parameters,
                    "h5_files": run_h5_files,
                }
            )

        if not resolved_runs:
            print(f"{case_dir.name}: no usable runs found, skipping case.")
            continue

        h5_files = sorted({path for run in resolved_runs for path in run["h5_files"]})
        run_by_resolved_h5 = {
            str(path.resolve()): run for run in resolved_runs for path in run["h5_files"]
        }
        total_files += len(h5_files)

        case_output_dir = cases_output_dir / case_dir.name
        case_output_dir.mkdir(parents=True, exist_ok=True)
        run_pproc(h5_files, reader_cache, case_output_dir, args.dry_run)
        if args.dry_run:
            continue

        case_stats_path = case_output_dir / "region_statistics.json"
        case_stats = load_region_stats(case_stats_path)
        case_summary = build_case_summary(
            case_dir.name,
            title,
            description,
            case_stats,
            str(metadata_path.relative_to(repo_root)),
            case_details,
            case_metadata_summary,
        )
        case_summaries.append(case_summary)

        # The shared parameters.py that every launch was submitted with lives
        # once at the case root, not per run.
        parameters_source = case_dir / "parameters.py"
        if parameters_source.is_file():
            shutil.copy2(parameters_source, case_output_dir / "parameters.py")
            case_summary["parameters_file"] = f"cases/{case_dir.name}/parameters.py"

        first_run_launch_id: int | None = None

        for entry in case_stats["files"]:
            entry["title"] = title
            entry["description"] = description
            entry["case_id"] = case_dir.name
            entry_path = Path(str(entry.get("file_path", "")))
            resolved = run_by_resolved_h5.get(str(entry_path.resolve()))
            run_parameters = resolved["run_parameters"] if resolved else {}
            packaged_files = run_parameters.get("packaged_files") or {}

            if resolved is not None:
                entry["file_metadata"] = {
                    "launch_id": resolved["launch_id"],
                    "folder": resolved["run_entry"].get("folder"),
                    "relative_source": packaged_files.get("profiling_data"),
                    "run_directory": packaged_files.get("run_directory"),
                    "run_metadata_destination": resolved["run_entry"].get("run_metadata"),
                }

                job_info = run_parameters.get("job")
                if job_info:
                    entry["job_info"] = {
                        "ranks": job_info.get("ranks"),
                        "pragmas": job_info.get("pragmas", {}),
                    }

                # The parameter payload minus the job (surfaced separately
                # above) and packaged_files (internal bookkeeping, not
                # simulation config) - the rest is whatever the model put
                # there, rendered generically on the run page.
                entry["run_parameters"] = {
                    key: value
                    for key, value in run_parameters.items()
                    if key not in ("job", "packaged_files")
                }

            run_label = str(entry.get("label") or entry_path.name)
            run_id = slugify(run_label)
            run_output_dir = case_output_dir / "runs" / run_id
            run_output_dir.mkdir(parents=True, exist_ok=True)
            # Every run figure is rank 0 only for now.
            run_pproc(
                [Path(str(entry["file_path"]))],
                reader_cache,
                run_output_dir,
                args.dry_run,
                export_prof_files=True,
                export_speedscope_files=True,
            )

            run_outputs = {"id": run_id}
            output_files = {
                "durations": "durations_data.json",
                "speedup": "speedup_data.json",
                "gantt": "gantt_data.json",
                "flame": "flame_data.json",
                "region_statistics": "region_statistics.json",
                "speedscope": "profile.speedscope.json",
            }
            for key, file_name in output_files.items():
                if (run_output_dir / file_name).exists():
                    run_outputs[key] = f"cases/{case_dir.name}/runs/{run_id}/{file_name}"

            prof_path = run_output_dir / "profile_rank0.prof"
            svg_path = run_output_dir / "flamegraph_rank0.svg"
            if prof_path.exists() and render_flamegraph(prof_path, svg_path):
                run_outputs["flamegraph"] = f"cases/{case_dir.name}/runs/{run_id}/{svg_path.name}"
                run_outputs["profile"] = f"cases/{case_dir.name}/runs/{run_id}/{prof_path.name}"

            gallery_files = resolve_gallery_files(case_dir, run_parameters) if resolved else []
            gallery_names = copy_gallery_files(gallery_files, run_output_dir)
            if gallery_names:
                run_outputs["gallery"] = [
                    f"cases/{case_dir.name}/runs/{run_id}/gallery/{name}" for name in gallery_names
                ]

            # The case overview shows the first run's gallery (if any) as a
            # representative preview, so it's tracked here as runs are seen.
            if resolved is not None and run_outputs.get("gallery"):
                launch_id = resolved["launch_id"]
                if first_run_launch_id is None or launch_id < first_run_launch_id:
                    first_run_launch_id = launch_id
                    case_summary["gallery"] = run_outputs["gallery"]

            entry["run_outputs"] = run_outputs
            aggregated_files.append(entry)

    print(f"Selected {total_files} files from {len(case_dirs)} case folders.")
    if args.dry_run:
        return 0

    aggregated = {
        "cases": case_summaries,
        "files": aggregated_files,
    }
    with (output_dir / "region_statistics.json").open("w", encoding="utf-8") as file:
        json.dump(aggregated, file, indent=2)
        file.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
