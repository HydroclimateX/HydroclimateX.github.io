# LISFLOOD Interactive Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a public user select a rectangle up to 300 km², run one LISFLOOD-FP return-period scenario, wait for completion, and reuse an identical cached result.

**Architecture:** Keep `lisflood-app` as native Leaflet and turn the existing `lisflood-runner` image into one standard-library HTTP service with a bounded in-memory FIFO queue and one worker. Store completed PNG/JSON results in the existing shared cache; bundle the rectangular Nanjing DEM and WorldPop grids inside the image.

**Tech Stack:** Python 3.11 standard library, NumPy, Pillow, LISFLOOD-FP 8.0.3 CPU ACC, GDAL CLI, Leaflet, Nginx, Docker Compose, `unittest`.

---

## File map

- Modify `lisflood_runner/generate.py`: grid conversion/cropping, generated parameters, one-scenario execution, validation, flat manifest, atomic result publication.
- Create `lisflood_runner/service.py`: HTTP endpoints, validation, deterministic cache key, eight-slot queue, one worker.
- Create `lisflood_runner/test_service.py`: API, queue, cache, restart, and failure tests.
- Modify `lisflood_runner/test_generate.py`: synthetic grid and one-scenario tests.
- Add `lisflood_runner/data/dem.asc.gz`, `population.asc.gz`, `DATA-SOURCES.md`, and `SHA256SUMS`: built-in rectangular source data and provenance.
- Modify the three files under `lisflood-app/`: two-corner rectangle selection, Run submission, polling, and flat-manifest rendering.
- Modify `lisflood_runner/test_web.py`: static frontend/deployment contract.
- Modify `lisflood_runner/Dockerfile`, `docker-compose.yml`, `nginx/lisflood.conf`, `nginx.analytics.conf`, `.env.example`, `deploy-lisflood.sh`, and `lisflood_runner/README.md`: long-running service deployment.

### Task 1: Build the tracked rectangular base-data bundle

**Files:**
- Create: `lisflood_runner/data/dem.asc.gz`
- Create: `lisflood_runner/data/population.asc.gz`
- Create: `lisflood_runner/data/DATA-SOURCES.md`
- Create: `lisflood_runner/data/SHA256SUMS`

- [ ] **Step 1: Fetch and crop only the required source windows**

Run in a disposable directory; the `/vsicurl/` paths avoid downloading full global/national rasters:

```bash
mkdir -p /tmp/lisflood-base lisflood_runner/data
gdalbuildvrt /tmp/lisflood-base/dem.vrt \
  /vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N32_00_E118_00_DEM/Copernicus_DSM_COG_10_N32_00_E118_00_DEM.tif \
  /vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/Copernicus_DSM_COG_10_N32_00_E119_00_DEM/Copernicus_DSM_COG_10_N32_00_E119_00_DEM.tif
gdalwarp -overwrite -t_srs EPSG:32650 \
  -te 665955.77 3546538.43 710895.77 3571288.43 -ts 1498 825 \
  -r bilinear -dstnodata -9999 /tmp/lisflood-base/dem.vrt /tmp/lisflood-base/dem.tif
gdalwarp -overwrite -t_srs EPSG:32650 \
  -te 665955.77 3546538.43 710895.77 3571288.43 -ts 1498 825 \
  -r sum -dstnodata 0 \
  /vsicurl/https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/CHN/v1/100m/constrained/chn_pop_2025_CN_100m_R2025A_v1.tif \
  /tmp/lisflood-base/population.tif
gdal_translate -of AAIGrid -a_nodata -9999 /tmp/lisflood-base/dem.tif lisflood_runner/data/dem.asc
gdal_translate -of AAIGrid -a_nodata -9999 /tmp/lisflood-base/population.tif lisflood_runner/data/population.asc
gzip -n -9 lisflood_runner/data/dem.asc lisflood_runner/data/population.asc
```

Expected: both gzip files exist; decompressed headers are `1498` columns, `825` rows, and `30` metre cells. The three source URLs were HEAD-verified on 2026-08-27: both DEM tiles and the 922,251,437-byte WorldPop file returned HTTP 200 with byte-range support.

- [ ] **Step 2: Add exact provenance**

Create `lisflood_runner/data/DATA-SOURCES.md` with:

```markdown
# LISFLOOD base data

- DEM: Copernicus DEM GLO-30 tiles N32E118 and N32E119, bilinearly projected to EPSG:32650 at 30 m. Source: https://registry.opendata.aws/copernicus-dem/
- Population: WorldPop R2025A constrained 2025 population counts for China, summed into the aligned 30 m grid. DOI: 10.5258/SOTON/WP00839. Source: https://hub.worldpop.org/geodata/summary?id=72922
- Extent: 665955.77, 3546538.43, 710895.77, 3571288.43 in EPSG:32650.
- Licences: Copernicus DEM licence and WorldPop CC BY 4.0; retain attribution when redistributing derived rasters.
- Processing: GDAL `gdalbuildvrt`, `gdalwarp`, and `gdal_translate` commands recorded in the implementation plan.
```

Generate reproducible checksums:

```bash
cd lisflood_runner/data
sha256sum dem.asc.gz population.asc.gz > SHA256SUMS
```

- [ ] **Step 3: Validate alignment and usable coverage**

Run:

```bash
python3 - <<'PY'
import gzip
import numpy as np
from pathlib import Path

def read(name):
    with gzip.open(Path('lisflood_runner/data') / name, 'rt') as f:
        header = {k.lower(): float(v) for k, v in (f.readline().split()[:2] for _ in range(6))}
        data = np.loadtxt(f)
    return header, data

dem_h, dem = read('dem.asc.gz')
pop_h, pop = read('population.asc.gz')
assert dem_h == pop_h
assert dem.shape == pop.shape == (825, 1498)
assert dem_h['cellsize'] == 30
assert np.isfinite(dem[dem != -9999]).all()
assert (pop[pop != -9999] >= 0).all() and pop[pop > 0].sum() > 0
print('base-data-ok')
PY
```

Expected: `base-data-ok`.

- [ ] **Step 4: Commit**

```bash
git add lisflood_runner/data
git commit -m "data: bundle Nanjing LISFLOOD base grids"
```

### Task 2: Refactor the runner to execute one selected grid window

**Files:**
- Modify: `lisflood_runner/generate.py`
- Modify: `lisflood_runner/test_generate.py`

- [ ] **Step 1: Write failing grid and parameter tests**

Add imports for `crop_grid`, `job_id`, `snap_bounds`, and `write_ascii`, then add:

```python
class WindowTests(unittest.TestCase):
    HEADER = {
        "ncols": 10.0, "nrows": 10.0, "xllcorner": 500000.0,
        "yllcorner": 3500000.0, "cellsize": 30.0, "nodata_value": -9999.0,
    }

    def test_snap_crop_and_stable_job_id(self) -> None:
        with unittest.mock.patch(
            "lisflood_runner.generate.transform_points",
            return_value=[(500030.0, 3500030.0), (500180.0, 3500180.0)],
        ):
            window, effective = snap_bounds([[31.0, 118.0], [31.1, 118.1]], self.HEADER, 300)
        self.assertEqual(window, (1, 1, 6, 6))
        self.assertEqual(len(effective), 2)
        grid = np.arange(100).reshape(10, 10)
        np.testing.assert_array_equal(crop_grid(grid, window), grid[4:9, 1:6])
        self.assertEqual(job_id(window, 20, "8.0.3", "data"), job_id(window, 20, "8.0.3", "data"))

    def test_rejects_area_above_limit(self) -> None:
        large_header = dict(self.HEADER, ncols=1000.0, nrows=1000.0)
        with unittest.mock.patch(
            "lisflood_runner.generate.transform_points",
            return_value=[(500000.0, 3500000.0), (520000.0, 3520000.0)],
        ):
            with self.assertRaisesRegex(ValueError, "300"):
                snap_bounds([[31.0, 118.0], [31.2, 118.2]], large_header, 300)

    def test_write_ascii_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "grid.asc"
            data = np.arange(9, dtype=float).reshape(3, 3)
            header = dict(self.HEADER, ncols=3.0, nrows=3.0)
            write_ascii(path, header, data)
            actual_header, actual = generate.read_ascii(path)
            self.assertEqual(actual_header["ncols"], 3)
            np.testing.assert_allclose(actual, data)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python -m unittest lisflood_runner.test_generate.WindowTests -v
```

Expected: import failures for the four new functions.

- [ ] **Step 3: Add the minimum grid helpers**

Add to `generate.py`:

```python
import gzip
import hashlib

PARAMETER_VERSION = "surface-v1"

def transform_points(points, source: str, target: str) -> list[tuple[float, float]]:
    result = subprocess.run(
        ["gdaltransform", "-s_srs", source, "-t_srs", target],
        input="".join(f"{x} {y}\n" for x, y in points), text=True,
        capture_output=True, check=True,
    )
    return [tuple(map(float, line.split()[:2])) for line in result.stdout.splitlines()]

def snap_bounds(bounds, header, max_area_km2):
    (south, west), (north, east) = bounds
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("invalid bounds")
    (x0, y0), (x1, y1) = transform_points([(west, south), (east, north)], "EPSG:4326", "EPSG:32650")
    origin_x, origin_y, cell = header["xllcorner"], header["yllcorner"], header["cellsize"]
    window = (
        max(0, math.ceil((x0 - origin_x) / cell)),
        max(0, math.ceil((y0 - origin_y) / cell)),
        min(int(header["ncols"]), math.floor((x1 - origin_x) / cell)),
        min(int(header["nrows"]), math.floor((y1 - origin_y) / cell)),
    )
    c0, r0, c1, r1 = window
    if c0 >= c1 or r0 >= r1 or x0 < origin_x or y0 < origin_y or x1 > origin_x + header["ncols"] * cell or y1 > origin_y + header["nrows"] * cell:
        raise ValueError("bounds outside available extent")
    area = (c1 - c0) * (r1 - r0) * cell * cell / 1e6
    if area > max_area_km2:
        raise ValueError(f"area exceeds {max_area_km2:g} km²")
    corners = transform_points(
        [(origin_x + c0 * cell, origin_y + r0 * cell), (origin_x + c1 * cell, origin_y + r1 * cell)],
        "EPSG:32650", "EPSG:4326",
    )
    return window, [[corners[0][1], corners[0][0]], [corners[1][1], corners[1][0]]]

def crop_grid(data, window):
    c0, r0, c1, r1 = window
    return data[data.shape[0] - r1:data.shape[0] - r0, c0:c1]

def write_ascii(path, header, data):
    lines = (
        f"ncols {data.shape[1]}\n", f"nrows {data.shape[0]}\n",
        f"xllcorner {header['xllcorner']:.6f}\n", f"yllcorner {header['yllcorner']:.6f}\n",
        f"cellsize {header['cellsize']:.6f}\n", f"NODATA_value {NODATA:g}\n",
    )
    with path.open("w", encoding="utf-8") as target:
        target.writelines(lines)
        np.savetxt(target, np.nan_to_num(data, nan=NODATA), fmt="%.6f")

def job_id(window, period, model, data_version):
    value = f"{','.join(map(str, window))}|{period}|{model}|{PARAMETER_VERSION}|{data_version}"
    return hashlib.sha256(value.encode()).hexdigest()[:20]
```

Update `read_ascii` to use `gzip.open(path, "rt")` when `path.suffix == ".gz"`.

- [ ] **Step 4: Replace the five-scenario entrypoint with `run_job`**

Keep `design_storm`, `build_risk`, `write_rainfall`, `mass_balance_error`, `save_layer`, and `model_version`. Replace `run_all`/`publish_cache` with one function having this exact contract:

```python
def run_job(engine: Path, base_header: dict[str, float], dem: np.ndarray,
            population: np.ndarray, window: tuple[int, int, int, int], period: int,
            effective_bounds: list[list[float]], data_version: str,
            staging: Path, timeout: int = 7200) -> dict:
    """Run one cropped ACC rainfall scenario and write a flat manifest to staging."""
```

Use a code-owned `PARAMETERS` string containing `DEMfile dem.asc`, `resroot result`, `dirroot results`, `sim_time 43200`, `initial_tstep 10`, `massint 3600`, `saveint 3600`, `acceleration`, `fpfric 0.06`, `infiltration 0.00001`, `hazard`, `depththresh 0.01`, `comp_out`, `rainfall design.rain`, and `evaporation evaporation.evap`. Write the four-line existing evaporation series from `lisflood-fp/ft.evap` as a constant; do not copy `ft.par` or any boundary/SWMM input.

Implement the function in this order:

```python
c0, r0, c1, r1 = window
cell = base_header["cellsize"]
header = dict(base_header, ncols=float(c1 - c0), nrows=float(r1 - r0),
              xllcorner=base_header["xllcorner"] + c0 * cell,
              yllcorner=base_header["yllcorner"] + r0 * cell)
cropped_dem = crop_grid(dem, window)
cropped_population = np.nan_to_num(crop_grid(population, window), nan=0.0)
staging.mkdir(parents=True)

with tempfile.TemporaryDirectory(prefix="lisflood-job-") as directory:
    work = Path(directory)
    write_ascii(work / "dem.asc", header, cropped_dem)
    rates = design_storm(period)
    write_rainfall(work / "design.rain", rates)
    (work / "evaporation.evap").write_text(EVAPORATION, encoding="utf-8")
    (work / "web.par").write_text(PARAMETERS, encoding="utf-8")
    subprocess.run([str(engine), "web.par"], cwd=work, check=True, timeout=timeout)
    output = work / "results"
    error = mass_balance_error(locate_output(output, ".mass"))
    if error > 0.03:
        raise RuntimeError(f"mass-balance error is {error:.2%}")
    depth_header, depth = read_ascii(locate_output(output, ".max"))
    hazard_header, hazard = read_ascii(locate_output(output, HAZARD_SUFFIX))
    assert_aligned(header, depth_header)
    assert_aligned(header, hazard_header)
    if np.nanmin(depth) < -1e-8:
        raise RuntimeError("negative depth")

risk, breaks = build_risk(depth, hazard, cropped_population)
flooded = np.isfinite(depth) & (depth >= 0.10)
save_layer(staging / "dem.png", cropped_dem,
           [(20 + i * 24, 55 + i * 18, 45 + i * 15, 190) for i in range(10)])
save_layer(staging / "population.png", cropped_population,
           [(83 + i * 12, 35, 130 + i * 10, 30 + i * 20) for i in range(10)])
save_layer(staging / "depth.png", np.where(flooded, depth, np.nan),
           [(16, 100 + i * 14, 180 + i * 7, 30 + i * 20) for i in range(10)])
save_layer(staging / "hazard.png",
           np.where(flooded, np.digitize(hazard, [0.75, 1.25, 2.5]) + 1, 0),
           [(0, 0, 0, 0), (254, 229, 153, 170), (253, 174, 97, 190),
            (240, 59, 32, 210), (122, 1, 119, 220)], True)
save_layer(staging / "risk.png", risk,
           [(0, 0, 0, 0), (49, 163, 84, 175), (254, 224, 139, 190),
            (244, 109, 67, 210), (165, 0, 38, 225)], True)
manifest = {
    "schemaVersion": 1, "generatedAt": datetime.now(timezone.utc).isoformat(),
    "modelVersion": model_version(engine), "dataVersion": data_version,
    "returnPeriod": period, "rainfallMm": round(float(rates.sum() / 60), 3),
    "bounds": effective_bounds,
    "populationBreaks": [round(float(value), 6) for value in breaks],
    "layers": {name: f"{name}.png" for name in ("dem", "population", "depth", "hazard", "risk")},
    "stats": {
        "floodedAreaKm2": round(float(flooded.sum() * cell * cell / 1e6), 3),
        "exposedPopulation": round(float(cropped_population[flooded].sum())),
        "maximumDepthM": round(float(np.nanmax(depth)), 3),
    },
}
(staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
return manifest
```

Change `build_risk` so an all-zero population crop uses breaks `[0.0, 0.0, 0.0]` and exposure class zero instead of raising. Add a test for this branch. Before final publication, the service changes every relative layer value to `/results/<jobId>/<name>.png` and rewrites the manifest once.

- [ ] **Step 5: Run runner tests**

Run:

```bash
python -m unittest lisflood_runner.test_generate -v
```

Expected: all tests pass; the old “all five scenarios” publication tests have been replaced by one-job atomic-result tests.

- [ ] **Step 6: Commit**

```bash
git add lisflood_runner/generate.py lisflood_runner/test_generate.py
git commit -m "refactor: run one LISFLOOD grid window"
```

### Task 3: Add the standard-library API and single worker

**Files:**
- Create: `lisflood_runner/service.py`
- Create: `lisflood_runner/test_service.py`

- [ ] **Step 1: Write failing service tests**

Create tests around a temporary cache and an injected runner. The planned `Service` constructor is `Service(cache_dir, engine, header, dem, population, data_version, model_version, max_area=300, timeout=7200, minimum_free_gb=15, runner=generate.run_job, start_worker=True)`; `run_next()` processes exactly one queued item so FIFO behavior is testable without timing sleeps.

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from lisflood_runner.generate import job_id
from lisflood_runner.service import InsufficientStorage, QueueFull, Service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.cache = Path(self.temporary.name)
        self.header = {
            "ncols": 20.0, "nrows": 20.0, "xllcorner": 0.0,
            "yllcorner": 0.0, "cellsize": 30.0, "nodata_value": -9999.0,
        }
        self.grid = np.ones((20, 20))
        self.completed = []

        def runner(engine, header, dem, population, window, period,
                   effective, data_version, staging, timeout):
            self.completed.append((window, period))
            manifest = {"schemaVersion": 1, "bounds": effective, "returnPeriod": period}
            (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            return manifest

        self.service = Service(
            self.cache, Path("/engine"), self.header, self.grid, self.grid,
            "data", "8.0.3 ACC", minimum_free_gb=0,
            runner=runner, start_worker=False,
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def snapped(index=0):
        return (index, 0, index + 1, 1), [[0.0, float(index)], [1.0, float(index + 1)]]

    def test_same_window_and_period_share_job_id(self):
        with patch("lisflood_runner.service.snap_bounds", return_value=self.snapped()):
            first = self.service.submit([[0, 0], [1, 1]], 20)
            second = self.service.submit([[0, 0], [1, 1]], 20)
        self.assertEqual(first["jobId"], second["jobId"])
        self.assertEqual(self.service.queue.qsize(), 1)

    def test_completed_manifest_is_returned_without_queueing(self):
        identifier = job_id((0, 0, 1, 1), 20, "8.0.3 ACC", "data")
        folder = self.cache / identifier
        folder.mkdir()
        (folder / "manifest.json").write_text('{"schemaVersion":1}', encoding="utf-8")
        with patch("lisflood_runner.service.snap_bounds", return_value=self.snapped()):
            result = self.service.submit([[0, 0], [1, 1]], 20)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.service.queue.qsize(), 0)

    def test_fifo_run_next_executes_one_job_at_a_time(self):
        with patch("lisflood_runner.service.snap_bounds", side_effect=[self.snapped(0), self.snapped(1)]):
            first = self.service.submit([[0, 0], [1, 1]], 5)
            second = self.service.submit([[0, 1], [1, 2]], 10)
        self.service.run_next()
        self.assertEqual(self.completed, [((0, 0, 1, 1), 5)])
        self.assertEqual(self.service.status(first["jobId"])["status"], "completed")
        self.assertEqual(self.service.status(second["jobId"])["status"], "queued")
        self.service.run_next()
        self.assertEqual(self.completed[-1], ((1, 0, 2, 1), 10))

    def test_ninth_pending_job_is_rejected(self):
        windows = [self.snapped(i) for i in range(9)]
        with patch("lisflood_runner.service.snap_bounds", side_effect=windows):
            for index in range(8):
                self.service.submit([[0, index], [1, index + 1]], 20)
            with self.assertRaises(QueueFull):
                self.service.submit([[0, 8], [1, 9]], 20)

    def test_low_disk_is_rejected_before_queueing(self):
        with patch("lisflood_runner.service.ensure_cache_space", side_effect=RuntimeError("disk")), \
             patch("lisflood_runner.service.snap_bounds", return_value=self.snapped()):
            with self.assertRaises(InsufficientStorage):
                self.service.submit([[0, 0], [1, 1]], 20)
        self.assertEqual(self.service.queue.qsize(), 0)

    def test_failed_job_removes_staging_and_reports_failed(self):
        def fail(*args, **kwargs):
            raise RuntimeError("private engine path")
        service = Service(
            self.cache, Path("/engine"), self.header, self.grid, self.grid,
            "data", "8.0.3 ACC", minimum_free_gb=0,
            runner=fail, start_worker=False,
        )
        with patch("lisflood_runner.service.snap_bounds", return_value=self.snapped()):
            result = service.submit([[0, 0], [1, 1]], 20)
        service.run_next()
        self.assertEqual(service.status(result["jobId"])["status"], "failed")
        self.assertEqual(service.status(result["jobId"])["error"], "Simulation failed")
        self.assertFalse((self.cache / f'.{result["jobId"]}.tmp').exists())

    def test_completed_cache_is_reconstructed_after_restart(self):
        identifier = "a" * 20
        folder = self.cache / identifier
        folder.mkdir()
        (folder / "manifest.json").write_text('{"schemaVersion":1}', encoding="utf-8")
        self.assertEqual(self.service.status(identifier)["status"], "completed")
        self.assertEqual(self.service.status(identifier)["manifestUrl"], f"/results/{identifier}/manifest.json")
```

Extend the imports with `threading`, `urllib.error`, `urllib.request`, and `ThreadingHTTPServer`, then add:

```python
class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        cache = Path(self.temporary.name)
        header = {"ncols": 20.0, "nrows": 20.0, "xllcorner": 0.0,
                  "yllcorner": 0.0, "cellsize": 30.0, "nodata_value": -9999.0}
        grid = np.ones((20, 20))

        def runner(engine, header, dem, population, window, period,
                   effective, data_version, staging, timeout):
            manifest = {"schemaVersion": 1, "bounds": effective, "returnPeriod": period}
            (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            return manifest

        self.service = Service(cache, Path("/engine"), header, grid, grid,
                               "data", "8.0.3 ACC", minimum_free_gb=0,
                               runner=runner, start_worker=False)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.service))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def request(self, path, body=None, content_type="application/json"):
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if body is not None else None
        )
        request = urllib.request.Request(self.base + path, data=data)
        if data is not None:
            request.add_header("Content-Type", content_type)
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response = error
        return response.status, json.loads(response.read())

    def test_config_contract(self):
        status, data = self.request("/api/lisflood/config")
        self.assertEqual(status, 200)
        self.assertEqual(data["schemaVersion"], 1)
        self.assertEqual(data["returnPeriods"], [5, 10, 20, 50, 100])

    def test_post_rejects_invalid_inputs(self):
        self.assertEqual(self.request("/api/lisflood/run", b"{")[0], 400)
        self.assertEqual(self.request("/api/lisflood/run", {
            "bounds": [[0, 0], [1, 1]], "returnPeriod": 7,
        })[0], 400)
        for message in ("bounds outside available extent", "area exceeds 300 km²"):
            with patch("lisflood_runner.service.snap_bounds", side_effect=ValueError(message)):
                status, data = self.request("/api/lisflood/run", {
                    "bounds": [[0, 0], [1, 1]], "returnPeriod": 20,
                })
            self.assertEqual(status, 400)
            self.assertEqual(data["error"], message)

    def test_post_then_completed_status(self):
        snapped = ((0, 0, 1, 1), [[0.0, 0.0], [1.0, 1.0]])
        with patch("lisflood_runner.service.snap_bounds", return_value=snapped):
            status, job = self.request("/api/lisflood/run", {
                "bounds": [[0, 0], [1, 1]], "returnPeriod": 20,
            })
        self.assertEqual(status, 202)
        self.service.run_next()
        status, result = self.request(job["statusUrl"])
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["manifestUrl"], f'/results/{job["jobId"]}/manifest.json')

    def test_unknown_job_returns_404(self):
        status, data = self.request("/api/lisflood/jobs/" + "f" * 20)
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "Job not found")
```

Use a real local server bound to port `0`; do not add an HTTP test dependency.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest lisflood_runner.test_service -v
```

Expected: `ModuleNotFoundError: lisflood_runner.service`.

- [ ] **Step 3: Implement `Service`**

Create `service.py` with the injectable `Service` constructor used by the tests and a `Service.from_environment()` classmethod. The classmethod loads `/opt/lisflood/data/dem.asc.gz` and `population.asc.gz`, verifies `SHA256SUMS`, and supplies the environment defaults below. The object exposes `config()`, `submit(bounds, period)`, `status(job_id)`, `run_next()`, and `worker()`, and owns `queue.Queue(maxsize=8)`, a state dictionary, and a lock. `submit` calls `ensure_cache_space(cache_dir, minimum_free_gb)`, then uses `snap_bounds` and `job_id`; a completed manifest wins over in-memory state. `run_next` writes `.<jobId>.tmp`, calls `run_job`, prefixes manifest layer URLs, atomically renames the directory, and always removes failed staging. `worker` is only `while True: run_next()`.

Use environment defaults:

```python
DATA_DIR = Path(os.getenv("LISFLOOD_DATA_DIR", "/opt/lisflood/data"))
CACHE_DIR = Path(os.getenv("LISFLOOD_CACHE_DIR", "/opt/lisflood/cache"))
ENGINE = Path(os.getenv("LISFLOOD_ENGINE", "/opt/lisflood/bin/lisflood"))
MAX_AREA = float(os.getenv("LISFLOOD_MAX_AREA_KM2", "300"))
TIMEOUT = int(os.getenv("LISFLOOD_JOB_TIMEOUT_SECONDS", "7200"))
DEFAULT_WINDOW = (460, 124, 1037, 701)
```

- [ ] **Step 4: Implement the three HTTP routes**

Subclass `BaseHTTPRequestHandler` and implement only:

```python
GET  /api/lisflood/config
POST /api/lisflood/run
GET  /api/lisflood/jobs/<20 lowercase hex characters>
```

Limit request bodies to 4096 bytes, require `Content-Type: application/json`, serialize via `json.dumps`, set `Content-Type: application/json`, never expose exception text or filesystem paths, and suppress default client-address logging. Start `ThreadingHTTPServer(("0.0.0.0", 8080), Handler)` and one daemon worker thread in `main()`.

Define `QueueFull`, `InsufficientStorage`, and `EngineUnavailable` as small exception classes. Map them to `{"error":"Queue is full"}`/429, `{"error":"Insufficient storage"}`/507, and `{"error":"Simulation engine unavailable"}`/503. Map JSON/type/period/bounds `ValueError` messages to 400, job lookup misses to `{"error":"Job not found"}`/404, and every other exception to `{"error":"Internal service error"}`/500. Only the worker's public failure state is `{"status":"failed","error":"Simulation failed"}`.

- [ ] **Step 5: Run service and runner tests**

```bash
python -m unittest lisflood_runner.test_generate lisflood_runner.test_service -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add lisflood_runner/service.py lisflood_runner/test_service.py
git commit -m "feat: queue interactive LISFLOOD jobs"
```

### Task 4: Add rectangle selection and result polling to the static page

**Files:**
- Modify: `lisflood-app/index.html`
- Modify: `lisflood-app/app.js`
- Modify: `lisflood-app/style.css`
- Modify: `lisflood_runner/test_web.py`

- [ ] **Step 1: Update the failing web contract**

Assert that HTML contains `selectArea`, `selectedArea`, and `runSimulation`; JavaScript contains `/api/lisflood/config`, `/api/lisflood/run`, `/api/lisflood/jobs/`, `L.rectangle`, a two-second poll, and no WebSocket. Replace the old root-manifest assertion with flat `manifest.layers` and `manifest.stats` assertions.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest lisflood_runner.test_web.WebContractTests.test_map_has_interactive_run_controls -v
```

Expected: failure because `selectArea` is absent.

- [ ] **Step 3: Add minimal accessible controls**

Add beside the return-period fieldset:

```html
<fieldset>
  <legend>Study area</legend>
  <button id="selectArea" type="button">Select area</button>
  <output id="selectedArea">Loading default area…</output>
  <button id="runSimulation" type="button" disabled>Run simulation</button>
</fieldset>
```

Keep the existing layer, opacity, metrics, legend, disclaimer, and source attribution elements.

- [ ] **Step 4: Replace static-manifest startup with config, selection, and polling**

In `app.js`, keep one state object and implement:

```javascript
const state = { config: null, bounds: null, corners: [], rectangle: null,
  period: '20', layer: 'risk', overlay: null, manifest: null, selecting: false };

function rectangleAreaKm2(bounds) {
  const r = 6371.0088, toRad = value => value * Math.PI / 180;
  const [[south, west], [north, east]] = bounds;
  return r * r * Math.abs(Math.sin(toRad(north)) - Math.sin(toRad(south))) * Math.abs(toRad(east - west));
}

async function runSimulation() {
  const response = await fetch('/api/lisflood/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({bounds: state.bounds, returnPeriod: Number(state.period)}),
  });
  const job = await response.json();
  if (!response.ok) throw new Error(job.error || 'Simulation request failed');
  state.bounds = job.effectiveBounds;
  await poll(job.jobId);
}

async function poll(jobId) {
  for (;;) {
    const response = await fetch(`/api/lisflood/jobs/${jobId}`, {cache: 'no-store'});
    const job = await response.json();
    if (!response.ok || job.status === 'failed') throw new Error(job.error || 'Simulation failed');
    if (job.status === 'completed') {
      const result = await fetch(job.manifestUrl, {cache: 'no-store'});
      state.manifest = await result.json();
      render();
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 2000));
  }
}
```

`Select area` clears the current result and arms two map clicks. Sort the two latitudes/longitudes, draw `L.rectangle`, display approximate area, and enable Run only when the rectangle is within `availableBounds` and not above `maxAreaKm2`. Backend validation remains authoritative. Period or rectangle changes clear the old result so stale output is never labelled as current.

- [ ] **Step 5: Render the flat per-job manifest**

Change `layerUrl()` to `state.manifest.layers[state.layer]`; read `state.manifest.stats`; display `state.manifest.returnPeriod`; fit the snapped bounds after completion. Fetch config on startup, draw `defaultBounds`, enable Run, and do not request `/results/manifest.json`.

- [ ] **Step 6: Add only the needed CSS**

Extend existing button styles for the two full-width actions, make `selectedArea` a block with the panel's small text style, and add a disabled state. Do not change the overall layout or add assets.

- [ ] **Step 7: Run the web contract and all Python tests**

```bash
python -m unittest lisflood_runner.test_web lisflood_runner.test_generate lisflood_runner.test_service -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add lisflood-app lisflood_runner/test_web.py
git commit -m "feat: run LISFLOOD from selected rectangles"
```

### Task 5: Run the service in Docker and proxy it through Nginx

**Files:**
- Modify: `lisflood_runner/Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `nginx/lisflood.conf`
- Modify: `nginx.analytics.conf`
- Modify: `lisflood_runner/test_web.py`

- [ ] **Step 1: Write failing deployment assertions**

Require the Dockerfile to copy `lisflood_runner/data` and enter `lisflood_runner.service`; require Compose to remove the tools profile and model mount, expose 8080, set the two configurable limits, retain 2 CPU/2560 MB/cache volume, and add a healthcheck; require Nginx to proxy `/api/lisflood/` and rate-limit the exact POST route. Assert that `nginx.analytics.conf` no longer embeds a duplicate LISFLOOD server block.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest lisflood_runner.test_web.WebContractTests.test_compose_and_nginx_publish_interactive_results -v
```

Expected: failure because runner still has `profiles: ["lisflood-tools"]`.

- [ ] **Step 3: Make the runner image long-running**

Append to the Dockerfile:

```dockerfile
COPY lisflood_runner/data /opt/lisflood/data
EXPOSE 8080
ENTRYPOINT ["python", "-m", "lisflood_runner.service"]
```

Replace the old `ENTRYPOINT`; add no Python dependency.

- [ ] **Step 4: Convert the existing Compose service**

For `lisflood-runner`, remove `profiles`, `init`, and the model volume; add `restart: unless-stopped`, expose 8080, join `wasp-net`, keep the cache volume, and set:

```yaml
environment:
  LISFLOOD_CACHE_DIR: /opt/lisflood/cache
  LISFLOOD_MAX_AREA_KM2: ${LISFLOOD_MAX_AREA_KM2:-300}
  LISFLOOD_JOB_TIMEOUT_SECONDS: ${LISFLOOD_JOB_TIMEOUT_SECONDS:-7200}
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/lisflood/config')"]
  interval: 10s
  timeout: 5s
  retries: 12
```

- [ ] **Step 5: Keep LISFLOOD config in one Nginx file**

Remove the LISFLOOD HTTP/TLS servers from `nginx.analytics.conf`; `nginx/select-config.sh` already appends `nginx/lisflood.conf` only when its certificate exists. Add to `nginx/lisflood.conf`:

```nginx
upstream lisflood_backend { server lisflood-runner:8080; }
limit_req_zone $binary_remote_addr zone=lisflood_submit:10m rate=2r/m;

location = /api/lisflood/run {
    limit_req zone=lisflood_submit burst=1 nodelay;
    proxy_pass http://lisflood_backend;
    proxy_set_header Host $host;
    proxy_read_timeout 30s;
}
location /api/lisflood/ {
    proxy_pass http://lisflood_backend;
    proxy_set_header Host $host;
    proxy_read_timeout 30s;
}
```

Place both API locations before the static `location /` in the existing TLS server.

- [ ] **Step 6: Validate configuration and tests**

```bash
docker compose config --quiet
python -m unittest lisflood_runner.test_web -v
```

Expected: both commands exit 0.

- [ ] **Step 7: Commit**

```bash
git add lisflood_runner/Dockerfile docker-compose.yml nginx/lisflood.conf nginx.analytics.conf lisflood_runner/test_web.py
git commit -m "deploy: serve queued LISFLOOD jobs"
```

### Task 6: Simplify deployment and documentation

**Files:**
- Modify: `deploy-lisflood.sh`
- Modify: `.env.example`
- Modify: `lisflood_runner/README.md`
- Modify: `lisflood_runner/test_web.py`

- [ ] **Step 1: Change deployment contract tests to the new prerequisites**

Assert that the deploy script checks `lisflood_runner/data/{dem.asc.gz,population.asc.gz,SHA256SUMS}`, never checks `ft.par`, never runs the tools profile, starts `lisflood-runner` with `--wait`, and verifies `/api/lisflood/config` through HTTPS. Assert `.env.example` contains the area and timeout variables and no `LISFLOOD_MODEL_DIR`.

- [ ] **Step 2: Run and verify RED**

```bash
python -m unittest lisflood_runner.test_web.WebContractTests.test_deploy_script_guards_data_service_and_https -v
```

Expected: failure on the existing `ft.par` check.

- [ ] **Step 3: Remove obsolete model bootstrap**

In `deploy-lisflood.sh`, remove `MODEL_DIR`, its export, and all model-file loops. Before Docker build, check the three tracked data files. Build runner/Nginx, then run:

```bash
docker compose up -d --build --wait lisflood-runner
```

Keep the existing DNS, certificate, rollback, Nginx health, and existing-service safeguards. Replace the root manifest verification with:

```bash
curl --fail --silent --show-error --resolve "$DOMAIN:443:127.0.0.1" \
  "https://$DOMAIN/api/lisflood/config" | grep -q '"maxAreaKm2"'
```

- [ ] **Step 4: Update configuration and operator documentation**

Replace `LISFLOOD_MODEL_DIR` in `.env.example` with:

```dotenv
LISFLOOD_MAX_AREA_KM2=300
LISFLOOD_JOB_TIMEOUT_SECONDS=7200
LISFLOOD_CACHE_DIR=/opt/hydroclimatex-wasp/state/lisflood-cache
```

Rewrite the README around `./deploy-lisflood.sh`, the three endpoints, the data provenance files, single-worker behavior, manual cache deletion, and the two configuration values. Remove all instructions to create private `ft.par` assets or invoke the tools profile.

- [ ] **Step 5: Run contracts and shell syntax check**

```bash
bash -n deploy-lisflood.sh
python -m unittest lisflood_runner.test_web -v
```

Expected: both exit 0.

- [ ] **Step 6: Commit**

```bash
git add deploy-lisflood.sh .env.example lisflood_runner/README.md lisflood_runner/test_web.py
git commit -m "docs: deploy interactive LISFLOOD service"
```

### Task 7: Full LISFLOOD-only verification

**Files:**
- Modify only if a LISFLOOD verification exposes a defect in the files above.

- [ ] **Step 1: Run the complete LISFLOOD test set**

```bash
python -m unittest lisflood_runner.test_generate lisflood_runner.test_service lisflood_runner.test_web -v
```

Expected: all tests pass.

- [ ] **Step 2: Validate Compose and build the runner smoke test**

```bash
docker compose config --quiet
docker compose build lisflood-runner
```

Expected: config exits 0; the image build verifies LISFLOOD-FP 8.0.3 and the official rainfall `.max`, `.maxHaz`, and `.mass` outputs.

- [ ] **Step 3: Run a container-level API/cache smoke test**

```bash
docker compose up -d --wait lisflood-runner
docker compose exec nginx wget -qO- http://lisflood-runner:8080/api/lisflood/config
```

Expected: JSON reports schema 1, the fixed default bounds, max area 300, grid 30, and five periods.

- [ ] **Step 4: Verify the browser flow through HTTPS**

Open `https://lisflood.hydroclimatex.com`, select two corners, choose one return period, click Run, observe queued/running/completed, switch all five layers, and repeat the identical run. Expected: the second submission returns the existing manifest without starting another LISFLOOD process.

- [ ] **Step 5: Confirm unrelated applications were untouched**

```bash
git diff --name-only 1c6f2ff..HEAD
```

Expected: only LISFLOOD app/runner, shared Compose/Nginx/deploy configuration, `.env.example`, and this plan's data/documentation files appear; no WASP or Analytics application source appears.

- [ ] **Step 6: Commit any verification-only correction**

If Step 1–5 required a correction, review `git status --short`, then stage only the allowed LISFLOOD files and commit:

```bash
git add lisflood-app lisflood_runner docker-compose.yml nginx/lisflood.conf nginx.analytics.conf deploy-lisflood.sh .env.example
git commit -m "fix: complete LISFLOOD interactive verification"
```

If no correction was required, do not create an empty commit.
