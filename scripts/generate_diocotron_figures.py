#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate scope-profiler figures from all diocotron profiling folders."
    )
    parser.add_argument(
        "--pattern",
        default="*-diocotron*",
        help="Glob pattern used to select profiling directories in the repository root.",
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


def resolve_case_metadata_path(case_dir: Path) -> Path:
    preferred = case_dir / "case_metadata.json"
    legacy = case_dir / "metadata.json"
    if preferred.exists():
        return preferred
    if legacy.exists():
        return legacy
    raise SystemExit(f"Missing case metadata in {case_dir}: expected case_metadata.json")


def extract_title(metadata: dict, case_dir: Path) -> str:
    return str(
        metadata.get("label")
        or metadata.get("name")
        or metadata.get("profiling_case_info", {}).get("test_case_name")
        or metadata.get("general_information", {}).get("test_case_name")
        or case_dir.name
    )


def extract_description(metadata: dict) -> str:
    return str(
        metadata.get("description")
        or metadata.get("profiling_case_info", {}).get("test_case_description")
        or metadata.get("general_information", {}).get("test_case_description")
        or ""
    )


def extract_case_details(metadata: dict) -> dict:
    profiling_info = metadata.get("profiling_case_info", {})
    general_info = metadata.get("general_information", {})
    hardware_info = metadata.get("hardware_information", {})
    return {
        "datetime_utc": str(
            metadata.get("datetime_utc")
            or general_info.get("time_date_utc")
            or ""
        ),
        "struphy_model_used": str(
            profiling_info.get("struphy_model_used")
            or general_info.get("struphy_model_used")
            or ""
        ),
        "physics_problem": str(
            profiling_info.get("physics_problem")
            or general_info.get("physics_problem")
            or ""
        ),
        "cluster_name": str(hardware_info.get("cluster_name") or ""),
    }


def extract_case_metadata_summary(metadata: dict) -> dict:
    general_info = metadata.get("general_information", {})
    hardware_info = metadata.get("hardware_information", {})
    software_info = metadata.get("software_information", {})
    profiling_info = metadata.get("profiling_case_info", {})
    return {
        "datetime_utc": metadata.get("datetime_utc") or general_info.get("time_date_utc") or "",
        "datetime_token": metadata.get("datetime_token") or "",
        "commit": metadata.get("commit") or "",
        "commit_short": metadata.get("commit_short") or "",
        "testcase": metadata.get("testcase") or "",
        "language": metadata.get("language") or "",
        "source_results_root": metadata.get("source_results_root") or "",
        "source_parameters_file": metadata.get("source_parameters_file") or "",
        "cluster_name": hardware_info.get("cluster_name") or "",
        "struphy_model_used": profiling_info.get("struphy_model_used")
        or general_info.get("struphy_model_used")
        or "",
        "physics_problem": profiling_info.get("physics_problem")
        or general_info.get("physics_problem")
        or "",
        "test_case_identifier": profiling_info.get("test_case_identifier") or "",
        "test_case_name": profiling_info.get("test_case_name")
        or general_info.get("test_case_name")
        or "",
        "test_case_description": profiling_info.get("test_case_description")
        or general_info.get("test_case_description")
        or "",
        "pyccel_language": profiling_info.get("pyccel_language")
        or software_info.get("pyccel_language")
        or "",
        "pyccel_compiler_family": profiling_info.get("pyccel_compiler_family")
        or software_info.get("pyccel_compiler_family")
        or "",
        "struphy_commit": software_info.get("struphy_commit") or "",
        "slurm_script": general_info.get("slurm_script")
        or profiling_info.get("slurm_script")
        or "",
        "slurm_variables": general_info.get("slurm_variables")
        or profiling_info.get("slurm_variables")
        or {},
        "github": metadata.get("github") or {},
    }


def load_case_metadata(
    case_dir: Path,
) -> tuple[str, str, dict, dict, dict, Path]:
    metadata_path = resolve_case_metadata_path(case_dir)

    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    title = extract_title(metadata, case_dir)
    description = extract_description(metadata)
    files = metadata.get("files", [])
    if not isinstance(files, list):
        raise SystemExit(f"Invalid 'files' section in {metadata_path}")

    file_metadata_by_destination = {}
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        destination = file_entry.get("destination")
        if destination:
            file_metadata_by_destination[str(destination)] = file_entry

    case_details = extract_case_details(metadata)
    case_metadata_summary = extract_case_metadata_summary(metadata)
    return (
        title,
        description,
        file_metadata_by_destination,
        case_details,
        case_metadata_summary,
        metadata_path,
    )


def run_scope_profiler(
    pproc_executable: str,
    h5_files: list[Path],
    output_dir: Path,
    dry_run: bool,
) -> None:
    command = [
        pproc_executable,
        "pproc",
        *(str(file) for file in h5_files),
        "--ranks",
        "0",
        "-o",
        str(output_dir),
    ]

    if dry_run:
        print("Dry run command:")
        print(" ".join(command))
        return

    subprocess.run(command, check=True)


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return slug or "run"


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
        "plots": {
            "durations": f"cases/{folder_name}/durations_plot_total.png",
            "speedup": f"cases/{folder_name}/speedup_plot.png",
            "gantt": f"cases/{folder_name}/gantt_plot.png",
        },
    }


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = (repo_root / args.output).resolve()

    diocotron_dirs = sorted(
        directory
        for directory in repo_root.glob(args.pattern)
        if directory.is_dir() and "diocotron" in directory.name
    )
    if not diocotron_dirs:
        raise SystemExit(f"No .h5 files found in folders matching '{args.pattern}'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    cases_output_dir = output_dir / "cases"
    cases_output_dir.mkdir(parents=True, exist_ok=True)
    local_pproc = repo_root / ".venv" / "bin" / "scope-profiler pproc"
    pproc_executable = shutil.which("scope-profiler")
    if pproc_executable is None and local_pproc.exists():
        pproc_executable = str(local_pproc)
    if pproc_executable is None:
        raise SystemExit(
            "scope-profiler was not found in PATH or .venv/bin. Install requirements first."
        )

    aggregated_files: list[dict] = []
    case_summaries: list[dict] = []
    total_files = 0

    for case_dir in diocotron_dirs:
        h5_files = sorted(case_dir.glob("*.h5"))
        if not h5_files:
            continue
        total_files += len(h5_files)
        (
            title,
            description,
            file_metadata_by_destination,
            case_details,
            case_metadata_summary,
            metadata_path,
        ) = load_case_metadata(case_dir)

        case_output_dir = cases_output_dir / case_dir.name
        case_output_dir.mkdir(parents=True, exist_ok=True)
        run_scope_profiler(pproc_executable, h5_files, case_output_dir, args.dry_run)
        if args.dry_run:
            continue

        case_stats_path = case_output_dir / "region_statistics.json"
        case_stats = load_region_stats(case_stats_path)
        case_summaries.append(
            build_case_summary(
                case_dir.name,
                title,
                description,
                case_stats,
                str(metadata_path.relative_to(repo_root)),
                case_details,
                case_metadata_summary,
            )
        )

        for entry in case_stats["files"]:
            entry["title"] = title
            entry["description"] = description
            entry["case_id"] = case_dir.name
            file_name = Path(str(entry.get("file_path", ""))).name
            file_metadata = file_metadata_by_destination.get(file_name)
            if file_metadata is not None:
                entry["file_metadata"] = file_metadata
            run_label = str(entry.get("label") or file_name)
            run_id = slugify(run_label)
            run_output_dir = case_output_dir / "runs" / run_id
            run_output_dir.mkdir(parents=True, exist_ok=True)
            for stale_file in (
                "durations_plot_total.png",
                "speedup_plot.png",
                "gantt_plot.png",
                "region_statistics.json",
            ):
                stale_path = run_output_dir / stale_file
                if stale_path.exists():
                    stale_path.unlink()
            run_scope_profiler(
                pproc_executable,
                [Path(str(entry["file_path"]))],
                run_output_dir,
                args.dry_run,
            )
            run_outputs = {"id": run_id}
            output_files = {
                "durations": "durations_plot_total.png",
                "speedup": "speedup_plot.png",
                "gantt": "gantt_plot.png",
                "region_statistics": "region_statistics.json",
            }
            for key, file_name in output_files.items():
                if (run_output_dir / file_name).exists():
                    run_outputs[key] = f"cases/{case_dir.name}/runs/{run_id}/{file_name}"
            entry["run_outputs"] = run_outputs
            aggregated_files.append(entry)

    if total_files == 0:
        raise SystemExit(f"No .h5 files found in folders matching '{args.pattern}'.")

    print(f"Selected {total_files} files from {len(diocotron_dirs)} diocotron folders.")
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
