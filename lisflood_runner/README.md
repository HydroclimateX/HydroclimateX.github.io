# LISFLOOD Web runner

The service runs official LISFLOOD-FP 8.0.3 CPU ACC for the public Qixia
District research demonstration. It models surface flooding only; underground
sewers and engineered drainage are not represented.

## Deploy

Copy `.env.example` to `.env`, set the existing project secrets, then run from
the repository root as root:

```bash
sudo ./deploy-lisflood.sh
```

The script validates DNS, existing WASP certificates and the tracked input
checksums, builds the runner and Nginx images, starts the runner, obtains the
LISFLOOD certificate when needed, and verifies HTTPS before reporting success.

## Public API

- `GET /api/lisflood/config` returns the available rectangle, area limit,
  supported return periods and model version.
- `GET /api/lisflood/ready` returns `200 {"status":"ready"}` only when the
  model engine is available and the cache filesystem has at least 15 GiB free.
- `POST /api/lisflood/run` accepts
  `{"bounds":[[south,west],[north,east]],"returnPeriod":20}` and returns a
  job identifier. The rectangle must be within the configured domain and
  area limit.
- `GET /api/lisflood/jobs/<jobId>` returns `queued`, `running`, `completed`
  or `failed`. A completed job links to its manifest and PNG layers under
  `/results/<jobId>/`.

There is one FIFO worker. A repeated rectangle and return period reuses the
completed cache entry; failed jobs never replace a completed entry. Delete
cache contents manually, while `lisflood-runner` is stopped, to force a fresh
run.

## Open input data

The image contains the aligned, compressed grids in `lisflood_runner/data/`:

- `dem.asc.gz`: Copernicus GLO-30 DEM, clipped to the prepared study extent;
- `population.asc.gz`: WorldPop 2025 population counts on the same grid;
- `SHA256SUMS`: checksums verified before deployment and at service startup.

See `lisflood_runner/data/DATA-SOURCES.md` for provenance, licensing and processing details.

Verify the bundle from the repository root with:

```bash
(cd lisflood_runner/data && sha256sum -c SHA256SUMS)
```

The first build downloads the pinned official LISFLOOD-FP 8 archive from
[Zenodo](https://zenodo.org/record/4073011/files/LISFLOOD-FP-8.zip).

## Configuration

`LISFLOOD_MAX_AREA_KM2` limits each submitted rectangle (default `300`).
`LISFLOOD_JOB_TIMEOUT_SECONDS` limits one model run (default `7200`).
`LISFLOOD_CACHE_DIR` selects the persistent result cache and normally remains
`/opt/hydroclimatex-wasp/state/lisflood-cache`.

## Low-storage recovery

The runner deliberately rejects new jobs when the cache filesystem has less
than 15 GiB free. Inspect the host before redeploying:

```bash
df -h /opt/hydroclimatex-wasp/state/lisflood-cache
docker system df
du -sh /opt/hydroclimatex-wasp/state/lisflood-cache
```

Unused Docker build cache can be safely regenerated and reclaimed with:

```bash
docker builder prune -af
```

Run the inspection commands again. If the filesystem still has less than
15 GiB free, expand the server disk instead of weakening the reserve.
Do not run `docker system prune --volumes`; the named volumes contain persistent
analytics and application data. Completed LISFLOOD results are also retained
unless an administrator explicitly removes them while the runner is stopped.
