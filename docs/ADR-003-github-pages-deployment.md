# ADR-003: GitHub Pages Deployment & Scholar Sync Pipeline

**Date:** 2026-08-01
**Status:** Accepted

## Context

The HydroclimateX Lab site is a plain static site served from the repo root
(`index.html`, `main.js`, `style.css`, `assets/`, `showcase/`). Requirements:

1. **Commit + push → deploy.** Any change on `main` must publish to GitHub Pages
   automatically, without manual intervention.
2. **Fresh Google Scholar metrics.** The facts bar and publication list should
   stay in sync with the public Scholar profile (`4iVouPYAAAAJ`).
3. **Clean published artifact.** Only the files the web page actually needs
   should be served — not the backend, WASP-devel, or CI internals.

## Decision

### Deployment: GitHub Pages via GitHub Actions

- Pages source is set to **GitHub Actions** (Settings → Pages → Source).
- `.github/workflows/static.yml` runs on **push to `main`**, a **daily schedule**
  (cron `0 3 * * *`), and **manual dispatch**.
- Push runs deploy fast and deterministically: they stage the web root and
  publish. They do **not** touch the network (no Scholar call), so a routine
  edit never depends on Google Scholar availability.

### Scholar sync: committed data files

- `scripts/sync_scholar.py` (Python stdlib only, no pip deps) fetches the public
  Scholar profile, parses citation metrics and the full publication list, and
  writes `data/scholar-stats.json` + `data/scholar-publications.json`.
- Those JSON files are **committed to `main`** and served with the site.
- On the daily schedule (and manual dispatch), the workflow:

  1. Re-runs the sync (`continue-on-error: true`);
  2. If Scholar is unavailable, logs a warning and **keeps the last verified
     data** (the script only writes on success);
  3. Commits and pushes any change to `data/` back to `main`.

- The commit happens **only** on schedule/dispatch runs, never on push runs —
  this prevents an "edit → deploy → commit → push → deploy" loop.
- `updatedAt` is date-only (`YYYY-MM-DD`), so a no-change run produces identical
  files and the commit step correctly reports "No Scholar changes".

### Site reads the committed data

- `main.js` fetches `data/scholar-stats.json` (metrics) and
  `data/scholar-publications.json` (publications) from the same origin.
- Publications are grouped into the research-direction filters using
  title/venue keywords; untagged papers appear under "All".
- A curated `FALLBACK_PUBLICATIONS` list and the hard-coded metrics in
  `index.html` remain as offline/first-load fallbacks.
- External data is HTML-escaped before injection.

### Published artifact: web root only

- A build step stages `index.html main.js style.css assets showcase data` into
  `_site/`, which is uploaded and deployed. `backend/`, `WASP-devel/`,
  `deploy.sh`, `docs/`, etc. are never published.

## Rationale

- **Actions mode** makes deployment explicit and scriptable, and lets us run the
  daily refresh in the same pipeline that deploys.
- **Committed JSON** keeps the site fully static (no server-side rendering) and
  lets Pages serve real data with zero runtime dependencies.
- **Date-only `updatedAt`** bounds repo churn to actual metric changes, so the
  daily run doesn't generate a commit every night.
- **Web-root artifact** matches ADR-001's "flat structure" decision and keeps the
  published surface minimal.

## Consequences

- The one-time prerequisite: Pages source must be **GitHub Actions**, not
  "Deploy from a branch".
- Scholar scraping is inherently fragile (captcha, markup changes). Failures are
  tolerated; the site always serves the last verified snapshot.
- The publication list now reflects the **full** Scholar profile (49 papers),
  not just the curated hydrology subset — the site intro already promises
  "the full list synced daily from Google Scholar".
- A push triggers a single deploy. The daily schedule run also refreshes and
  commits data, but a commit pushed with `GITHUB_TOKEN` does not re-trigger the
  workflow — so at most one scheduled deploy per day plus any pushes.
