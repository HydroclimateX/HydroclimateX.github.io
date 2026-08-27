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
        self.assertIn("fetch('/results/manifest.json'", script)
        self.assertIn("L.imageOverlay", script)
        self.assertNotIn("WebSocket", script)

    def test_compose_and_nginx_publish_only_cached_results(self) -> None:
        compose = read("docker-compose.yml")
        environment = read(".env.example")
        nginx = read("nginx.analytics.conf")
        lisflood = read("nginx/lisflood.conf")
        bootstrap = read("nginx.bootstrap.conf")
        dockerfile = read("nginx/Dockerfile")
        selector = read("nginx/select-config.sh")
        self.assertIn("lisflood-runner:", compose)
        self.assertIn('profiles: ["lisflood-tools"]', compose)
        self.assertIn("mem_limit: 2560m", compose)
        self.assertIn("cpus: 2.0", compose)
        self.assertIn("LISFLOOD_MODEL_DIR: /opt/lisflood/model", compose)
        self.assertIn("${LISFLOOD_MODEL_DIR:-/opt/hydroclimatex-wasp/lisflood-private/model}:/opt/lisflood/model:ro", compose)
        self.assertNotIn("LISFLOOD_PRIVATE_DIR", compose)
        self.assertNotIn("LISFLOOD_REQUIRE_PARITY", compose)
        self.assertIn("LISFLOOD_MODEL_DIR=/opt/hydroclimatex-wasp/lisflood-private/model", environment)
        self.assertNotIn("LISFLOOD_PRIVATE_DIR", environment)
        self.assertNotIn("LISFLOOD_REQUIRE_PARITY", environment)
        self.assertIn("${LISFLOOD_CACHE_DIR:-/opt/hydroclimatex-wasp/state/lisflood-cache}:/srv/lisflood-results:ro", compose)
        self.assertNotIn("server_name lisflood.hydroclimatex.com", nginx)
        self.assertIn("server_name lisflood.hydroclimatex.com", lisflood)
        self.assertIn("location = /results/manifest.json", lisflood)
        self.assertIn("alias /srv/lisflood-results/", lisflood)
        self.assertIn("lisflood.hydroclimatex.com", bootstrap)
        self.assertIn("COPY lisflood-app /usr/share/nginx/lisflood", dockerfile)
        self.assertIn("COPY nginx/lisflood.conf /opt/wasp/lisflood.conf", dockerfile)
        self.assertIn("cat /opt/wasp/lisflood.conf >> /etc/nginx/conf.d/default.conf", selector)

    def test_deploy_script_guards_dns_cache_and_https(self) -> None:
        deploy = read("deploy-lisflood.sh")
        self.assertIn('EXPECTED_IP="8.210.252.61"', deploy)
        self.assertIn("docker compose --profile lisflood-tools run --rm lisflood-runner", deploy)
        self.assertIn("/results/manifest.json", deploy)
        self.assertIn("--resolve", deploy)
        self.assertIn("PRIOR_NGINX_IMAGE", deploy)
        self.assertIn("docker image tag", deploy)
        self.assertIn('MODEL_DIR="${LISFLOOD_MODEL_DIR:-/opt/hydroclimatex-wasp/lisflood-private/model}"', deploy)
        self.assertIn('for required in ft.par dem.asc population.asc', deploy)
        self.assertNotIn("missing private LISFLOOD-FP source", deploy)

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
                        if name == "population" and number != header["nodata_value"]:
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
