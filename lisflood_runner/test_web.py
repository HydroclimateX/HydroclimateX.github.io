import gzip
import hashlib
import math
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class WebContractTests(unittest.TestCase):
    def test_map_has_only_the_planned_public_controls(self) -> None:
        html = read("lisflood-app/index.html")
        script = read("lisflood-app/app.js")
        self.assertIn('vendor/leaflet.js', html)
        for period in (5, 10, 20, 50, 100):
            self.assertIn(f'data-period="{period}"', html)
        for layer in ("dem", "population", "depth", "hazard", "risk"):
            self.assertIn(f'value="{layer}"', html)
        for metric in ("floodedArea", "exposedPopulation", "maximumDepth"):
            self.assertIn(f'id="{metric}"', html)
        self.assertIn("Research demonstration", html)
        self.assertIn("L.imageOverlay", script)
        self.assertNotIn("WebSocket", script)

    def test_map_has_interactive_run_controls(self) -> None:
        html = read("lisflood-app/index.html")
        script = read("lisflood-app/app.js")
        for element_id in ("selectArea", "selectedArea", "runSimulation"):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in ("/api/lisflood/config", "/api/lisflood/run", "/api/lisflood/jobs/"):
            self.assertIn(endpoint, script)
        self.assertIn("L.rectangle", script)
        self.assertIn("2000", script)
        self.assertNotIn("WebSocket", script)
        self.assertNotIn("fetch('/results/manifest.json'", script)
        self.assertIn("manifest.layers[state.layer]", script)
        self.assertIn("manifest.stats", script)
        self.assertIn("effectiveBounds", script)
        self.assertIn("returnPeriod: Number(state.period)", script)

    def test_geometry_validation_is_separate_from_running_state(self) -> None:
        script = read("lisflood-app/app.js")
        self.assertIn("function geometryIsValid()", script)
        self.assertIn("function canRun()", script)
        self.assertIn("return geometryIsValid() && !state.running;", script)
        self.assertIn("if (!geometryIsValid())", script)

    def test_frontend_validates_results_and_resiliently_polls_jobs(self) -> None:
        html = read("lisflood-app/index.html")
        script = read("lisflood-app/app.js")
        self.assertIn("typeof coordinate !== 'number'", script)
        self.assertIn("Number.isFinite(coordinate)", script)
        self.assertIn("south < -90", script)
        self.assertIn("west < -180", script)
        self.assertIn("south >= north", script)
        self.assertIn("west >= east", script)
        self.assertIn("function normalizeConfig(config)", script)
        self.assertIn("function sameOriginPath(value, pattern)", script)
        self.assertIn("new RegExp(`^/results/${jobId}/${name}", script)
        self.assertIn("normalizeStats(manifest.stats)", script)
        self.assertIn("schemaVersion !== 1", script)
        self.assertIn("POLL_DEADLINE_MS = 24 * 60 * 60 * 1000", script)
        self.assertIn("MAX_TRANSIENT_ERRORS = 3", script)
        self.assertIn("job.statusUrl", script)
        self.assertIn("/api/lisflood/jobs/", script)
        self.assertIn("fetchJson('/api/lisflood/run'", script)
        self.assertIn("transientErrors > MAX_TRANSIENT_ERRORS", script)
        self.assertIn("Math.min(POLL_INTERVAL_MS * 2 ** transientErrors, 10000)", script)
        self.assertIn("/results/", script)
        self.assertIn("Invalid simulation result", script)
        self.assertIn('id="legend" aria-label="Map legend" hidden', html)
        self.assertIn('data-period="20"', html)
        self.assertIn('data-period="5"', html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn('aria-pressed="false"', html)
        self.assertIn("setAttribute('aria-pressed'", script)
        self.assertNotIn("defaultBounds || config.availableBounds", script)

    def test_result_is_bound_to_current_job_request_and_mobile_panel_stays_visible(self) -> None:
        html = read("lisflood-app/index.html")
        script = read("lisflood-app/app.js")
        css = read("lisflood-app/style.css")
        self.assertIn("function boundsMatch(actual, expected", script)
        self.assertIn("function prepareManifest(manifest, jobId, expectedPeriod, expectedBounds, availableBounds)", script)
        self.assertIn("returnPeriod !== expectedPeriod", script)
        self.assertIn("boundsMatch(bounds, expectedBounds)", script)
        self.assertIn("boundsWithin(bounds, availableBounds)", script)
        self.assertIn("^/results/${jobId}/", script)
        self.assertIn("pollJob(job.statusUrl, job.jobId, requestedPeriod, effectiveBounds", script)
        self.assertNotIn("coordinate + 0", script)
        self.assertNotIn(".legend, footer p:not(#status)", css)
        self.assertIn('id="legend" aria-label="Map legend" hidden', html)
        self.assertIn("overflow: auto", css)
        self.assertIn("[hidden] { display: none !important; }", css)

    def test_frontend_exposes_clear_job_feedback_results_and_area_reset(self) -> None:
        html = read("lisflood-app/index.html")
        script = read("lisflood-app/app.js")
        self.assertIn('id="resetArea"', html)
        self.assertIn('Reset to Qixia', html)
        self.assertLess(html.index('id="runSimulation"'), html.index('id="status"'))
        self.assertLess(html.index('id="status"'), html.index('<footer>'))
        self.assertIn('id="results" hidden', html)
        self.assertIn('id="resultMeta"', html)
        for text in ('Ready', 'Simulation queued…', 'Simulation running…', 'Preparing map…', 'Result ready', 'Cached result loaded'):
            self.assertIn(text, script)
        self.assertIn("function resetArea()", script)
        self.assertIn("map.off('click', firstCorner);", script)
        self.assertIn("map.off('click', secondCorner);", script)
        self.assertIn("$('resetArea').disabled = !state.config || state.running;", script)
        self.assertIn("$('resetArea').addEventListener('click', resetArea);", script)
        self.assertIn("$('results').hidden = true;", script)
        self.assertIn("$('results').hidden = false;", script)
        self.assertIn("const returnedPeriod = manifest.returnPeriod;", script)
        self.assertIn("const generatedLabel = generatedAt && !Number.isNaN(generatedAt.getTime())", script)
        self.assertIn("$('resultMeta').textContent = `${returnedPeriod}-year · ${state.config.modelVersion}${generatedLabel}`;", script)
        self.assertIn("maximum ${state.config.maxAreaKm2.toLocaleString()} km²", script)
        self.assertIn('must lie within the available model area', script)
        self.assertIn("const cached = job.status === 'completed';", script)
        self.assertIn("$('status').textContent = 'Preparing map…';", script)
        self.assertNotIn("Preparing cached result…", script)
        self.assertIn("if (!job || typeof job !== 'object' || !isJobId(job.jobId))", script)
        self.assertIn("jobId = job.jobId;\n    if (!isStatusUrl(job.statusUrl, jobId))", script)
        self.assertIn("Simulation failed. Retry or contact the administrator with job ${jobId}.", script)
        self.assertNotIn("function layerUrl()", script)
        self.assertNotIn("$('status').textContent = `${returnedPeriod}-year event ready", script)

    def test_requests_have_bounded_timeout_and_selection_pane(self) -> None:
        script = read("lisflood-app/app.js")
        self.assertIn("const REQUEST_TIMEOUT_MS = 30000", script)
        self.assertIn("new AbortController()", script)
        self.assertIn("controller.signal", script)
        self.assertIn("setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)", script)
        self.assertIn("Request timed out", script)
        self.assertIn("map.createPane('selectionPane')", script)
        self.assertIn("selectionPane.style.zIndex", script)
        self.assertIn("selectionPane.style.pointerEvents = 'none'", script)
        self.assertIn("pane: 'selectionPane'", script)

    def test_compose_and_nginx_publish_interactive_results(self) -> None:
        compose = read("docker-compose.yml")
        environment = read(".env.example")
        nginx = read("nginx.analytics.conf")
        lisflood = read("nginx/lisflood.conf")
        bootstrap = read("nginx.bootstrap.conf")
        dockerfile = read("nginx/Dockerfile")
        runner_dockerfile = read("lisflood_runner/Dockerfile")
        selector = read("nginx/select-config.sh")
        runner = compose.split("  lisflood-runner:\n", 1)[1].split(
            "  # ---- Nginx Reverse Proxy ----", 1
        )[0]

        self.assertIn("lisflood-runner:", compose)
        self.assertNotIn("profiles:", runner)
        self.assertNotIn("init:", runner)
        self.assertNotIn("LISFLOOD_MODEL_DIR", runner)
        self.assertNotIn("/opt/lisflood/model", runner)
        self.assertIn("restart: unless-stopped", runner)
        self.assertIn('expose:\n      - "8080"', runner)
        self.assertIn("networks:\n      - wasp-net", runner)
        self.assertIn("cpus: 2.0", runner)
        self.assertIn("mem_limit: 2560m", runner)
        self.assertIn("LISFLOOD_CACHE_DIR: /opt/lisflood/cache", runner)
        self.assertIn("LISFLOOD_MAX_AREA_KM2: ${LISFLOOD_MAX_AREA_KM2:-300}", runner)
        self.assertIn(
            "LISFLOOD_JOB_TIMEOUT_SECONDS: ${LISFLOOD_JOB_TIMEOUT_SECONDS:-7200}",
            runner,
        )
        self.assertIn(
            "${LISFLOOD_CACHE_DIR:-/opt/hydroclimatex-wasp/state/lisflood-cache}:/opt/lisflood/cache",
            runner,
        )
        self.assertIn(
            "http://localhost:8080/api/lisflood/config",
            runner,
        )
        self.assertNotIn("LISFLOOD_PRIVATE_DIR", compose)
        self.assertNotIn("LISFLOOD_REQUIRE_PARITY", compose)
        self.assertIn("LISFLOOD_MAX_AREA_KM2=300", environment)
        self.assertIn("LISFLOOD_JOB_TIMEOUT_SECONDS=7200", environment)
        self.assertIn("LISFLOOD_CACHE_DIR=/opt/hydroclimatex-wasp/state/lisflood-cache", environment)
        self.assertNotIn("LISFLOOD_MODEL_DIR", environment)
        self.assertNotIn("LISFLOOD_PRIVATE_DIR", environment)
        self.assertNotIn("LISFLOOD_REQUIRE_PARITY", environment)
        self.assertIn("${LISFLOOD_CACHE_DIR:-/opt/hydroclimatex-wasp/state/lisflood-cache}:/srv/lisflood-results:ro", compose)
        self.assertNotIn("server_name lisflood.hydroclimatex.com", nginx)
        self.assertIn("server_name lisflood.hydroclimatex.com", lisflood)
        self.assertIn("location = /results/manifest.json", lisflood)
        self.assertIn("alias /srv/lisflood-results/", lisflood)
        self.assertIn("upstream lisflood_backend", lisflood)
        self.assertIn("limit_req_zone $binary_remote_addr zone=lisflood_submit:10m rate=2r/m;", lisflood)
        self.assertIn("location = /api/lisflood/run", lisflood)
        self.assertIn("limit_req zone=lisflood_submit burst=1 nodelay;", lisflood)
        self.assertIn("location /api/lisflood/", lisflood)
        self.assertLess(lisflood.index("location /api/lisflood/"), lisflood.rindex("location / {"))
        self.assertIn("client_max_body_size 4k;", lisflood)
        self.assertIn(
            'logging:\n      driver: "json-file"\n      options:\n        max-size: "10m"\n        max-file: "3"',
            runner,
        )
        self.assertIn("COPY lisflood_runner/data /opt/lisflood/data", runner_dockerfile)
        self.assertIn("EXPOSE 8080", runner_dockerfile)
        self.assertIn('ENTRYPOINT ["python", "-m", "lisflood_runner.service"]', runner_dockerfile)
        self.assertIn("lisflood.hydroclimatex.com", bootstrap)
        self.assertIn("COPY lisflood-app /usr/share/nginx/lisflood", dockerfile)
        self.assertIn("COPY nginx/lisflood.conf /opt/wasp/lisflood.conf", dockerfile)
        self.assertIn("cat /opt/wasp/lisflood.conf >> /etc/nginx/conf.d/default.conf", selector)

    def test_deploy_script_guards_data_service_and_https(self) -> None:
        deploy = read("deploy-lisflood.sh")
        environment = read(".env.example")
        self.assertIn('EXPECTED_IP="8.210.252.61"', deploy)
        for filename in ("dem.asc.gz", "population.asc.gz", "SHA256SUMS"):
            self.assertIn(f"lisflood_runner/data/{filename}", deploy)
        self.assertNotIn("ft.par", deploy)
        self.assertNotIn("LISFLOOD_MODEL_DIR", deploy)
        self.assertNotIn("--profile lisflood-tools", deploy)
        self.assertIn("docker compose up -d --build --wait lisflood-runner", deploy)
        self.assertIn("--resolve", deploy)
        self.assertIn("PRIOR_NGINX_IMAGE", deploy)
        self.assertIn("docker image tag", deploy)
        self.assertIn("/api/lisflood/config", deploy)
        self.assertIn('grep -q \'"maxAreaKm2"\'', deploy)
        self.assertIn("certificate_is_valid()", deploy)
        self.assertIn("openssl x509", deploy)
        self.assertIn("-checkend 86400", deploy)
        self.assertIn("openssl pkey", deploy)
        self.assertIn("certificate_is_valid \"$existing_domain\"", deploy)
        self.assertIn("certificate_is_valid \"$DOMAIN\"", deploy)
        self.assertIn("--force-renewal", deploy)
        self.assertIn("--wait-timeout 120", deploy)
        self.assertIn("rollback failed", deploy)
        self.assertNotIn('docker compose up -d --no-build --force-recreate nginx || true', deploy)
        self.assertIn('status="${1:-$?}"', deploy)
        self.assertIn("restore_proxy 1", deploy)
        self.assertIn("PRIOR_NGINX_CONFIG", deploy)
        self.assertIn("docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wasp-nginx", deploy)
        self.assertIn('[[ "$PRIOR_NGINX_CONFIG" == "nginx.conf" || "$PRIOR_NGINX_CONFIG" == "nginx.analytics.conf" ]]', deploy)
        self.assertIn('NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up', deploy)
        self.assertNotIn("elif ! NGINX_CONFIG=nginx.analytics.conf docker compose", deploy)
        self.assertNotIn('install -d -m 0755 "$CACHE_DIR"', deploy)
        self.assertNotIn("export WASP_STATE_DIR=\"$STATE_DIR\" LISFLOOD_CACHE_DIR", deploy)
        self.assertIn('install -d -m 0755 "$STATE_DIR/www/.well-known/acme-challenge"', deploy)
        self.assertIn("LISFLOOD_MAX_AREA_KM2=300", environment)
        self.assertIn("LISFLOOD_JOB_TIMEOUT_SECONDS=7200", environment)
        self.assertIn("LISFLOOD_CACHE_DIR=/opt/hydroclimatex-wasp/state/lisflood-cache", environment)
        self.assertNotIn("LISFLOOD_MODEL_DIR", environment)

    def test_runner_image_builds_pinned_official_engine(self) -> None:
        dockerfile = read("lisflood_runner/Dockerfile")
        self.assertIn("https://zenodo.org/record/4073011/files/LISFLOOD-FP-8.zip", dockerfile)
        self.assertIn("a64fce20557217c628ff2ee2641275fcc576dc209326bb3d7cd6d7edad6f5808", dockerfile)
        self.assertIn("cmake --build", dockerfile)
        self.assertIn("lisflood -version", dockerfile)
        self.assertIn("testing/T025_Rain", dockerfile)
        for suffix in (".max", ".maxHaz", ".mass"):
            self.assertIn(f"res_rain{suffix}", dockerfile)

    def test_page_discloses_that_drainage_is_not_modelled(self) -> None:
        html = read("lisflood-app/index.html")
        self.assertIn("Sewer networks and engineered drainage are not represented", html)

    def test_preparation_script_aligns_the_open_rasters(self) -> None:
        prepare = read("scripts/prepare-lisflood-data.sh")
        self.assertIn("Copernicus GLO-30", prepare)
        self.assertIn("WorldPop 2025", prepare)
        self.assertIn("-t_srs EPSG:32650", prepare)
        self.assertIn("-tr 30 30", prepare)
        self.assertIn("-r sum", prepare)

    def test_base_data_bundle_has_aligned_intact_grids(self) -> None:
        data_dir = ROOT / "lisflood_runner" / "data"
        self.assertIn("lisflood_runner/data/DATA-SOURCES.md", read("lisflood_runner/README.md"))
        checksums = {}
        for line in (data_dir / "SHA256SUMS").read_text(encoding="ascii").splitlines():
            digest, name = line.split()
            checksums[name] = digest
        self.assertEqual(set(checksums), {"dem.asc.gz", "population.asc.gz"})

        expected = {
            "ncols": 1498.0,
            "nrows": 825.0,
            "xllcorner": 665955.77,
            "yllcorner": 3546538.43,
            "cellsize": 30.0,
            "nodata_value": -9999.0,
        }
        headers = []
        for name, digest in checksums.items():
            path = data_dir / name
            hasher = hashlib.sha256()
            with path.open("rb") as binary:
                for chunk in iter(lambda: binary.read(1024 * 1024), b""):
                    hasher.update(chunk)
            self.assertEqual(hasher.hexdigest(), digest)

            with gzip.open(path, "rt", encoding="ascii") as source:
                header = {}
                header_lines = [source.readline() for _ in range(6)]
                self.assertEqual(source.buffer.mtime, 0)
                for line in header_lines:
                    key, value = line.split()[:2]
                    header[key.lower()] = float(value)
                row_count = 0
                for line in source:
                    values = line.split()
                    self.assertEqual(len(values), int(header["ncols"]))
                    for value in values:
                        number = float(value)
                        self.assertTrue(math.isfinite(number))
                        if name == "population.asc.gz" and number != header["nodata_value"]:
                            self.assertGreaterEqual(number, 0.0)
                    row_count += 1
                self.assertEqual(row_count, int(header["nrows"]))
                self.assertGreater(row_count, 0)

            for key, value in expected.items():
                self.assertAlmostEqual(header[key], value, places=6)
            headers.append(header)

        self.assertEqual(headers[0], headers[1])


if __name__ == "__main__":
    unittest.main()
