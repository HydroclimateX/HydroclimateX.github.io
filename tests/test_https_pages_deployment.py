"""Regression checks for the public Pages site and HTTPS WASP API deployment.

These tests deliberately inspect deployment sources so they run with only the
Python standard library in CI and on a fresh server checkout.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def write_command_stub(directory: Path, command: str) -> None:
    """Write a harmless command stub used to exercise deploy sequencing."""
    body = f"""#!/usr/bin/env bash
printf '{command} NGINX_CONFIG=%s args=%s\\n' "${{NGINX_CONFIG:-}}" "$*" >> "$WASP_TEST_LOG"
case '{command}' in
  getent) exit 0 ;;
  curl) printf '%b' "${{WASP_CURL_RESPONSE:-$'\\n200'}}" ;;
  docker)
    if [[ "$*" == *"certbot certonly"* ]]; then
      exit "${{WASP_CERTBOT_EXIT:-0}}"
    fi
    ;;
esac
exit 0
"""
    path = directory / command
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def deploy_test_environment(root: Path) -> tuple[dict[str, str], Path]:
    """Copy the deployment script and prepare command stubs in an isolated dir."""
    script = root / "deploy.sh"
    script.write_text(read("deploy.sh"), encoding="utf-8")
    script.chmod(0o755)
    stub_directory = root / "stubs"
    stub_directory.mkdir()
    for command in ("docker", "systemctl", "getent", "curl", "mkdir", "cat", "chmod"):
        write_command_stub(stub_directory, command)
    log = root / "calls.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{stub_directory}{os.pathsep}{environment['PATH']}",
            "WASP_TEST_LOG": str(log),
            "WASP_RENEWAL_SCRIPT": str(root / "renew-wasp-cert"),
            "WASP_CRON_FILE": str(root / "wasp-cert-renew"),
        }
    )
    return environment, log


class HttpsPagesDeploymentTests(unittest.TestCase):
    def test_frontend_uses_https_api_override_and_localhost_fallback(self) -> None:
        source = read("showcase/wasp-web/index.html")

        self.assertIn("window.WASP_API_BASE", source)
        self.assertIn("https://wasp.hydroclimatex.com", source)
        self.assertIn("http://localhost:8000", source)
        self.assertNotIn("http://121.41.164.89", source)

    def test_fastapi_cors_allows_production_pages_origin(self) -> None:
        source = read("backend/app.py")

        self.assertIn('"https://hydroclimatex.com"', source)

    def test_pages_artifact_stages_required_files_and_optional_assets(self) -> None:
        workflow = read(".github/workflows/static.yml")

        self.assertRegex(workflow, r"cp\s+index\.html\s+main\.js\s+style\.css\s+_site/")
        self.assertRegex(workflow, r"cp\s+-r\s+showcase\s+data\s+figs\s+_site/")
        self.assertRegex(workflow, r"if\s+\[\s+-d\s+assets\s+\]")
        for path in ("_site/index.html", "_site/showcase/wasp-web/index.html"):
            self.assertIn(f"test -f {path}", workflow)
        self.assertIn('test -f "_site/figs/Flood&Drought.jpeg"', workflow)

    def test_pages_staging_shell_handles_ampersand_filename(self) -> None:
        workflow = read(".github/workflows/static.yml")
        match = re.search(
            r"Stage web-root files for Pages\n\s+run: \|\n(?P<script>(?:\s{10}.+\n)+)\n\s+- uses:",
            workflow,
        )
        self.assertIsNotNone(match, "could not locate the Pages staging shell")
        script = "\n".join(
            line[10:] for line in match.group("script").splitlines()
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for filename in ("index.html", "main.js", "style.css"):
                (root / filename).touch()
            (root / "showcase" / "wasp-web").mkdir(parents=True)
            (root / "showcase" / "wasp-web" / "index.html").touch()
            (root / "data").mkdir()
            (root / "figs").mkdir()
            (root / "figs" / "Flood&Drought.jpeg").touch()

            result = subprocess.run(
                ["bash", "-euo", "pipefail", "-c", script],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue((root / "_site" / "figs" / "Flood&Drought.jpeg").is_file())

    def test_nginx_terminates_tls_and_leaves_cors_to_fastapi(self) -> None:
        nginx = read("nginx.conf")
        bootstrap = read("nginx.bootstrap.conf")

        self.assertIn("server_name wasp.hydroclimatex.com", nginx)
        self.assertIn("listen 443 ssl", nginx)
        self.assertIn(
            "/etc/letsencrypt/live/wasp.hydroclimatex.com/fullchain.pem", nginx
        )
        self.assertIn("return 301 https://$host$request_uri", nginx)
        self.assertIn("client_max_body_size 10M", nginx)
        self.assertIn("proxy_read_timeout 120s", nginx)
        self.assertNotIn("add_header Access-Control-Allow", nginx)
        self.assertIn("/.well-known/acme-challenge/", bootstrap)
        self.assertIn("server_name wasp.hydroclimatex.com", bootstrap)

    def test_compose_hides_api_port_and_includes_certbot(self) -> None:
        compose = read("docker-compose.yml")

        self.assertNotRegex(compose, r"(?m)^version:")
        self.assertIn("expose:\n      - \"8000\"", compose)
        self.assertNotIn('"8000:8000"', compose)
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)
        self.assertIn(
            "./${NGINX_CONFIG:-nginx.conf}:/etc/nginx/conf.d/default.conf:ro",
            compose,
        )
        self.assertIn("./certbot/www:/var/www/certbot", compose)
        self.assertIn("./certbot/conf:/etc/letsencrypt", compose)
        self.assertIn("certbot:", compose)

    def test_deploy_bootstraps_tls_without_exposing_api_port(self) -> None:
        deploy = read("deploy.sh")

        self.assertIn("ze.jiang@hhu.edu.cn", deploy)
        self.assertIn("wasp.hydroclimatex.com", deploy)
        self.assertIn("certbot certonly --webroot", deploy)
        self.assertIn("docker compose exec -T nginx nginx -s reload", deploy)
        self.assertNotIn("8000/tcp", deploy)
        self.assertNotIn("WASP_BACKEND_URL", deploy)

    def test_deploy_installs_cron_renewal_and_checks_icp_blocking(self) -> None:
        deploy = read("deploy.sh")

        self.assertIn('CRON_FILE="/etc/cron.d/wasp-cert-renew"', deploy)
        self.assertIn('cat > "$CRON_FILE"', deploy)
        self.assertIn('root $RENEWAL_SCRIPT', deploy)
        self.assertIn("Non-compliance ICP Filing", deploy)
        self.assertIn('HTTP_STATUS="${HTTP_PROBE##*$', deploy)
        self.assertLess(
            deploy.index("Non-compliance ICP Filing"),
            deploy.index("docker compose run --rm certbot certonly"),
        )

    def test_deploy_requires_root_before_any_command_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            result = subprocess.run(
                [str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be run as root", result.stderr)
            self.assertFalse(log.exists() and log.read_text(encoding="utf-8").strip())

    def test_existing_certificate_skips_http_bootstrap_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            certificate = root / "certbot/conf/live/wasp.hydroclimatex.com/fullchain.pem"
            certificate.parent.mkdir(parents=True)
            certificate.touch()
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertNotIn("NGINX_CONFIG=nginx.bootstrap.conf", calls)
            self.assertNotIn("certbot certonly", calls)
            self.assertIn("args=compose up -d --build wasp-api", calls)
            self.assertIn("NGINX_CONFIG=nginx.conf", calls)

    def test_certbot_failure_stops_bootstrap_proxy_without_final_tls_switch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_CERTBOT_EXIT"] = "1"
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("NGINX_CONFIG=nginx.bootstrap.conf", calls)
            self.assertIn("certbot certonly", calls)
            self.assertIn("docker NGINX_CONFIG= args=compose stop nginx", calls)
            self.assertNotIn("NGINX_CONFIG=nginx.conf", calls)

    def test_icp_blocking_exits_before_any_docker_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_CURL_RESPONSE"] = "Non-compliance ICP Filing\\n403"
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Non-compliance ICP Filing/403", result.stderr)
            calls = log.read_text(encoding="utf-8")
            self.assertNotIn("docker ", calls)

    def test_bootstrap_nginx_serves_only_acme_and_returns_503_otherwise(self) -> None:
        bootstrap = read("nginx.bootstrap.conf")

        self.assertIn("/.well-known/acme-challenge/", bootstrap)
        self.assertIn("return 503", bootstrap)
        self.assertNotIn("proxy_pass", bootstrap)
        self.assertNotIn("upstream", bootstrap)

    def test_frontend_escapes_api_column_names_before_inner_html(self) -> None:
        source = read("showcase/wasp-web/index.html")

        self.assertIn("function escapeHTML(value)", source)
        self.assertIn("${escapeHTML(target_column)}", source)
        self.assertIn("${predictor_columns.map(escapeHTML).join(', ')}", source)
        self.assertIn("${escapeHTML(firstPred)}", source)
        self.assertIn("${escapeHTML(col)}", source)
        self.assertIn("${escapeHTML(c.predictor)}", source)

    def test_baseline_scalers_fit_training_data_and_transform_test_data(self) -> None:
        source = read("backend/wasp/prediction.py")

        self.assertRegex(source, r"baseline_scaler_X\s*=\s*StandardScaler\(\)")
        self.assertRegex(
            source,
            r"X_train_raw_scaled\s*=\s*baseline_scaler_X\.fit_transform\(X_train_raw\)",
        )
        self.assertRegex(
            source,
            r"X_test_raw_scaled\s*=\s*baseline_scaler_X\.transform\(X_test_raw\)",
        )
        self.assertRegex(source, r"baseline_scaler_y\s*=\s*StandardScaler\(\)")
        self.assertRegex(
            source,
            r"y_train_raw_scaled\s*=\s*baseline_scaler_y\.fit_transform\(",
        )
        self.assertIn("baseline_model.fit(\n        X_train_raw_scaled,\n        y_train_raw_scaled", source)
        self.assertIn("baseline_scaler_y.inverse_transform", source)
        self.assertNotIn("StandardScaler().fit_transform(X_test_raw)", source)

    def test_deployment_sources_are_trackable_but_runtime_state_is_ignored(self) -> None:
        ignored = read(".gitignore")

        self.assertNotIn("backend/", ignored)
        self.assertNotIn("docker-compose.yml", ignored)
        self.assertNotIn("deploy.sh", ignored)
        self.assertNotIn("nginx.conf", ignored)
        self.assertNotIn(".gitignore", ignored)
        for entry in ("certbot/conf/", "certbot/www/", "certs/", ".env", ".ecs_ip"):
            self.assertIn(entry, ignored)


if __name__ == "__main__":
    unittest.main()
