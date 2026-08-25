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
        nginx = read("nginx.analytics.conf")
        bootstrap = read("nginx.bootstrap.conf")
        dockerfile = read("nginx/Dockerfile")
        self.assertIn("lisflood-runner:", compose)
        self.assertIn('profiles: ["lisflood-tools"]', compose)
        self.assertIn("mem_limit: 2560m", compose)
        self.assertIn("cpus: 2.0", compose)
        self.assertIn("${LISFLOOD_CACHE_DIR:-/opt/hydroclimatex-wasp/state/lisflood-cache}:/srv/lisflood-results:ro", compose)
        self.assertIn("server_name lisflood.hydroclimatex.com", nginx)
        self.assertIn("alias /srv/lisflood-results/", nginx)
        self.assertIn("lisflood.hydroclimatex.com", bootstrap)
        self.assertIn("COPY lisflood-app /usr/share/nginx/lisflood", dockerfile)

    def test_deploy_script_guards_dns_cache_and_https(self) -> None:
        deploy = read("deploy-lisflood.sh")
        self.assertIn('EXPECTED_IP="8.210.252.61"', deploy)
        self.assertIn("docker compose --profile lisflood-tools run --rm lisflood-runner", deploy)
        self.assertIn("/results/manifest.json", deploy)
        self.assertIn("--resolve", deploy)
        self.assertIn("PRIOR_NGINX_IMAGE", deploy)
        self.assertIn("docker image tag", deploy)

    def test_preparation_script_aligns_the_open_rasters(self) -> None:
        prepare = read("scripts/prepare-lisflood-data.sh")
        self.assertIn("Copernicus GLO-30", prepare)
        self.assertIn("WorldPop 2025", prepare)
        self.assertIn("-t_srs EPSG:32650", prepare)
        self.assertIn("-tr 30 30", prepare)
        self.assertIn("-r sum", prepare)


if __name__ == "__main__":
    unittest.main()
