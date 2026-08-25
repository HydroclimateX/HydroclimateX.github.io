#!/usr/bin/env bash
set -euo pipefail

DOMAIN="lisflood.hydroclimatex.com"
EXPECTED_IP="8.210.252.61"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
PRIVATE_DIR="${LISFLOOD_PRIVATE_DIR:-/opt/hydroclimatex-wasp/lisflood-private}"
CACHE_DIR="${LISFLOOD_CACHE_DIR:-$STATE_DIR/lisflood-cache}"
NGINX_IMAGE="hydroclimatex/wasp-nginx:current"
PRIOR_NGINX_IMAGE=""
export WASP_STATE_DIR="$STATE_DIR" LISFLOOD_PRIVATE_DIR="$PRIVATE_DIR" LISFLOOD_CACHE_DIR="$CACHE_DIR"

fail() { printf '[lisflood] error: %s\n' "$*" >&2; exit 1; }
info() { printf '[lisflood] %s\n' "$*"; }
restore_proxy() {
  status=$?
  trap - ERR
  info "Deployment failed; restoring the existing proxy configuration."
  if [[ -n "$PRIOR_NGINX_IMAGE" ]]; then
    docker image tag "$PRIOR_NGINX_IMAGE" "$NGINX_IMAGE" || true
    NGINX_CONFIG=nginx.analytics.conf docker compose up -d --no-build --force-recreate nginx || true
  fi
  exit "$status"
}

[[ "$EUID" -eq 0 ]] || fail "run as root"
[[ "$(nproc)" -ge 2 ]] || fail "at least 2 vCPU are required"
[[ -f "$PRIVATE_DIR/source/CMakeLists.txt" ]] || fail "missing private LISFLOOD-FP source"
[[ -f "$PRIVATE_DIR/model/${LISFLOOD_PARAMETER_FILE:-ft.par}" ]] || fail "missing private model parameter file"
for existing_domain in wasp.hydroclimatex.com analytics.hydroclimatex.com telemetry.hydroclimatex.com; do
  [[ -s "$STATE_DIR/conf/live/$existing_domain/fullchain.pem" ]] || fail "missing existing certificate for $existing_domain"
done

records="$(dig +short A "$DOMAIN" | sed '/^[[:space:]]*$/d' | sort -u)"
[[ "$records" == "$EXPECTED_IP" ]] || fail "$DOMAIN must resolve exactly to $EXPECTED_IP"
[[ -z "$(dig +short AAAA "$DOMAIN" | sed '/^[[:space:]]*$/d')" ]] || fail "$DOMAIN must not publish an AAAA record before deployment"

install -d -m 0755 "$CACHE_DIR" "$STATE_DIR/www/.well-known/acme-challenge"
cd "$SCRIPT_DIR"
docker compose config --quiet
PRIOR_NGINX_IMAGE="$(docker inspect --format '{{.Image}}' wasp-nginx 2>/dev/null || true)"
[[ -n "$PRIOR_NGINX_IMAGE" ]] || fail "a running WASP Nginx image is required for rollback"
[[ "$(docker inspect --format '{{.State.Health.Status}}' wasp-nginx 2>/dev/null || true)" == "healthy" ]] || fail "the existing WASP Nginx container must be healthy"
info "Building the model runner and static web image."
docker compose --profile lisflood-tools build lisflood-runner nginx
info "Generating all five cached scenarios before changing the public proxy."
docker compose --profile lisflood-tools run --rm lisflood-runner
[[ -s "$CACHE_DIR/manifest.json" ]] || fail "cache generator did not publish /results/manifest.json"

trap restore_proxy ERR
if [[ ! -s "$STATE_DIR/conf/live/$DOMAIN/fullchain.pem" || ! -s "$STATE_DIR/conf/live/$DOMAIN/privkey.pem" ]]; then
  printf 'ready\n' > "$STATE_DIR/www/.well-known/acme-challenge/wasp-bootstrap-ready"
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --no-build --force-recreate nginx
  docker compose run --rm certbot certonly --webroot --webroot-path /var/www/certbot \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email -d "$DOMAIN"
fi

NGINX_CONFIG=nginx.analytics.conf docker compose up -d --no-build --force-recreate --wait nginx
curl --fail --silent --show-error --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" | grep -qx healthy
curl --fail --silent --show-error --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/results/manifest.json" >/dev/null
trap - ERR
info "LISFLOOD Web is available at https://$DOMAIN/"
