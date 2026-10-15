# profiling-data
Repo for storing profiling data

## The scope-profiler submodule

`vendor/scope-profiler` is a git submodule pinned to one commit. Everything
that draws a chart comes from it: `scripts/generate_figures.py` imports the
Python library to write the plot-data JSON, and `docs/package.json` resolves
`@scope-profiler/plotly` to `vendor/scope-profiler/packages/plotly` for the
browser code that renders it. Both halves are therefore always the same
commit -- before this, the Python side tracked the `devel` branch and the
browser side an npm release, so the two could drift apart between builds.

The current pin is scope-profiler 0.7.4, commit
[`910cf78`](https://github.com/max-models/scope-profiler/commit/910cf78adcefe451c2a7288c5e98d212c348f183)
from `devel` (updated 2026-10-15), including `@scope-profiler/plotly` 0.4.0.

Clone with it:

```bash
git clone --recurse-submodules <this repo>
# or, in an existing clone
git submodule update --init
```

Move it to a newer scope-profiler:

```bash
git -C vendor/scope-profiler fetch
git -C vendor/scope-profiler checkout <sha-or-branch>
git add vendor/scope-profiler        # records the new commit
(cd docs && npm install)             # refresh the lockfile if the JS changed
```

## Scope-profiler reports

The standalone report is already a useful profiling overview:

```bash
scope-profiler report files*.h5
```

Its interactive charts and shared filtering controls are now integrated into
the case, run, and comparison pages. The website uses scope-profiler's public
Python exporters and Plotly builders, with a shared analysis viewer in
`docs/src/lib/analysis.js`; navigation, simulation parameters, and galleries
remain specific to this repository. Consult the
[CLI documentation](https://max-models.github.io/scope-profiler/cli.html)
when generating reports or adapting their features for the dashboard.

The shared toolbar filters native charts and timing tables by region name,
top regions by total time, and nesting depth. It also selects the timing
metric. Charts load when expanded and support light/dark themes and full screen.

- Case pages show grouped durations, signed changes for two-run cases, and
  speedup with selectable MPI rank, node, thread, or core axes where recorded
  counts differ. Strong-scaling efficiency requires a fixed grid; weak-scaling
  efficiency requires fixed grid cells per rank. Efficiency is omitted when
  runs use different compute backends, such as NumPy versus CuPy.
- Run pages add stacked own/child duration bars, timeline, flame chart,
  duration timeseries and distributions, rank imbalance, and an exclusive-time
  rank heatmap. Available LIKWID metrics are also exported.
- Compare pages let any two runs be compared side by side or by absolute or
  percentage change. The delta table uses the selected metric; missing data
  and percentages with a zero baseline stay unavailable.

`scripts/analysis.py` writes an `analysis.json` manifest per case and run,
alongside lazily loaded chart payloads. Statistics, duration charts, and rank
diagnostics use **all recorded ranks**. Call timelines, flame views, timeseries,
and distributions use **rank 0**, as their titles indicate. The standalone
SVG flame graph and speedscope viewer retain their own controls.

Verify the exporter with `python -m unittest discover -s scripts -p 'test_*.py'`,
then generate figures and build the docs as described below.

## Docs build and publish

The docs publish workflow is in `.github/workflows/publish-astro-docs.yml`.

Docs are always built from the Astro project in `docs/` and deployed to GitHub Pages from the Actions artifact (no commit/push back to the repository).

- CI uses Node.js 22 for docs dependency/install compatibility.
- Install dependencies: `npm ci` (in `docs/`). This needs the submodule to be
  checked out, since the chart package is resolved from inside it.
- Build docs: `npm run build --if-present` (in `docs/`)

Figures are always generated from profiling data into `docs/public/figures` before the docs build.

The generation command is automated via:

`python scripts/generate_figures.py`

It scans every profiling case directory in the repository root — any folder holding a `case_metadata.json` next to `.h5` runs, regardless of test case (diocotron, poisson, …) — and includes every `.h5` file automatically.
Pass `--pattern` to restrict the run to a subset, e.g. `--pattern '*-poisson_*'`.
Case `title` and `description` fields in `docs/public/figures/region_statistics.json` are injected from each folder's `case_metadata.json` (with `metadata.json` accepted for backward compatibility).
Each profiling directory is processed independently, so plots are generated per-case under `docs/public/figures/cases/<case-id>/` and are never merged across different folders.

## Flame graphs

Each run page shows a flame graph.

The SVG flame graph and `.prof` download are generated for **rank 0**.

- `scope_profiler.export_prof()` writes `profile_rank0.prof`.
- `scope_profiler.export_flamegraph_svg()` writes `flamegraph_rank0.svg`. It renders the same reconstructed call tree through [flameprof](https://pypi.org/project/flameprof/) (part of the `pproc` extra), and carries a `viewBox` rather than flameprof's fixed 1200px width, so the page can scale it to the card.
- The run page inlines the SVG rather than using it as an `<img>` source, so the per-frame hover tooltips (percentage, call count, tottime/cumtime) work.

The `.prof` file is also published, and is downloadable from the run page for use with any pstats viewer.

## Timeline (speedscope)

Each run page also embeds [speedscope](https://www.speedscope.app), which shows every individual call rather than the aggregate the flame graph is built from — so it offers Time Order, Left Heavy and Sandwich views.

- `scope_profiler.export_speedscope()` writes `profile.speedscope.json`.
- speedscope is a self-contained static web app with no server component. `docs/scripts/copy-speedscope.mjs` copies its release build out of `node_modules` into `docs/public/speedscope/`, run automatically from the `predev`/`prebuild` npm hooks (it has to happen after `npm ci`, so the Python figure generation cannot do it).
- The run page embeds it in an iframe and points it at the profile with speedscope's `#profileURL=` hash parameter.

`docs/public/speedscope/` is generated and git-ignored, like `docs/public/figures/`.

Everything under `docs/public/figures/cases/` is wiped and regenerated on each run, so artifacts from cases that no longer exist are not published.
