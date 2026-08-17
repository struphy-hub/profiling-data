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
