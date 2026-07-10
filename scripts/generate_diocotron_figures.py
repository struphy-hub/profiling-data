#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
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
        help="Output directory passed to scope-profiler-pproc.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the command and selected files without running scope-profiler-pproc.",
    )
    return parser.parse_args()


def load_case_metadata(metadata_path: Path) -> tuple[str, str]:
    if not metadata_path.exists():
        raise SystemExit(f"Missing metadata file: {metadata_path}")

    with metadata_path.open("r", encoding="utf-8") as file:
        metadata = json.load(file)

    if "name" not in metadata or "description" not in metadata:
        raise SystemExit(
            f"Metadata file must contain both 'name' and 'description': {metadata_path}"
        )

    title = str(metadata["name"])
    description = str(metadata["description"])
    return title, description


def run_scope_profiler(
    pproc_executable: str,
    h5_files: list[Path],
    output_dir: Path,
    dry_run: bool,
) -> None:
    command = [
        pproc_executable,
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
        "runs": len(files),
        "ranks": ranks,
        "common_regions": case_stats.get("common_regions", []),
        "plots": {
            "durations": f"cases/{folder_name}/durations_plot.png",
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
    local_pproc = repo_root / ".venv" / "bin" / "scope-profiler-pproc"
    pproc_executable = shutil.which("scope-profiler-pproc")
    if pproc_executable is None and local_pproc.exists():
        pproc_executable = str(local_pproc)
    if pproc_executable is None:
        raise SystemExit(
            "scope-profiler-pproc was not found in PATH or .venv/bin. Install requirements first."
        )

    aggregated_files: list[dict] = []
    case_summaries: list[dict] = []
    total_files = 0

    for case_dir in diocotron_dirs:
        h5_files = sorted(case_dir.glob("*.h5"))
        if not h5_files:
            continue
        total_files += len(h5_files)
        title, description = load_case_metadata(case_dir / "metadata.json")

        case_output_dir = cases_output_dir / case_dir.name
        case_output_dir.mkdir(parents=True, exist_ok=True)
        run_scope_profiler(pproc_executable, h5_files, case_output_dir, args.dry_run)
        if args.dry_run:
            continue

        case_stats_path = case_output_dir / "region_statistics.json"
        case_stats = load_region_stats(case_stats_path)
        case_summaries.append(build_case_summary(case_dir.name, title, description, case_stats))

        for entry in case_stats["files"]:
            entry["title"] = title
            entry["description"] = description
            entry["case_id"] = case_dir.name
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
