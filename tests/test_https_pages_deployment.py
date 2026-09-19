"""Regression checks for the public Pages site and HTTPS WASP API deployment.

These tests deliberately inspect deployment sources so they run with only the
Python standard library in CI and on a fresh server checkout.
"""

from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def write_command_stub(directory: Path, command: str) -> None:
    """Write a harmless command stub used to exercise deploy sequencing."""
    body = f"""#!/usr/bin/env bash
printf '{command} NGINX_CONFIG=%s args=%s\\n' "${{NGINX_CONFIG:-}}" "$*" >> "$WASP_TEST_LOG"
case '{command}' in
  curl)
    if [[ "$*" == *"https://wasp.hydroclimatex.com/api/health"* ]]; then
      live="$WASP_STATE_DIR/conf/live/wasp.hydroclimatex.com"
      if [[ ! -s "$live/fullchain.pem" || ! -s "$live/privkey.pem" ]]; then
        exit 1
      fi
      count=0
      if [[ -f "$WASP_HEALTH_COUNT_FILE" ]]; then count="$(<"$WASP_HEALTH_COUNT_FILE")"; fi
      count=$((count + 1))
      printf '%s' "$count" > "$WASP_HEALTH_COUNT_FILE"
      if [[ "$count" -eq 1 ]]; then
        printf '%s' "${{WASP_HTTPS_HEALTH_RESPONSE:-healthy}}"
        exit "${{WASP_HTTPS_HEALTH_EXIT:-0}}"
      fi
      printf '%s' "${{WASP_ROLLBACK_HEALTH_RESPONSE:-healthy}}"
      exit "${{WASP_ROLLBACK_HEALTH_EXIT:-0}}"
    fi
    printf '%b' "${{WASP_CURL_RESPONSE:-$'\\n200'}}"
    ;;
  getent) printf '8.210.252.61 STREAM wasp.hydroclimatex.com\n'; exit 0 ;;
  dig)
    if [[ "$*" == *" AAAA "* ]]; then
      printf '%b' "${{WASP_DIG_AAAA-}}"
    else
      printf '%b' "${{WASP_DIG_A-8.210.252.61\\n}}"
    fi
    ;;
  openssl)
    if [[ "$*" == *"-checkend"* ]]; then
      if [[ "${{WASP_CERT_EXPIRED:-0}}" -eq 1 ]]; then
        count=0
        if [[ -f "$WASP_OPENSSL_CHECK_COUNT_FILE" ]]; then count="$(<"$WASP_OPENSSL_CHECK_COUNT_FILE")"; fi
        count=$((count + 1))
        printf '%s' "$count" > "$WASP_OPENSSL_CHECK_COUNT_FILE"
        if [[ "$count" -eq 1 ]]; then exit 1; fi
      fi
      exit 0
    fi
    if [[ "$*" == *"x509"*"-pubkey"* ]]; then printf 'public-key'; exit 0; fi
    if [[ "$*" == *"pkey"*"-pubout"* ]]; then
      if [[ -n "${{WASP_PRIVATE_PUBLIC_KEY:-}}" ]]; then
        count=0
        if [[ -f "$WASP_KEY_CHECK_COUNT_FILE" ]]; then count="$(<"$WASP_KEY_CHECK_COUNT_FILE")"; fi
        count=$((count + 1))
        printf '%s' "$count" > "$WASP_KEY_CHECK_COUNT_FILE"
        if [[ "$count" -eq 1 ]]; then printf '%s' "$WASP_PRIVATE_PUBLIC_KEY"; exit 0; fi
      fi
      printf 'public-key'; exit 0
    fi
    exit 0
    ;;
  docker)
    if [[ "$*" == *"State.Health.Status"*"wasp-api"* ]]; then
      printf '%s\n' "${{WASP_OLD_API_HEALTH:-healthy}}"; exit 0
    fi
    if [[ "$*" == *"State.Health.Status"*"wasp-nginx"* ]]; then
      printf '%s\n' "${{WASP_OLD_NGINX_HEALTH:-healthy}}"; exit 0
    fi
    if [[ "$*" == *"inspect"*"wasp-api"* ]]; then
      [[ -n "${{WASP_OLD_API_IMAGE:-}}" ]] || exit 1
      printf '%s\n' "$WASP_OLD_API_IMAGE"; exit 0
    fi
    if [[ "$*" == *"inspect"*"wasp-nginx"* ]]; then
      [[ -n "${{WASP_OLD_NGINX_IMAGE:-}}" ]] || exit 1
      printf '%s\n' "$WASP_OLD_NGINX_IMAGE"; exit 0
    fi
    if [[ "${{WASP_ROLLBACK:-0}}" == "1" && "$*" == *"compose up"* ]]; then
      exit "${{WASP_ROLLBACK_EXIT:-0}}"
    fi
    if [[ "$NGINX_CONFIG" == "nginx.bootstrap.conf" && "$*" == *"compose up"* ]]; then
      exit "${{WASP_BOOTSTRAP_NGINX_EXIT:-0}}"
    fi
    if [[ "$*" == *"certbot certonly"* ]]; then
      status="${{WASP_CERTBOT_EXIT:-0}}"
      if [[ "$status" -eq 0 || "${{WASP_CERTBOT_LEAVE_PARTIAL:-0}}" -eq 1 ]]; then
        live="$WASP_STATE_DIR/conf/live/wasp.hydroclimatex.com"
        mkdir -p "$live"
        printf '%s\n' "${{WASP_REPLACEMENT_CERT_CONTENT:-certificate}}" > "$live/fullchain.pem"
      fi
      if [[ "$status" -eq 0 ]]; then
        printf 'private-key\n' > "$live/privkey.pem"
      fi
      exit "$status"
    fi
    if [[ "$*" == *"certbot renew"* ]]; then
      exit "${{WASP_CERTBOT_RENEW_EXIT:-0}}"
    fi
    if [[ "$NGINX_CONFIG" == "nginx.conf" && "$*" == *"compose up"* ]]; then
      exit "${{WASP_TLS_NGINX_EXIT:-0}}"
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
    for command in ("docker", "systemctl", "nginx", "getent", "dig", "openssl", "curl"):
        write_command_stub(stub_directory, command)
    log = root / "calls.log"
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{stub_directory}{os.pathsep}{environment['PATH']}",
            "WASP_TEST_LOG": str(log),
            "WASP_RENEWAL_SCRIPT": str(root / "renew-wasp-cert"),
            "WASP_CRON_FILE": str(root / "wasp-cert-renew"),
            "WASP_STATE_DIR": str(root / "state"),
            "WASP_HEALTH_COUNT_FILE": str(root / "health-count"),
            "WASP_OPENSSL_CHECK_COUNT_FILE": str(root / "openssl-check-count"),
            "WASP_KEY_CHECK_COUNT_FILE": str(root / "key-check-count"),
        }
    )
    return environment, log


def create_valid_test_certificate(root: Path) -> Path:
    live = root / "state/conf/live/wasp.hydroclimatex.com"
    live.mkdir(parents=True, exist_ok=True)
    (live / "fullchain.pem").write_text("certificate\n", encoding="utf-8")
    (live / "privkey.pem").write_text("private-key\n", encoding="utf-8")
    return live / "fullchain.pem"


class HttpsPagesDeploymentTests(unittest.TestCase):
    def test_three_public_sites_have_distinct_roles_and_links(self) -> None:
        homepage = read("index.html")
        introduction = read("showcase/wasp-web/index.html")

        self.assertGreaterEqual(homepage.count('href="/showcase/wasp-web/"'), 2)
        self.assertIn('href="https://wasp.hydroclimatex.com"', homepage)
        self.assertNotIn("<iframe", homepage)
        self.assertIn("WASP Overview", introduction)
        for step in ("Decompose", "Identify", "Modulate", "Reconstruct"):
            self.assertIn(step, introduction)
        for section in ("CSV input", "Run parameters", "R", "Python", "MATLAB"):
            self.assertIn(section, introduction)
        self.assertIn(
            "https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2019WR026962",
            introduction,
        )
        self.assertIn(
            "https://www.sciencedirect.com/science/article/pii/S1364815220309646",
            introduction,
        )
        self.assertIn('href="https://wasp.hydroclimatex.com"', introduction)

    def test_interactive_app_uses_same_origin_and_localhost_fallback(self) -> None:
        source = read("wasp-app/index.html")

        self.assertIn("window.WASP_API_BASE", source)
        self.assertIn("http://localhost:8000", source)
        self.assertRegex(source, r"(?s):\s*''\s*\);")
        self.assertIn("API_BASE + '/api/demo-data'", source)
        self.assertIn("API_BASE + '/api/wasp/predict'", source)
        self.assertIn(
            'href="https://hydroclimatex.com/showcase/wasp-web/"', source
        )
        self.assertNotIn("http://121.41.164.89", source)

    def test_web_sources_contain_no_old_backend_ip_or_mixed_content(self) -> None:
        web_sources = "\n".join(
            read(path)
            for path in (
                "index.html",
                "main.js",
                "style.css",
                "showcase/wasp-web/index.html",
                "wasp-app/index.html",
            )
        )
        self.assertNotIn("121.41.164.89", web_sources)
        self.assertNotIn("http://wasp.hydroclimatex.com", web_sources)

    def test_fastapi_cors_allows_production_pages_origin(self) -> None:
        source = read("backend/app.py")

        self.assertIn('"https://hydroclimatex.com"', source)
        self.assertIn('"https://wasp.hydroclimatex.com"', source)
        self.assertNotIn("github\\.io", source)
        self.assertIn("allow_credentials=False", source)
        self.assertIn('allow_methods=["GET", "POST", "OPTIONS"]', source)
        self.assertIn('allow_headers=["Content-Type", "Accept"]', source)

    def test_fastapi_public_contract_is_namespaced_under_api(self) -> None:
        source = read("backend/app.py")

        self.assertIn('docs_url="/api/docs"', source)
        self.assertIn('redoc_url="/api/redoc"', source)
        self.assertIn('openapi_url="/api/openapi.json"', source)
        self.assertIn('@app.get("/api/health")', source)
        self.assertIn('@app.get("/api/demo-data")', source)
        self.assertIn('@app.post("/api/wasp/predict")', source)
        self.assertNotIn('@app.get("/")', source)
        self.assertIn("WASP_MAX_UPLOAD_MB", source)
        self.assertIn("except (TypeError, ValueError) as error:", source)
        self.assertIn('"message": f"Invalid WASP input: {error}"', source)
        self.assertIn("run_in_threadpool", source)
        self.assertIn("asyncio.Semaphore(1)", source)

    def test_api_container_runs_exactly_one_worker(self) -> None:
        dockerfile = read("backend/Dockerfile")

        self.assertIn("FROM python:3.11-slim-bookworm", dockerfile)
        self.assertIn("https://mirrors.aliyun.com", dockerfile)
        self.assertNotIn("http://mirrors.aliyun.com", dockerfile)
        self.assertIn('"--workers", "1"', dockerfile)
        self.assertIn("http://localhost:8000/api/health", dockerfile)

    def test_pages_artifact_stages_required_files_and_optional_assets(self) -> None:
        workflow = read(".github/workflows/static.yml")

        self.assertRegex(workflow, r"cp\s+index\.html\s+main\.js\s+style\.css\s+_site/")
        self.assertRegex(workflow, r"cp\s+-r\s+showcase\s+data\s+figs\s+_site/")
        self.assertRegex(workflow, r"if\s+\[\s+-d\s+assets\s+\]")
        for path in ("_site/index.html", "_site/showcase/wasp-web/index.html"):
            self.assertIn(f"test -f {path}", workflow)
        self.assertIn('test -f "_site/figs/Flood&Drought.jpeg"', workflow)
        self.assertIn("test ! -e _site/wasp-app", workflow)
        self.assertNotRegex(workflow, r"cp\s+-r\s+wasp-app")

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
            for page in ("wasp-web", "wqm-web", "synthesis-web"):
                (root / "showcase" / page).mkdir(parents=True)
                (root / "showcase" / page / "index.html").touch()
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
        self.assertIn("return 301 https://wasp.hydroclimatex.com$request_uri", nginx)
        self.assertNotIn("return 301 https://$host$request_uri", nginx)
        self.assertIn("client_max_body_size 11M", nginx)
        self.assertIn("proxy_read_timeout 120s", nginx)
        self.assertIn("root /usr/share/nginx/html", nginx)
        self.assertRegex(nginx, r"(?s)location / \{.*?try_files \$uri \$uri/ /index\.html;")
        self.assertRegex(nginx, r"(?s)location /api/ \{.*?proxy_pass http://wasp_backend;")
        self.assertNotIn("proxy_pass http://wasp_backend/;", nginx)
        self.assertNotIn("add_header Access-Control-Allow", nginx)
        self.assertIn("limit_req_zone", nginx)
        self.assertIn("limit_conn_zone", nginx)
        self.assertIn("zone=wasp_predict_global", nginx)
        self.assertIn("limit_conn wasp_predict_global 1", nginx)
        self.assertRegex(
            nginx,
            r"(?s)location = /api/wasp/predict \{.*?limit_req .*?limit_conn",
        )
        self.assertIn("listen 80 default_server", nginx)
        self.assertIn("listen 443 ssl default_server", nginx)
        self.assertIn("return 444", nginx)
        self.assertIn("/.well-known/acme-challenge/", bootstrap)
        self.assertIn("server_name wasp.hydroclimatex.com", bootstrap)
        self.assertIn("listen 80 default_server", bootstrap)

    def test_compose_hides_api_port_serves_baked_images_and_rotates_logs(self) -> None:
        compose = read("docker-compose.yml")

        self.assertNotRegex(compose, r"(?m)^version:")
        self.assertIn("expose:\n      - \"8000\"", compose)
        self.assertNotIn('"8000:8000"', compose)
        self.assertIn('"${NGINX_HTTP_PUBLISH:-0.0.0.0:80}:80"', compose)
        self.assertIn('"${NGINX_HTTPS_PUBLISH:-0.0.0.0:443}:443"', compose)
        self.assertIn("image: hydroclimatex/wasp-api:current", compose)
        self.assertIn("image: hydroclimatex/wasp-nginx:current", compose)
        self.assertRegex(compose, r"(?s)nginx:.*?build:.*?dockerfile: nginx/Dockerfile")
        self.assertNotIn("nginx.conf:/etc/nginx", compose)
        self.assertNotIn("./wasp-app:/usr/share/nginx/html", compose)
        self.assertIn("${WASP_STATE_DIR:-./certbot}/www:/var/www/certbot", compose)
        self.assertIn("${WASP_STATE_DIR:-./certbot}/conf:/etc/letsencrypt", compose)
        self.assertIn("certbot:", compose)
        self.assertGreaterEqual(compose.count('driver: "json-file"'), 2)
        self.assertGreaterEqual(compose.count('max-size: "10m"'), 2)
        self.assertGreaterEqual(compose.count('max-file: "3"'), 2)
        self.assertEqual(compose.count("restart: unless-stopped"), 3)
        self.assertIn("NGINX_CONFIG=${NGINX_CONFIG:-nginx.conf}", compose)
        self.assertIn("wasp-bootstrap-ready", compose)
        self.assertIn("https://127.0.0.1/api/health", compose)
        dockerfile = read("nginx/Dockerfile")
        entrypoint = read("nginx/select-config.sh")
        self.assertIn("COPY wasp-app", dockerfile)
        self.assertIn("COPY nginx.conf", dockerfile)
        self.assertIn("COPY nginx.bootstrap.conf", dockerfile)
        self.assertIn("NGINX_CONFIG", entrypoint)

    def test_deploy_bootstraps_tls_without_exposing_api_port(self) -> None:
        deploy = read("deploy.sh")

        self.assertIn("ze.jiang@hhu.edu.cn", deploy)
        self.assertIn("wasp.hydroclimatex.com", deploy)
        self.assertIn("certbot certonly --webroot", deploy)
        self.assertIn("docker compose exec -T nginx nginx -s reload", deploy)
        self.assertNotIn("8000/tcp", deploy)
        self.assertNotIn("WASP_BACKEND_URL", deploy)
        self.assertIn('EXPECTED_IP="8.210.252.61"', deploy)
        self.assertIn('APP_ROOT="/opt/hydroclimatex-wasp"', deploy)
        self.assertIn('WASP_STATE_DIR="$APP_ROOT/state"', deploy)
        self.assertIn('export WASP_STATE_DIR', deploy)
        self.assertIn("wasp-bootstrap-ready", deploy)
        self.assertIn("--wait --wait-timeout", deploy)
        self.assertIn("--resolve", deploy)
        self.assertIn("https://$DOMAIN/api/health", deploy)
        self.assertIn("docker inspect", deploy)
        self.assertIn("docker image tag", deploy)
        self.assertIn("hydroclimatex/wasp-api:current", deploy)
        self.assertIn("hydroclimatex/wasp-nginx:current", deploy)
        self.assertIn("--no-build", deploy)

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

    def test_generated_renewal_script_reloads_nginx_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            deploy_result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(deploy_result.returncode, 0, deploy_result.stdout + deploy_result.stderr)
            log.write_text("", encoding="utf-8")

            renewal_result = subprocess.run(
                ["bash", str(root / "renew-wasp-cert")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(renewal_result.returncode, 0, renewal_result.stdout + renewal_result.stderr)
            calls = log.read_text(encoding="utf-8")
            renew = "args=compose run --rm certbot renew --webroot --webroot-path /var/www/certbot"
            reload_nginx = "args=compose exec -T nginx nginx -s reload"
            self.assertIn(renew, calls)
            self.assertIn(reload_nginx, calls)
            self.assertLess(calls.index(renew), calls.index(reload_nginx))

            renewal = (root / "renew-wasp-cert").read_text(encoding="utf-8")
            self.assertIn("systemctl is-active --quiet nginx", renewal)
            self.assertIn("nginx -t", renewal)
            self.assertIn("systemctl reload nginx", renewal)

    def test_generated_renewal_script_does_not_reload_after_renew_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            deploy_result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertEqual(deploy_result.returncode, 0, deploy_result.stdout + deploy_result.stderr)
            log.write_text("", encoding="utf-8")
            environment["WASP_CERTBOT_RENEW_EXIT"] = "1"

            renewal_result = subprocess.run(
                ["bash", str(root / "renew-wasp-cert")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(renewal_result.returncode, 0)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("certbot renew", calls)
            self.assertNotIn("nginx -s reload", calls)

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
            create_valid_test_certificate(root)
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
            self.assertIn("args=compose build wasp-api nginx", calls)
            self.assertIn("args=compose up -d --no-build --force-recreate --wait", calls)
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

    def test_tls_switch_failure_stops_http_bootstrap_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_TLS_NGINX_EXIT"] = "1"
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
            self.assertIn("NGINX_CONFIG=nginx.conf", calls)
            self.assertIn("docker NGINX_CONFIG= args=compose stop nginx", calls)

    def test_https_health_probe_success_completes_deployment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)

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
            self.assertIn("--resolve wasp.hydroclimatex.com:443:127.0.0.1", calls)
            self.assertIn("https://wasp.hydroclimatex.com/api/health", calls)
            self.assertIn("WASP is available", result.stdout)

    def test_https_health_probe_failure_stops_nginx_and_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            environment["WASP_HTTPS_HEALTH_EXIT"] = "1"

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
            self.assertIn("https://wasp.hydroclimatex.com/api/health", calls)
            self.assertIn("docker NGINX_CONFIG= args=compose stop nginx", calls)
            self.assertNotIn("WASP is available", result.stdout)

    def test_bootstrap_container_failure_runs_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_BOOTSTRAP_NGINX_EXIT"] = "1"
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
            self.assertIn("docker NGINX_CONFIG= args=compose stop nginx", calls)
            self.assertNotIn("certbot certonly", calls)

    def test_candidate_health_failure_rolls_back_both_existing_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            environment.update(
                {
                    "WASP_OLD_API_IMAGE": "sha256:old-api",
                    "WASP_OLD_NGINX_IMAGE": "sha256:old-nginx",
                    "WASP_HTTPS_HEALTH_EXIT": "1",
                }
            )

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
            self.assertIn("image tag sha256:old-api hydroclimatex/wasp-api:current", calls)
            self.assertIn("image tag sha256:old-nginx hydroclimatex/wasp-nginx:current", calls)
            self.assertIn("--no-build --force-recreate --wait", calls)
            self.assertNotIn("args=compose stop nginx", calls)
            self.assertIn("Previous WASP deployment restored", result.stdout)

    def test_failed_rollback_stops_nginx_and_preserves_original_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            environment.update(
                {
                    "WASP_OLD_API_IMAGE": "sha256:old-api",
                    "WASP_OLD_NGINX_IMAGE": "sha256:old-nginx",
                    "WASP_HTTPS_HEALTH_EXIT": "1",
                    "WASP_ROLLBACK_EXIT": "1",
                }
            )

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
            self.assertIn("image tag sha256:old-api", calls)
            self.assertIn("image tag sha256:old-nginx", calls)
            self.assertIn("args=compose stop nginx", calls)

    def test_expiring_certificate_is_restored_when_bootstrap_candidate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            certificate = create_valid_test_certificate(root)
            certificate.write_text("old-expiring-certificate\n", encoding="utf-8")
            archive = root / "state/conf/archive/wasp.hydroclimatex.com"
            archive.mkdir(parents=True)
            (archive / "cert1.pem").write_text("old-archive\n", encoding="utf-8")
            renewal = root / "state/conf/renewal/wasp.hydroclimatex.com.conf"
            renewal.parent.mkdir(parents=True)
            renewal.write_text("old-renewal\n", encoding="utf-8")
            environment.update(
                {
                    "WASP_CERT_EXPIRED": "1",
                    "WASP_OLD_API_IMAGE": "sha256:old-api",
                    "WASP_OLD_NGINX_IMAGE": "sha256:old-nginx",
                    "WASP_BOOTSTRAP_NGINX_EXIT": "1",
                }
            )

            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(certificate.read_text(encoding="utf-8"), "old-expiring-certificate\n")
            self.assertEqual((archive / "cert1.pem").read_text(encoding="utf-8"), "old-archive\n")
            self.assertEqual(renewal.read_text(encoding="utf-8"), "old-renewal\n")
            calls = log.read_text(encoding="utf-8")
            self.assertIn("image tag sha256:old-api hydroclimatex/wasp-api:current", calls)
            self.assertIn("image tag sha256:old-nginx hydroclimatex/wasp-nginx:current", calls)
            self.assertNotIn("args=compose stop nginx", calls)
            self.assertIn("Previous WASP deployment restored", result.stdout)

    def test_failed_certbot_partial_state_is_saved_before_old_cert_restore(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            certificate = create_valid_test_certificate(root)
            certificate.write_text("old-expiring-certificate\n", encoding="utf-8")
            environment.update(
                {
                    "WASP_CERT_EXPIRED": "1",
                    "WASP_OLD_API_IMAGE": "sha256:old-api",
                    "WASP_OLD_NGINX_IMAGE": "sha256:old-nginx",
                    "WASP_CERTBOT_EXIT": "1",
                    "WASP_CERTBOT_LEAVE_PARTIAL": "1",
                    "WASP_REPLACEMENT_CERT_CONTENT": "failed-replacement-certificate",
                }
            )

            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(certificate.read_text(encoding="utf-8"), "old-expiring-certificate\n")
            failed_roots = list((root / "state/recovery").glob("failed-replacement-*"))
            self.assertEqual(len(failed_roots), 1)
            failed_certificate = next(failed_roots[0].rglob("fullchain.pem"))
            self.assertEqual(
                failed_certificate.read_text(encoding="utf-8"),
                "failed-replacement-certificate\n",
            )
            calls = log.read_text(encoding="utf-8")
            self.assertIn("image tag sha256:old-api hydroclimatex/wasp-api:current", calls)
            self.assertNotIn("args=compose stop nginx", calls)
            self.assertIn("Previous WASP deployment restored", result.stdout)

    def test_unhealthy_previous_containers_are_not_used_for_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            environment.update(
                {
                    "WASP_OLD_API_IMAGE": "sha256:old-api",
                    "WASP_OLD_NGINX_IMAGE": "sha256:old-nginx",
                    "WASP_OLD_API_HEALTH": "unhealthy",
                    "WASP_HTTPS_HEALTH_EXIT": "1",
                }
            )

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
            self.assertNotIn("docker NGINX_CONFIG= args=image tag", calls)
            self.assertIn("args=compose stop nginx", calls)

    def test_missing_private_key_is_recovered_before_fresh_issuance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            live = root / "state/conf/live/wasp.hydroclimatex.com"
            live.mkdir(parents=True)
            (live / "fullchain.pem").write_text("broken-certificate\n", encoding="utf-8")

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
            self.assertIn("NGINX_CONFIG=nginx.bootstrap.conf", calls)
            self.assertIn("certbot certonly", calls)
            self.assertTrue((live / "privkey.pem").is_file())
            recovery = root / "state/recovery"
            self.assertTrue(any(recovery.rglob("fullchain.pem")))

    def test_expiring_certificate_state_is_moved_to_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            archive = root / "state/conf/archive/wasp.hydroclimatex.com"
            archive.mkdir(parents=True)
            (archive / "cert1.pem").write_text("old\n", encoding="utf-8")
            renewal = root / "state/conf/renewal"
            renewal.mkdir(parents=True)
            (renewal / "wasp.hydroclimatex.com.conf").write_text("old\n", encoding="utf-8")
            environment["WASP_CERT_EXPIRED"] = "1"

            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("certbot certonly", log.read_text(encoding="utf-8"))
            recovery = root / "state/recovery"
            self.assertTrue(any(recovery.rglob("cert1.pem")))
            self.assertTrue(any(recovery.rglob("wasp.hydroclimatex.com.conf")))

    def test_mismatched_certificate_key_is_recovered_before_issuance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            create_valid_test_certificate(root)
            environment["WASP_PRIVATE_PUBLIC_KEY"] = "different-public-key"

            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("certbot certonly", log.read_text(encoding="utf-8"))
            recovery = root / "state/recovery"
            self.assertTrue(any(recovery.rglob("privkey.pem")))

    def test_dns_rejects_extra_a_record_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_DIG_A"] = "8.210.252.61\\n203.0.113.8\\n"
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly", result.stderr)
            self.assertNotIn("docker ", log.read_text(encoding="utf-8"))

    def test_dns_rejects_missing_a_record_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_DIG_A"] = ""
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly", result.stderr)
            self.assertNotIn("docker ", log.read_text(encoding="utf-8"))

    def test_dns_rejects_aaaa_record_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            environment, log = deploy_test_environment(root)
            environment["WASP_DIG_AAAA"] = "2001:db8::1\\n"
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; run_deployment', "bash", str(root / "deploy.sh")],
                cwd=root,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("AAAA", result.stderr)
            self.assertNotIn("docker ", log.read_text(encoding="utf-8"))

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
        source = read("wasp-app/index.html")

        self.assertIn("function escapeHTML(value)", source)
        self.assertIn("${escapeHTML(target_column)}", source)
        self.assertIn("${predictor_columns.map(escapeHTML).join(', ')}", source)
        # Predictor names are injected in the band-energy and modulation panels.
        self.assertIn("${escapeHTML(col)}", source)
        for supported in ("db1", "db2", "db4", "db8", "db16"):
            self.assertRegex(source, rf'<option value="{supported}"(?: selected)?>')
        self.assertNotIn('<option value="sym8">', source)
        self.assertNotIn('<option value="coif3">', source)
        self.assertNotIn('<option value="dmey">', source)

    def test_frontend_handles_non_json_proxy_errors_without_json_parsing(self) -> None:
        script = textwrap.dedent(
            """
            const assert = require('assert');
            const { parseWaspResponse } = require('./wasp-app/response.js');

            function response(status, contentType, payload) {
              let jsonCalls = 0;
              return {
                value: {
                  ok: status >= 200 && status < 300,
                  status,
                  headers: { get: () => contentType },
                  json: async () => { jsonCalls += 1; return payload; },
                },
                calls: () => jsonCalls,
              };
            }

            (async () => {
              const good = response(200, 'application/json; charset=utf-8', {success: true});
              assert.deepStrictEqual(await parseWaspResponse(good.value), {success: true});
              assert.strictEqual(good.calls(), 1);

              for (const [status, expected] of [
                [413, 'File too large'],
                [429, 'busy'],
                [502, 'HTTP 502'],
              ]) {
                const proxy = response(status, 'text/html', '<html>proxy error</html>');
                await assert.rejects(() => parseWaspResponse(proxy.value), new RegExp(expected, 'i'));
                assert.strictEqual(proxy.calls(), 0);
              }
            })().catch(error => { console.error(error); process.exit(1); });
            """
        )
        result = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        app = read("wasp-app/index.html")
        self.assertIn('src="response.js"', app)
        self.assertIn("parseWaspResponse(resp)", app)

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
        for entry in ("certbot/conf/", "certbot/www/", "certs/", ".env", ".ecs_ip"):
            self.assertIn(entry, ignored)

    def test_ubuntu_bootstrap_is_idempotent_and_deploys_feature_branch(self) -> None:
        bootstrap = read("scripts/bootstrap-hk-server.sh")

        self.assertIn("Ubuntu 24.04", bootstrap)
        self.assertIn('APP_ROOT="/opt/hydroclimatex-wasp"', bootstrap)
        self.assertIn('REPO_DIR="$APP_ROOT/repo"', bootstrap)
        self.assertIn('STATE_DIR="$APP_ROOT/state"', bootstrap)
        self.assertIn('DEPLOY_BRANCH="${WASP_DEPLOY_BRANCH:-codex/wasp-hong-kong}"', bootstrap)
        for package in (
            "ca-certificates", "curl", "git", "cron", "dnsutils", "openssl",
            "docker-ce", "docker-compose-plugin",
        ):
            self.assertIn(package, bootstrap)
        self.assertIn("git clone", bootstrap)
        self.assertIn("git fetch", bootstrap)
        self.assertIn("bash ./deploy.sh", bootstrap)

    def test_single_branch_checkout_can_switch_to_main_with_explicit_refspec(self) -> None:
        def git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
            result = subprocess.run(
                ["git", *arguments],
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return result

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            origin = root / "origin.git"
            seed = root / "seed"
            checkout = root / "checkout"
            origin.mkdir()
            seed.mkdir()
            git(origin, "init", "--bare")
            git(seed, "init", "-b", "main")
            git(seed, "config", "user.name", "WASP Test")
            git(seed, "config", "user.email", "wasp-test@example.invalid")
            (seed / "branch.txt").write_text("main\n", encoding="utf-8")
            git(seed, "add", "branch.txt")
            git(seed, "commit", "-m", "main")
            git(seed, "remote", "add", "origin", str(origin))
            git(seed, "push", "-u", "origin", "main")
            git(seed, "checkout", "-b", "feature")
            (seed / "branch.txt").write_text("feature\n", encoding="utf-8")
            git(seed, "commit", "-am", "feature")
            git(seed, "push", "-u", "origin", "feature")

            command = (
                'source "$1"; REPO_DIR="$2"; REPO_URL="$3"; '
                'DEPLOY_BRANCH="$4"; sync_repository'
            )
            first = subprocess.run(
                [
                    "bash", "-c", command, "bash",
                    str(ROOT / "scripts/bootstrap-hk-server.sh"),
                    str(checkout), str(origin), "feature",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(git(checkout, "branch", "--show-current").stdout.strip(), "feature")

            second = subprocess.run(
                [
                    "bash", "-c", command, "bash",
                    str(ROOT / "scripts/bootstrap-hk-server.sh"),
                    str(checkout), str(origin), "main",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(git(checkout, "branch", "--show-current").stdout.strip(), "main")
            self.assertEqual((checkout / "branch.txt").read_text(encoding="utf-8"), "main\n")

    def test_plotly_is_vendored_and_runtime_has_no_cdn_dependency(self) -> None:
        app = read("wasp-app/index.html")
        vendor = ROOT / "wasp-app/vendor/plotly-2.35.2.min.js"

        self.assertIn('src="vendor/plotly-2.35.2.min.js"', app)
        self.assertNotIn("cdn.plot.ly", app)
        self.assertTrue(vendor.is_file(), "the pinned Plotly bundle must be vendored")
        self.assertGreater(vendor.stat().st_size, 1_000_000)
        prefix = vendor.read_text(encoding="utf-8", errors="ignore")[:10000]
        self.assertIn("Plotly", prefix)
        license_file = ROOT / "wasp-app/vendor/plotly-2.35.2.min.js.LICENSE.txt"
        self.assertTrue(license_file.is_file())
        self.assertIn("MIT", license_file.read_text(encoding="utf-8"))

    def test_architecture_documents_match_the_three_site_deployment(self) -> None:
        documents = "\n".join(
            read(path)
            for path in (
                "docs/ADR-001-architecture.md",
                "docs/glossary.md",
                "examples/README.md",
            )
        )
        for expected in (
            "https://hydroclimatex.com",
            "https://hydroclimatex.com/showcase/wasp-web/",
            "https://wasp.hydroclimatex.com",
            "Hong Kong Lightweight Application Server",
            "/api/health",
            "scripts/bootstrap-hk-server.sh",
            "deploy.sh",
            "baked Nginx image",
            "automatic rollback",
            "11 MB",
            "10 MiB",
        ):
            self.assertIn(expected, documents)
        for obsolete in ("121.41.164.89", "WebR", "iframe"):
            self.assertNotIn(obsolete, documents)

    def test_wasp_introduction_has_mobile_accessible_navigation(self) -> None:
        introduction = read("showcase/wasp-web/index.html")
        stylesheet = read("style.css")

        self.assertIn('class="menu-button"', introduction)
        self.assertIn('aria-controls="wasp-nav"', introduction)
        self.assertIn('id="wasp-nav"', introduction)
        self.assertIn("classList.toggle('open')", introduction)
        self.assertRegex(
            stylesheet,
            r"(?s)@media \(max-width: 850px\).*?\.site-header\.open nav",
        )

    def test_example_readme_reports_the_actual_demo_row_count(self) -> None:
        with (ROOT / "examples/demo_q.csv").open(encoding="utf-8", newline="") as source:
            actual_rows = sum(1 for _ in csv.DictReader(source))
        readme = read("examples/README.md")
        match = re.search(r"contains\s+(\d+)\s+monthly observations", readme)

        self.assertIsNotNone(match, "README must state the demo observation count")
        self.assertGreaterEqual(actual_rows, 30)
        self.assertLessEqual(actual_rows, 5000)


class StaticResearchToolsTests(unittest.TestCase):
    def test_public_site_links_all_research_tools_and_papers(self) -> None:
        homepage = read("index.html")
        wasp = read("showcase/wasp-web/index.html")
        wqm = read("showcase/wqm-web/index.html")
        synthesis = read("showcase/synthesis-web/index.html")

        for slug, domain in (
            ("wqm", "https://wqm.hydroclimatex.com"),
            ("synthesis", "https://synthesis.hydroclimatex.com"),
        ):
            self.assertIn(f'href="/showcase/{slug}-web/"', homepage)
            self.assertIn(f'href="{domain}"', homepage)
        self.assertIn("Variable transformations in the spectral domain", wasp)
        self.assertIn("S0022169421008660", wasp)
        self.assertIn("Method, software and applications.", wasp)
        self.assertIn("MWR-D-22-0217.1", wqm)
        self.assertIn("https://github.com/HydroclimateX/WQM", wqm)
        self.assertIn("https://cran.r-project.org/package=WQM", wqm)
        self.assertIn("https://wqm.hydroclimatex.com", wqm)
        self.assertIn("https://github.com/HydroclimateX/synthesis", synthesis)
        self.assertIn("https://cran.r-project.org/package=synthesis", synthesis)
        self.assertIn("https://synthesis.hydroclimatex.com", synthesis)
        self.assertIn("https://cranlogs.r-pkg.org/badges/grand-total/WQM", homepage)
        self.assertIn("https://cranlogs.r-pkg.org/badges/grand-total/synthesis", homepage)
        self.assertIn("https://github.com/HydroclimateX/lisflood-web", homepage)


class HostFrontDoorTests(unittest.TestCase):
    DOMAINS = (
        "wasp.hydroclimatex.com",
        "lisflood.hydroclimatex.com",
        "analytics.hydroclimatex.com",
        "telemetry.hydroclimatex.com",
        "wqm.hydroclimatex.com",
        "synthesis.hydroclimatex.com",
    )

    def test_host_frontdoor_routes_only_application_domains(self) -> None:
        config = read("host-nginx/hydroclimatex-apps.conf")
        proxy = read("host-nginx/hydroclimatex-docker-proxy.conf")

        self.assertNotIn("cloud.hydroclimatex.com", config)
        self.assertIn("127.0.0.1:18080", config)
        self.assertIn("127.0.0.1:18443", proxy)
        self.assertIn("proxy_ssl_server_name on", proxy)
        self.assertIn("proxy_ssl_name $host", proxy)
        self.assertIn("proxy_ssl_verify on", proxy)
        self.assertIn("proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt", proxy)
        self.assertIn("proxy_set_header Host $host", proxy)
        self.assertIn("proxy_set_header X-Real-IP $remote_addr", proxy)
        self.assertIn("proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for", proxy)
        for domain in self.DOMAINS:
            self.assertIn(f"server_name {domain}", config)
            self.assertIn(
                f"/opt/hydroclimatex-wasp/state/conf/live/{domain}/fullchain.pem",
                config,
            )
            self.assertIn(
                f"/opt/hydroclimatex-wasp/state/conf/live/{domain}/privkey.pem",
                config,
            )

    def test_container_nginx_trusts_only_private_front_proxy_ranges(self) -> None:
        for path in ("nginx.conf", "nginx.analytics.conf"):
            config = read(path)
            self.assertIn("set_real_ip_from 172.16.0.0/12", config)
            self.assertIn("real_ip_header X-Forwarded-For", config)
            self.assertIn("real_ip_recursive on", config)
            self.assertNotIn("set_real_ip_from 0.0.0.0/0", config)

    def test_frontdoor_deployer_preserves_cloud_and_has_rollback(self) -> None:
        deploy = read("deploy-host-frontdoor.sh")

        for expected in (
            'NGINX_HTTP_PUBLISH="127.0.0.1:18080"',
            'NGINX_HTTPS_PUBLISH="127.0.0.1:18443"',
            'HOST_NGINX_ROOT="${HOST_NGINX_ROOT:-/etc/nginx}"',
            'HOST_SITE_AVAILABLE="$HOST_NGINX_ROOT/sites-available/hydroclimatex-apps"',
            'HOST_PROXY_SNIPPET="$HOST_NGINX_ROOT/snippets/hydroclimatex-docker-proxy.conf"',
            'RENEWAL_SCRIPT="${WASP_RENEWAL_SCRIPT:-/usr/local/sbin/renew-wasp-cert}"',
            "docker compose build nginx",
            "docker compose up -d --no-build --force-recreate --wait",
            "PRIOR_NGINX_IMAGE",
            'docker image tag "$PRIOR_NGINX_IMAGE" hydroclimatex/wasp-nginx:current',
            "nginx -t",
            "systemctl reload nginx",
            "rollback",
            "unset NGINX_HTTP_PUBLISH NGINX_HTTPS_PUBLISH",
            "18082",
            "ensure_publish_port_available_or_owned 18080 80",
            "ensure_publish_port_available_or_owned 18443 443",
            'backup_path "$RENEWAL_SCRIPT" renewal',
            'restore_path "$RENEWAL_SCRIPT" renewal',
            "status.php",
        ):
            self.assertIn(expected, deploy)
        self.assertNotIn("systemctl restart nginx", deploy)
        self.assertNotIn("systemctl restart frps", deploy)
        self.assertNotIn("sites-available/default", deploy)
        self.assertTrue(os.access(ROOT / "deploy-host-frontdoor.sh", os.X_OK))

    def test_frontdoor_rollback_restores_env_and_host_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            host = root / "nginx"
            available = host / "sites-available"
            enabled = host / "sites-enabled"
            snippets = host / "snippets"
            for directory in (available, enabled, snippets):
                directory.mkdir(parents=True, exist_ok=True)
            env_file = root / ".env"
            site_file = available / "hydroclimatex-apps"
            enabled_file = enabled / "hydroclimatex-apps"
            snippet_file = snippets / "hydroclimatex-docker-proxy.conf"
            renewal_file = root / "renew-wasp-cert"
            env_file.write_text("KEEP=original\n", encoding="utf-8")
            site_file.write_text("old site\n", encoding="utf-8")
            snippet_file.write_text("old snippet\n", encoding="utf-8")
            renewal_file.write_text("old renewal\n", encoding="utf-8")
            enabled_file.symlink_to(site_file)

            stubs = root / "stubs"
            stubs.mkdir()
            log = root / "calls.log"
            for command in ("docker", "nginx", "systemctl"):
                write_command_stub(stubs, command)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{stubs}{os.pathsep}{environment['PATH']}",
                    "WASP_TEST_LOG": str(log),
                    "HOST_NGINX_ROOT": str(host),
                    "HYDROCLIMATEX_ENV_FILE": str(env_file),
                    "WASP_RENEWAL_SCRIPT": str(renewal_file),
                }
            )
            backup = root / "backup"
            backup.mkdir()
            command = textwrap.dedent(
                """
                source "$1"
                BACKUP_DIR="$2"
                ROLLBACK_ARMED=1
                PRIOR_NGINX_RUNNING=false
                PRIOR_NGINX_IMAGE=old-image-id
                backup_path "$ENV_FILE" env
                backup_path "$HOST_SITE_AVAILABLE" site-available
                backup_path "$HOST_SITE_ENABLED" site-enabled
                backup_path "$HOST_PROXY_SNIPPET" proxy-snippet
                backup_path "$RENEWAL_SCRIPT" renewal
                printf 'changed\n' > "$ENV_FILE"
                printf 'changed\n' > "$HOST_SITE_AVAILABLE"
                printf 'changed\n' > "$HOST_PROXY_SNIPPET"
                printf 'changed\n' > "$RENEWAL_SCRIPT"
                ln -sfn /tmp/changed "$HOST_SITE_ENABLED"
                rollback 7
                """
            )
            result = subprocess.run(
                ["bash", "-c", command, "bash", str(ROOT / "deploy-host-frontdoor.sh"), str(backup)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
            self.assertEqual(env_file.read_text(encoding="utf-8"), "KEEP=original\n")
            self.assertEqual(site_file.read_text(encoding="utf-8"), "old site\n")
            self.assertEqual(snippet_file.read_text(encoding="utf-8"), "old snippet\n")
            self.assertEqual(renewal_file.read_text(encoding="utf-8"), "old renewal\n")
            self.assertTrue(enabled_file.is_symlink())
            self.assertEqual(enabled_file.resolve(), site_file.resolve())
            calls = log.read_text(encoding="utf-8")
            self.assertIn("image tag old-image-id hydroclimatex/wasp-nginx:current", calls)
            self.assertIn("compose stop nginx", calls)
            self.assertIn("nginx NGINX_CONFIG= args=-t", calls)
            self.assertIn("systemctl NGINX_CONFIG= args=reload nginx", calls)

    def test_frontdoor_deployer_stages_loopback_proxy_without_touching_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            host = root / "nginx"
            state = root / "state"
            env_file = root / ".env"
            renewal_file = root / "renew-wasp-cert"
            env_file.write_text("KEEP=original\n", encoding="utf-8")
            for domain in self.DOMAINS:
                live = state / "conf" / "live" / domain
                live.mkdir(parents=True)
                (live / "fullchain.pem").write_text("certificate\n", encoding="utf-8")
                (live / "privkey.pem").write_text("private-key\n", encoding="utf-8")

            stubs = root / "stubs"
            stubs.mkdir()
            log = root / "calls.log"
            for command in ("docker", "nginx", "systemctl", "openssl", "curl"):
                write_command_stub(stubs, command)
            openssl = stubs / "openssl"
            openssl.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'openssl NGINX_CONFIG=%s args=%s\\n' \"${NGINX_CONFIG:-}\" \"$*\" >> \"$WASP_TEST_LOG\"\n"
                "if [[ \"$*\" == *'-checkend'* ]]; then exit 0; fi\n"
                "if [[ \"$*\" == *'x509'*'-pubkey'* ]]; then printf 'public-key'; exit 0; fi\n"
                "if [[ \"$*\" == *'pkey'* ]]; then printf 'public-key'; exit 0; fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            openssl.chmod(0o755)
            ss = stubs / "ss"
            ss.write_text(
                "#!/usr/bin/env bash\nprintf 'LISTEN 0 4096 *:18082 *:*\\n'\n",
                encoding="utf-8",
            )
            ss.chmod(0o755)
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{stubs}{os.pathsep}{environment['PATH']}",
                    "WASP_TEST_LOG": str(log),
                    "WASP_STATE_DIR": str(state),
                    "HOST_NGINX_ROOT": str(host),
                    "HYDROCLIMATEX_ENV_FILE": str(env_file),
                    "WASP_RENEWAL_SCRIPT": str(renewal_file),
                    "FRONTDOOR_ALLOW_NON_ROOT": "1",
                    "WASP_HEALTH_COUNT_FILE": str(root / "health-count"),
                }
            )
            result = subprocess.run(
                [str(ROOT / "deploy-host-frontdoor.sh")],
                cwd=ROOT,
                text=True,
                capture_output=True,
                env=environment,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stdout + result.stderr + log.read_text(encoding="utf-8"),
            )
            env = env_file.read_text(encoding="utf-8")
            self.assertIn("KEEP=original", env)
            self.assertIn("NGINX_HTTP_PUBLISH=127.0.0.1:18080", env)
            self.assertIn("NGINX_HTTPS_PUBLISH=127.0.0.1:18443", env)
            site = host / "sites-available" / "hydroclimatex-apps"
            enabled = host / "sites-enabled" / "hydroclimatex-apps"
            self.assertEqual(site.read_text(encoding="utf-8"), read("host-nginx/hydroclimatex-apps.conf"))
            self.assertTrue(enabled.is_symlink())
            self.assertNotIn("cloud.hydroclimatex.com", site.read_text(encoding="utf-8"))
            renewal = renewal_file.read_text(encoding="utf-8")
            self.assertIn("docker compose run --rm certbot renew", renewal)
            self.assertIn("docker compose exec -T nginx nginx -s reload", renewal)
            self.assertIn("systemctl reload nginx", renewal)
            calls = log.read_text(encoding="utf-8")
            self.assertIn("compose up -d --no-build --force-recreate --wait --wait-timeout 180 nginx", calls)
            self.assertIn("systemctl NGINX_CONFIG= args=reload nginx", calls)
            self.assertNotIn("restart frps", calls)

    def test_frontdoor_runbook_documents_safe_cutover(self) -> None:
        runbook = read("HOST_FRONTDOOR.md")
        environment = read(".env.example")

        for expected in (
            "sudo ./deploy-host-frontdoor.sh",
            "cloud.hydroclimatex.com/status.php",
            "127.0.0.1:18080",
            "127.0.0.1:18443",
            "docker compose config --quiet",
            "nginx -t",
        ):
            self.assertIn(expected, runbook)
        self.assertIn("Do not open", runbook)
        self.assertIn("NGINX_HTTP_PUBLISH=0.0.0.0:80", environment)
        self.assertIn("NGINX_HTTPS_PUBLISH=0.0.0.0:443", environment)

    def test_publications_with_missing_year_are_not_rendered(self) -> None:
        script = read("main.js")

        self.assertIn(".filter(p => Number.isInteger(p.year))", script)
        self.assertNotIn("p.year || 'n/a'", script)

    def test_pages_workflow_checks_new_methodology_pages(self) -> None:
        workflow = read(".github/workflows/static.yml")
        self.assertIn("_site/showcase/wqm-web/index.html", workflow)
        self.assertIn("_site/showcase/synthesis-web/index.html", workflow)

    def test_wqm_demo_data_and_client_contract(self) -> None:
        payload = json.loads(read("interactive-apps/wqm/demo.json"))
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["package"], {"name": "WQM", "version": "0.1.4"})
        self.assertEqual(payload["parameters"]["method"], "QDM")
        self.assertEqual(payload["parameters"]["wavelet"], "morlet")
        self.assertEqual(payload["parameters"]["levels"], 9)
        self.assertEqual(payload["parameters"]["ensembleMembers"], 5)
        self.assertEqual(len(payload["stations"]), 2)
        for station in payload["stations"]:
            columns = station["validation"]
            names = ("date", "observed", "raw", "corrected", "r1", "r2", "r3", "r4", "r5")
            self.assertEqual(set(columns), set(names))
            lengths = {len(columns[name]) for name in names}
            self.assertEqual(len(lengths), 1)
            self.assertGreater(next(iter(lengths)), 100)
            for name in names[1:]:
                self.assertTrue(all(isinstance(value, (int, float)) and math.isfinite(value) for value in columns[name]))
            for name in ("observed", "raw", "corrected", "r1", "r2", "r3", "r4", "r5"):
                self.assertTrue(all(value >= 0 for value in columns[name]))

        html = read("interactive-apps/wqm/index.html")
        script = read("interactive-apps/wqm/app.js")
        for element_id in ("station", "method", "wavelet", "levels", "threshold", "member", "run", "metrics", "timeSeries", "download", "status"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("demo.json", script)
        self.assertIn("Plotly.react", script)

        node = subprocess.run(
            ["node", "-e", textwrap.dedent("""
                const assert = require('assert');
                const fs = require('fs');
                const app = require('./interactive-apps/wqm/app.js');
                const data = JSON.parse(fs.readFileSync('./interactive-apps/wqm/demo.json', 'utf8'));
                assert.strictEqual(app.validateDemo(data).stations.length, 2);
                const station = data.stations[0];
                const result = app.metrics(station.validation.observed, station.validation.raw, station.validation.corrected);
                assert(Number.isFinite(result.raw.rmse));
                assert(Number.isFinite(result.corrected.bias));
                const dryOnly = app.metrics([0, 1], [0.2, 0.9], [0.1, 1], 0.5);
                assert.strictEqual(dryOnly.raw.wetFrequency, 0.5);
                const csv = app.toCsv(station);
                assert(csv.startsWith('date,observed,raw,corrected,r1,r2,r3,r4,r5\\n'));
                assert.strictEqual(csv.trim().split('\\n').length, station.validation.date.length + 1);
            """)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(node.returncode, 0, node.stderr)

    def test_wqm_generation_is_pinned_and_reproducible(self) -> None:
        generator = read("scripts/generate-wqm-demo.R")
        for expected in (
            'packageVersion("WQM") == "0.1.4"',
            "data(\"sample\", package = \"WQM\")",
            "bc_cwt(",
            'QM = "QDM"',
            'wavelet = "morlet"',
            "number_sim = 5",
            "seed = 2021",
            "J <- ncol",
        ):
            self.assertIn(expected, generator)

    def test_synthesis_generators_run_in_node(self) -> None:
        html = read("interactive-apps/synthesis/index.html")
        for element_id in ("model", "samples", "seed", "noise", "modelParameters", "generate", "download", "timeSeries", "phasePlot", "status"):
            self.assertIn(f'id="{element_id}"', html)

        node = subprocess.run(
            ["node", "-e", textwrap.dedent("""
                const assert = require('assert');
                const app = require('./interactive-apps/synthesis/app.js');
                const ar = app.generate('ar1', {n: 100, seed: 7, noise: 0});
                assert.deepStrictEqual(ar, app.generate('ar1', {n: 100, seed: 7, noise: 0}));
                assert.notDeepStrictEqual(ar.columns.x, app.generate('ar1', {n: 100, seed: 8, noise: 0}).columns.x);
                assert.deepStrictEqual(Object.keys(ar.columns), ['index', 'x', 'lag1', 'lag2', 'lag3', 'lag4', 'lag5', 'lag6', 'lag7', 'lag8', 'lag9']);
                const logistic = app.generate('logistic', {n: 100, seed: 1, noise: 0, r: 4, start: 0.2});
                assert(Math.abs(logistic.columns.x[1] - 4 * logistic.columns.x[0] * (1 - logistic.columns.x[0])) < 1e-12);
                const lorenz = app.generate('lorenz', {n: 100, seed: 1, noise: 0, sigma: 10, beta: 8 / 3, rho: 28});
                assert.strictEqual(lorenz.columns.x.length, 100);
                assert(Object.values(lorenz.columns).flat().every(Number.isFinite));
                assert.throws(() => app.generate('ar1', {n: 99, seed: 1, noise: 0}), /100/);
                assert.throws(() => app.generate('logistic', {n: 100, seed: 1, noise: 0, r: 4.1, start: 0.2}), /r/);
                assert(app.toCsv(lorenz).startsWith('time,x,y,z\\n'));
                const stats = app.summary(ar.columns.x);
                assert(['mean', 'standardDeviation', 'minimum', 'maximum'].every(key => Number.isFinite(stats[key])));
            """)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(node.returncode, 0, node.stderr)

    def test_static_tools_are_baked_and_served_with_tls(self) -> None:
        dockerfile = read("nginx/Dockerfile")
        selector = read("nginx/select-config.sh")
        bootstrap = read("nginx.bootstrap.conf")
        nginx = read("nginx/static-tools.conf")
        deploy = read("deploy-static-tools.sh")

        self.assertIn("COPY interactive-apps /usr/share/nginx/tools", dockerfile)
        self.assertIn("COPY nginx/static-tools.conf /opt/wasp/static-tools.conf", dockerfile)
        self.assertIn("cat /opt/wasp/static-tools.conf", selector)
        for domain in ("wqm.hydroclimatex.com", "synthesis.hydroclimatex.com"):
            self.assertIn(domain, bootstrap)
            self.assertIn(f"server_name {domain}", nginx)
            self.assertIn(f"/etc/letsencrypt/live/{domain}/fullchain.pem", nginx)
            self.assertIn(domain, deploy)
            self.assertIn(f'https://$domain/health', deploy)
        self.assertEqual(nginx.count('location = /health { access_log off; return 200 "healthy\\n"; }'), 2)
        self.assertEqual(nginx.count("Content-Security-Policy"), 2)
        self.assertEqual(nginx.count("Strict-Transport-Security"), 2)
        self.assertIn("plotly-2.35.2.min.js", nginx)
        self.assertIn('EXPECTED_IP="8.210.252.61"', deploy)
        self.assertIn("certbot certonly --webroot", deploy)
        self.assertIn("restore_proxy", deploy)
        self.assertIn("docker compose build nginx", deploy)
        self.assertNotIn("wasp-api", deploy)
        self.assertTrue(os.access(ROOT / "deploy-static-tools.sh", os.X_OK))

        checklist = read("STATIC_TOOLS.md")
        self.assertIn("sudo ./deploy-static-tools.sh", checklist)
        self.assertIn("docker compose config --quiet", checklist)


if __name__ == "__main__":
    unittest.main()
