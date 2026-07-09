# profiling-data
Repo for storing profiling data

## Docs build and publish

The docs publish workflow is in `.github/workflows/publish-astro-docs.yml`.

- If `docs/package.json` exists, docs are treated as a Node/Astro project and the workflow runs:
  - `npm ci` (in `docs/`)
  - `npm run build --if-present` (in `docs/`)
- If `docs/package.json` does not exist, the workflow publishes the existing static `docs/` content directly.

Figures are always generated from profiling data and copied into `docs/figures` (and `docs/public/figures` when applicable).
