# LISFLOOD Interactive Web Design

## Goal

Turn `lisflood.hydroclimatex.com` from a static five-scenario viewer into a public, English, on-demand LISFLOOD-FP flood-risk map. A user selects a rectangle and one return period, starts a simulation, waits for completion, and receives cached results. The first phase remains a research demonstration without SWMM, sewer networks, engineered drainage, login, uploads, animation, or emergency-use claims.

## Existing Components

- `lisflood-app` is a native HTML/CSS/JavaScript Leaflet page. It currently reads one static `/results/manifest.json` and renders cached PNG overlays. It remains the only frontend.
- `lisflood_runner` builds the official LISFLOOD-FP 8.0.3 CPU ACC executable and already implements the Nanjing design storm, parameter rewriting, mass-balance checks, hazard/risk classification, PNG rendering, statistics, and atomic publication. These functions are reused.
- Nginx already serves the LISFLOOD page and result volume. Docker Compose currently exposes the runner only as a one-shot tool; it will become the single long-running compute service.

## Architecture

No database, Redis, task framework, WebSocket, or frontend framework is added. The existing runner image hosts a small Python standard-library HTTP server, an in-memory FIFO queue with at most eight pending jobs, and one worker thread. The worker is the only process allowed to start LISFLOOD-FP, so the existing `2 vCPU / 2560 MB` limit also bounds all public computation. A model process is terminated and marked failed after `LISFLOOD_JOB_TIMEOUT_SECONDS`, which defaults to 7200 seconds.

Nginx serves the static application and immutable result files, proxies `/api/lisflood/` to `lisflood-runner`, and rate-limits only new job submissions. A container restart loses queued/running state but not completed cache entries; affected users can submit again.

## Spatial and Model Inputs

- The available selection extent is the complete rectangular Nanjing base-data extent, approximately `[[32.042871, 118.757673], [32.258495, 119.238814]]`. It is not clipped to an administrative polygon.
- The default selection is a fixed `577 x 577` cell window centred on that extent, approximately `[[32.074303, 118.904460], [32.227474, 119.091316]]`, or `299.6361 km²` at 30 m resolution.
- `LISFLOOD_MAX_AREA_KM2` controls the maximum selected area and defaults to `300`. The backend transforms WGS84 bounds to EPSG:32650, snaps them inward to the 30 m base grid, then validates containment and snapped area. The returned effective bounds replace the user's unsnapped rectangle.
- The image contains aligned, compressed, complete rectangular rasters for Copernicus GLO-30 elevation and WorldPop R2025A 2025 population counts. WorldPop is conservatively resampled to 30 m with population totals preserved. Source, retrieval date, processing command, checksum, DOI, and licence are shipped with the data.
- The runtime generates every parameter and auxiliary file required by the selected crop. The first-phase surface defaults are zero initial depth, closed boundaries, constant Manning roughness `0.06`, infiltration `0.00001`, the existing evaporation series, ACC, hazard output, 180-minute Nanjing Jiangnan design rainfall with `r=0.393`, and a 12-hour simulation. No `ft.par`, `inpFile`, `uniform_rules`, FV1, DG2, SWMM, or private model mount is used.

## Web Interaction

The existing map and layer controls remain. A `Select area` action lets the user click or tap two opposite corners; Leaflet's built-in `L.rectangle` displays the selection, so no draw plugin is added. The panel shows selected area and a `Run simulation` button. The button is disabled for an incomplete or visibly invalid rectangle.

Submitting a job disables duplicate submission, displays `Queued` or `Running`, and polls every two seconds. Completion replaces the rectangle with backend-snapped bounds, loads the returned manifest, defaults to Population risk, and displays DEM, Population, Maximum depth, Flood hazard, Population risk, Flooded area, Exposed population, and Maximum depth. Failure shows a short retryable message. Existing disclaimers, OSM attribution, opacity control, return-period choices, and responsive layout remain.

## Public API

### `GET /api/lisflood/config`

Returns:

```json
{
  "schemaVersion": 1,
  "availableBounds": [[32.042871, 118.757673], [32.258495, 119.238814]],
  "defaultBounds": [[32.074303, 118.904460], [32.227474, 119.091316]],
  "maxAreaKm2": 300,
  "gridSizeM": 30,
  "returnPeriods": [5, 10, 20, 50, 100]
}
```

### `POST /api/lisflood/run`

Accepts only JSON shaped as:

```json
{"bounds": [[32.08, 118.91], [32.20, 119.08]], "returnPeriod": 20}
```

It returns HTTP 200 for an existing completed result or HTTP 202 for a queued/running job:

```json
{
  "jobId": "deterministic-id",
  "status": "queued",
  "effectiveBounds": [[32.08, 118.91], [32.20, 119.08]],
  "statusUrl": "/api/lisflood/jobs/deterministic-id"
}
```

Invalid JSON, return periods, bounds, extent, or area return HTTP 400. A full queue returns 429, low disk returns 507, and an unavailable engine returns 503. Error bodies contain a stable `error` string and no internal paths.

### `GET /api/lisflood/jobs/<jobId>`

Returns `queued`, `running`, `completed`, or `failed`. A completed response contains `/results/<jobId>/manifest.json`; a failed response contains a short public error. Unknown job IDs return 404 unless a completed result directory exists, in which case it is reconstructed as completed.

## Cache and Results

The deterministic job ID hashes snapped grid row/column bounds, return period, LISFLOOD version, parameter version, and base-data checksum. Requests that snap to the same grid and use the same return period share queued work or completed output.

Each job writes to `/results/.<jobId>.tmp`, validates model exit status, non-negative depth, aligned outputs, and mass-balance error no greater than 3%, then atomically renames the directory. Failed temporary output is deleted; completed data is never overwritten. Raw time-series files and the cropped working directory are deleted after each run. Completed caches are retained indefinitely in phase one; new jobs require at least 15 GB free disk.

The per-job manifest is intentionally flat:

```json
{
  "schemaVersion": 1,
  "generatedAt": "ISO-8601",
  "modelVersion": "8.0.3 ACC",
  "dataVersion": "sha256",
  "returnPeriod": 20,
  "rainfallMm": 0,
  "bounds": [[32.08, 118.91], [32.20, 119.08]],
  "populationBreaks": [0, 0, 0],
  "layers": {
    "dem": "/results/<jobId>/dem.png",
    "population": "/results/<jobId>/population.png",
    "depth": "/results/<jobId>/depth.png",
    "hazard": "/results/<jobId>/hazard.png",
    "risk": "/results/<jobId>/risk.png"
  },
  "stats": {
    "floodedAreaKm2": 0,
    "exposedPopulation": 0,
    "maximumDepthM": 0
  }
}
```

Hazard uses the official `.maxHaz` output and Defra thresholds `<0.75`, `0.75–1.25`, `1.25–2.5`, and `≥2.5`. Positive WorldPop cells within the selected rectangle define the three quartile breaks. The existing risk matrix remains unchanged, and depth below `0.10 m` remains No flood.

## Deployment

The runner Dockerfile keeps the pinned official source download, checksum, compilation, version assertion, and rainfall smoke test. It additionally copies the versioned compressed base data and starts the HTTP service. Compose removes the tools-only profile and private model mount, keeps the cache volume, adds port exposure and health checking, and restarts the service unless stopped.

Nginx adds the upstream/API locations and submission rate limit without making the existing WASP, Analytics, or telemetry services depend on LISFLOOD health. `deploy-lisflood.sh` no longer requires `ft.par` or precomputes five scenarios; it builds the runner and Nginx images, starts the service, verifies its health/config endpoints, preserves the existing certificate workflow, and verifies HTTPS.

## Verification

- Validate base raster CRS, 30 m alignment, complete rectangular coverage, NoData handling, checksums, WorldPop population conservation, and attribution.
- Retain the existing rainfall, hazard filename, risk matrix, parameter exclusion, model-version, mass-balance, and atomic-publication checks, adapting them from five scenarios to one selected scenario.
- Add one compact test module for WGS84-to-grid snapping, default and maximum area, containment, stable job IDs, cache hits, duplicate in-flight requests, FIFO single execution, queue-full/low-disk errors, restart cache reconstruction, failed-run cleanup, and flat manifest paths.
- Update the existing web contract test for rectangle controls, Run submission, polling, completed manifest rendering, error state, disclaimer, API proxy, Compose limits, image data, deployment checks, and HTTPS health.
- Run only LISFLOOD tests plus Docker Compose config validation and the runner image smoke test. Do not modify or run unrelated WASP or Analytics tests.

## Deliberate First-Phase Limits

The service has one global worker and at most eight pending jobs. Completed cache cleanup is manual. There is no percentage progress, cancellation, authentication, per-user history, online data fetch, night-light/traffic proxy, sewer network, engineered drainage, or simulation outside the built-in Nanjing data extent. Add those only when measured use or a separate research requirement justifies them.
