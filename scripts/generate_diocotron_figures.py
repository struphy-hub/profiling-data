#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import shutil
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


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = (repo_root / args.output).resolve()

    diocotron_dirs = sorted(
        directory
        for directory in repo_root.glob(args.pattern)
        if directory.is_dir() and "diocotron" in directory.name
    )
    h5_files = sorted(file for directory in diocotron_dirs for file in directory.glob("*.h5"))

    if not h5_files:
        raise SystemExit(f"No .h5 files found in folders matching '{args.pattern}'.")

    output_dir.mkdir(parents=True, exist_ok=True)
    local_pproc = repo_root / ".venv" / "bin" / "scope-profiler-pproc"
    pproc_executable = shutil.which("scope-profiler-pproc")
    if pproc_executable is None and local_pproc.exists():
        pproc_executable = str(local_pproc)
    if pproc_executable is None:
        raise SystemExit(
            "scope-profiler-pproc was not found in PATH or .venv/bin. Install requirements first."
        )

    command = [
        pproc_executable,
        *(str(file) for file in h5_files),
        "--ranks",
        "0",
        "-o",
        str(output_dir),
    ]

    print(f"Selected {len(h5_files)} files from {len(diocotron_dirs)} diocotron folders.")
    if args.dry_run:
        print("Dry run command:")
        print(" ".join(command))
        return 0

    subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
