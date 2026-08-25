#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXPECTED_IP="8.210.252.61"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
export WASP_STATE_DIR="$STATE_DIR"
BACKUP_CRON="${ANALYTICS_BACKUP_CRON:-/etc/cron.d/hydroclimatex-analytics-backup}"
SMTP_MARKER="$STATE_DIR/smtp-test-verified"
DOMAINS=(analytics.hydroclimatex.com telemetry.hydroclimatex.com)
PRIOR_NGINX_CONFIG="nginx.conf"
if [[ -s "$STATE_DIR/conf/live/analytics.hydroclimatex.com/fullchain.pem" &&
      -s "$STATE_DIR/conf/live/analytics.hydroclimatex.com/privkey.pem" &&
      -s "$STATE_DIR/conf/live/telemetry.hydroclimatex.com/fullchain.pem" &&
      -s "$STATE_DIR/conf/live/telemetry.hydroclimatex.com/privkey.pem" ]]; then
  PRIOR_NGINX_CONFIG="nginx.analytics.conf"
fi

fail() { printf '[analytics] error: %s\n' "$*" >&2; exit 1; }
info() { printf '[analytics] %s\n' "$*"; }
restore_proxy() {
  status=$?
  trap - ERR
  info "Analytics rollout failed; restoring the previous WASP proxy configuration."
  NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up -d --no-build --force-recreate nginx || true
  exit "$status"
}

[[ "$EUID" -eq 0 ]] || fail "run as root"
[[ "$(nproc)" -ge 2 ]] || fail "at least 2 vCPU are required"
required_keys=(POSTGRES_ADMIN_PASSWORD ANALYTICS_DB_PASSWORD UMAMI_DB_PASSWORD ANALYTICS_DATABASE_URL UMAMI_DATABASE_URL UMAMI_APP_SECRET ANALYTICS_ADMIN_PASSWORD_HASH ANALYTICS_INTERNAL_TOKEN ANALYTICS_SESSION_SECRET ANALYTICS_COLLECTED_SINCE ANALYTICS_SMTP_AUTHORIZATION_CODE)
for key in "${required_keys[@]}"; do
  if [[ -f "$SCRIPT_DIR/.env" ]]; then
    value="$(sed -n "s/^${key}=//p" "$SCRIPT_DIR/.env" | tail -1)"
    source_name=".env"
  else
    value="${!key:-}"
    source_name="the root shell environment"
  fi
  [[ -n "$value" ]] || fail "$key must be set in $source_name"
done
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  umami_api_username="$(sed -n 's/^UMAMI_API_USERNAME=//p' "$SCRIPT_DIR/.env" | tail -1)"
  umami_api_password="$(sed -n 's/^UMAMI_API_PASSWORD=//p' "$SCRIPT_DIR/.env" | tail -1)"
  umami_website_id="$(sed -n 's/^UMAMI_WEBSITE_ID=//p' "$SCRIPT_DIR/.env" | tail -1)"
else
  umami_api_username="${UMAMI_API_USERNAME:-}"
  umami_api_password="${UMAMI_API_PASSWORD:-}"
  umami_website_id="${UMAMI_WEBSITE_ID:-}"
fi
if [[ -z "$umami_api_username" || -z "$umami_api_password" || -z "$umami_website_id" ]]; then
  info "Umami read credentials are not configured; Website Analytics will remain unavailable."
fi
"$SCRIPT_DIR/scripts/install-dbip-country-lite.sh"
[[ -s "$SCRIPT_DIR/geoip/dbip-country-lite.mmdb" ]] || fail "DB-IP Country Lite installation failed"
[[ -s "$STATE_DIR/conf/live/wasp.hydroclimatex.com/fullchain.pem" ]] || fail "the existing WASP TLS certificate is required"

for domain in "${DOMAINS[@]}"; do
  records="$(dig +short A "$domain" | sed '/^[[:space:]]*$/d' | sort -u)"
  [[ "$records" == "$EXPECTED_IP" ]] || fail "$domain must resolve exactly to $EXPECTED_IP"
  [[ -z "$(dig +short AAAA "$domain" | sed '/^[[:space:]]*$/d')" ]] || fail "$domain must not publish an AAAA record before deployment"
done

cd "$SCRIPT_DIR"
trap restore_proxy ERR
docker compose config --quiet
info "Building and starting private data services and Analytics API."
docker compose build analytics-api wasp-api nginx
docker compose run --rm --no-deps wasp-api python -c \
  'import maxminddb; reader=maxminddb.open_database("/opt/geoip/dbip-country-lite.mmdb"); code=reader.get("8.8.8.8")["country"]["iso_code"]; assert len(code) == 2; reader.close()'
docker compose up -d --wait analytics-postgres umami analytics-api wasp-api
docker compose run --rm analytics-api python -m analytics_app.cli migrate

missing_certificate=0
for domain in "${DOMAINS[@]}"; do
  [[ -s "$STATE_DIR/conf/live/$domain/fullchain.pem" && -s "$STATE_DIR/conf/live/$domain/privkey.pem" ]] || missing_certificate=1
done
if [[ "$missing_certificate" -eq 1 ]]; then
  info "Starting the HTTP-only ACME configuration."
  install -d -m 0755 "$STATE_DIR/www/.well-known/acme-challenge"
  printf 'ready\n' > "$STATE_DIR/www/.well-known/acme-challenge/wasp-bootstrap-ready"
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --no-build --force-recreate nginx
  for domain in "${DOMAINS[@]}"; do
    if [[ ! -s "$STATE_DIR/conf/live/$domain/fullchain.pem" || ! -s "$STATE_DIR/conf/live/$domain/privkey.pem" ]]; then
      docker compose run --rm certbot certonly --webroot --webroot-path /var/www/certbot \
        --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email -d "$domain"
    fi
  done
fi

NGINX_CONFIG=nginx.analytics.conf docker compose up -d --no-build --force-recreate --wait nginx
curl --fail --silent --show-error --resolve "analytics.hydroclimatex.com:443:127.0.0.1" \
  https://analytics.hydroclimatex.com/health >/dev/null

info "Sending the required one-off SMTP test."
docker compose run --rm analytics-api python -m analytics_app.cli test-email
rm -f "$SMTP_MARKER"

install -d -m 0755 "$(dirname "$BACKUP_CRON")"
printf '23 2 * * * root cd %s && %s/scripts/backup-analytics-db.sh >> /var/log/hydroclimatex-analytics-backup.log 2>&1\n' \
  "$SCRIPT_DIR" "$SCRIPT_DIR" > "$BACKUP_CRON"
chmod 0644 "$BACKUP_CRON"
trap - ERR

info "Analytics is deployed without the monthly scheduler."
info "After the recipient verifies the SMTP test, run: touch $SMTP_MARKER"
info "After the first complete post-launch month, run scripts/enable-analytics-reports.sh"
