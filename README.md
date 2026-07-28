# profiling-data
Repo for storing profiling data

## Docs build and publish

The docs publish workflow is in `.github/workflows/publish-astro-docs.yml`.

Docs are always built from the Astro project in `docs/` and deployed to GitHub Pages from the Actions artifact (no commit/push back to the repository).

- CI uses Node.js 22 for docs dependency/install compatibility.
- Install dependencies: `npm ci` (in `docs/`)
- Build docs: `npm run build --if-present` (in `docs/`)

Figures are always generated from profiling data into `docs/public/figures` before the docs build.

The generation command is automated via:

`python scripts/generate_diocotron_figures.py`

It scans all diocotron profiling directories in the repository root and includes every `.h5` case automatically.
Case `title` and `description` fields in `docs/public/figures/region_statistics.json` are injected from each folder's `case_metadata.json` (with `metadata.json` accepted for backward compatibility).
Each profiling directory is processed independently, so plots are generated per-case under `docs/public/figures/cases/<case-id>/` and are never merged across different folders.

## Flame graphs

Each run page shows a flame graph.

All run-level figures — durations, gantt, flame and the flame graph — are generated for **rank 0 only** for now.

- `scope-profiler pproc --export-prof` writes `profile_rank0.prof`.
- [flameprof](https://pypi.org/project/flameprof/) renders it to `flamegraph_rank0.svg`. It is invoked as `python -m flameprof` because the console script it installs has an unusable shebang.
- flameprof emits a fixed 1200px-wide SVG; the generation script rewrites the header to carry a `viewBox` instead, so the page can scale it to the card width.
- The run page inlines the SVG rather than using it as an `<img>` source, so the per-frame hover tooltips (percentage, call count, tottime/cumtime) work.

The `.prof` file is also published, and is downloadable from the run page for use with any pstats viewer.

Everything under `docs/public/figures/cases/` is wiped and regenerated on each run, so artifacts from cases that no longer exist are not published.
