"""Regression checks for the public Pages site and HTTPS WASP API deployment.

These tests deliberately inspect deployment sources so they run with only the
Python standard library in CI and on a fresh server checkout.
"""

from __future__ import annotations

import csv
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
    for command in ("docker", "systemctl", "getent", "dig", "openssl", "curl"):
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
        self.assertIn('"80:80"', compose)
        self.assertIn('"443:443"', compose)
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
        self.assertEqual(compose.count("restart: unless-stopped"), 2)
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
        self.assertIn("${escapeHTML(firstPred)}", source)
        self.assertIn("${escapeHTML(col)}", source)
        self.assertIn("${escapeHTML(item.predictor)}", source)
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
        self.assertNotIn(".gitignore", ignored)
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
        with (ROOT / "examples/demo.csv").open(encoding="utf-8", newline="") as source:
            actual_rows = sum(1 for _ in csv.DictReader(source))
        readme = read("examples/README.md")
        match = re.search(r"contains\s+(\d+)\s+monthly observations", readme)

        self.assertIsNotNone(match, "README must state the demo observation count")
        self.assertEqual(int(match.group(1)), actual_rows)


if __name__ == "__main__":
    unittest.main()
