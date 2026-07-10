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
