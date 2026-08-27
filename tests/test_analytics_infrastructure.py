from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_compose_keeps_analytics_datastores_private_and_pinned():
    compose = read("docker-compose.yml")
    assert "postgres:16.4-alpine" in compose
    assert "ghcr.io/umami-software/umami:3.0.1@sha256:fe2c0fd1a7d58ddf4774bf262952f09064178da36653ffe2f4608e9ff92a59fc" in compose
    for service in ("analytics-postgres:", "umami:", "analytics-api:", "analytics-worker:"):
        assert service in compose
    worker_block = compose.split("analytics-worker:", 1)[1].split("wasp-api:", 1)[0]
    assert 'profiles: ["scheduled-reports"]' in worker_block
    analytics_block = compose.split("analytics-postgres:", 1)[1].split("umami:", 1)[0]
    assert "ports:" not in analytics_block
    assert "--no-access-log" in read("analytics/Dockerfile")
    assert "--no-access-log" in read("backend/Dockerfile")
    analytics_image = read("analytics/Dockerfile")
    assert "python:3.11.10-slim-bookworm" in analytics_image
    assert "libnss3" in analytics_image and "libgbm1" in analytics_image
    assert "plotly-2.35.2.min.js" not in analytics_image


def test_nginx_exposes_only_telemetry_collection_routes_and_privacy_safe_logs():
    config = read("nginx.analytics.conf")
    assert "server_name analytics.hydroclimatex.com" in config
    assert "server_name telemetry.hydroclimatex.com" in config
    telemetry = config.split("server_name telemetry.hydroclimatex.com", 2)[-1]
    assert "location = /script.js" in telemetry
    assert "location = /api/send" in telemetry
    assert "location / {\n        return 404;" in telemetry
    log_format = config.split("log_format privacy_safe", 1)[1].split(";", 1)[0]
    assert "$remote_addr" not in log_format
    assert "$http_user_agent" not in log_format
    assert "$request_uri" not in log_format
    assert "$uri" in log_format
    assert "~^https://(www\\.)?hydroclimatex\\.com$" in config
    assert "Access-Control-Allow-Origin $telemetry_cors_origin" in telemetry
    analytics_server = config.split("server_name analytics.hydroclimatex.com", 2)[-1].split("server_name telemetry.hydroclimatex.com", 1)[0]
    assert "location ^~ /internal/ { return 404; }" in analytics_server
    assert "location ^~ /public/ { return 404; }" in analytics_server
    assert "X-Frame-Options DENY" in analytics_server
    assert 'Cache-Control "no-store"' in analytics_server


def test_database_backup_has_thirty_day_retention_and_no_secrets_are_committed():
    backup = read("scripts/backup-analytics-db.sh")
    assert "-mtime +29" in backup
    assert "pg_dump" in backup
    env_example = read(".env.example")
    assert "ANALYTICS_SMTP_AUTHORIZATION_CODE=" in env_example
    assert "ANALYTICS_SMTP_AUTHORIZATION_CODE=changeme" not in env_example
    assert "MAXMIND_LICENSE_KEY" not in env_example
    ignore = read(".gitignore")
    assert ".env" in ignore
    assert "backups/" in ignore


def test_analytics_rollout_has_preflight_migration_certificates_and_gated_scheduler():
    deploy = read("deploy-analytics.sh")
    assert "nproc" in deploy
    assert "MemTotal" not in deploy
    assert '[[ -f "$SCRIPT_DIR/.env" ]] || fail' not in deploy
    assert 'value="${!key:-}"' in deploy
    assert "analytics.hydroclimatex.com" in deploy
    assert "telemetry.hydroclimatex.com" in deploy
    assert "8.210.252.61" in deploy
    assert "analytics_app.cli migrate" in deploy
    assert "analytics_app.cli test-email" in deploy
    assert "backup-analytics-db.sh" in deploy
    assert "analytics-worker" not in deploy
    required_line = next(line for line in deploy.splitlines() if line.startswith("required_keys="))
    for deferred_umami_key in ("UMAMI_API_USERNAME", "UMAMI_API_PASSWORD", "UMAMI_WEBSITE_ID"):
        assert deferred_umami_key not in required_line
    assert "Website Analytics will remain unavailable" in deploy
    enable = read("scripts/enable-analytics-reports.sh")
    assert "smtp-test-verified" in enable
    assert "scheduled-reports" in enable
    assert "analytics-worker" in enable


def test_deploy_wires_umami_initializer_and_0600_secrets():
    deploy = read("deploy-analytics.sh")
    compose = read("docker-compose.yml")
    assert "analytics_app.cli init-umami" in deploy
    assert "chmod 600" in deploy
    assert "UMAMI_API_USERNAME" in deploy
    assert "UMAMI_WEBSITE_ID" in deploy
    assert "UMAMI_ADMIN_USERNAME" in compose
    assert "UMAMI_ADMIN_PASSWORD" in compose


def test_dbip_country_lite_is_keyless_validated_and_wired_into_wasp():
    installer = read("scripts/install-dbip-country-lite.sh")
    compose = read("docker-compose.yml")
    deploy = read("deploy-analytics.sh")
    backend_requirements = read("backend/requirements.txt")
    analytics_requirements = read("analytics-requirements.txt")

    assert "https://download.db-ip.com/free/dbip-country-lite-" in installer
    assert 'date -u +%Y-%m' in installer
    assert "date -u -v-1m +%Y-%m" in installer
    assert "mmdb.gz" in installer
    assert "gzip -dc" in installer
    assert "MMDB metadata marker" in installer
    assert "mv -f" in installer
    assert "dbip-country-lite.mmdb" in installer
    assert "MAXMIND_LICENSE_KEY" not in installer

    assert "ANALYTICS_GEOIP_DATABASE=/opt/geoip/dbip-country-lite.mmdb" in compose
    assert "geoip/dbip-country-lite.mmdb" in deploy
    assert "maxminddb.open_database" in deploy
    assert "docker compose build analytics-api wasp-api nginx" in deploy
    assert "GeoLite2" not in compose + deploy
    assert "maxminddb==3.1.1" in backend_requirements
    assert "geoip2" not in backend_requirements + analytics_requirements


def test_dashboard_attributes_country_geolocation_to_dbip():
    dashboard = read("analytics_app/static/index.html")

    assert '<a href="https://db-ip.com"' in dashboard
    assert "IP Geolocation by DB-IP" in dashboard


def test_dashboard_uses_committed_static_world_map_without_plotly():
    svg = read("analytics_app/static/world-map.svg")
    assert svg.strip().startswith("<svg")
    assert "viewBox" in svg
    assert 'data-iso3="' in svg

    dashboard = read("analytics_app/static/index.html").lower()
    app_script = read("analytics_app/static/app.js")
    assert "plotly" not in dashboard + app_script.lower()
    assert "renderStaticMap" in app_script
