#!/usr/bin/env bash
set -euo pipefail

DOMAIN="lisflood.hydroclimatex.com"
EXPECTED_IP="8.210.252.61"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
NGINX_IMAGE="hydroclimatex/wasp-nginx:current"
PRIOR_NGINX_IMAGE=""
PRIOR_NGINX_CONFIG=""
export WASP_STATE_DIR="$STATE_DIR"

fail() { printf '[lisflood] error: %s\n' "$*" >&2; exit 1; }
info() { printf '[lisflood] %s\n' "$*"; }
certificate_is_valid() {
  local domain="$1"
  local cert="$STATE_DIR/conf/live/$domain/fullchain.pem"
  local key="$STATE_DIR/conf/live/$domain/privkey.pem"
  local cert_public key_public
  [[ -s "$cert" && -s "$key" ]] || return 1
  openssl x509 -in "$cert" -noout -checkend 86400 >/dev/null 2>&1 || return 1
  cert_public="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum)" || return 1
  key_public="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum)" || return 1
  [[ "$cert_public" == "$key_public" ]]
}
restore_proxy() {
  local status="${1:-$?}"
  trap - ERR
  info "Deployment failed; restoring the existing proxy configuration."
  local rollback_failed=0
  if [[ -n "$PRIOR_NGINX_IMAGE" ]]; then
    if ! docker image tag "$PRIOR_NGINX_IMAGE" "$NGINX_IMAGE"; then
      rollback_failed=1
    elif ! NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up -d --no-build --force-recreate --wait --wait-timeout 120 nginx; then
      rollback_failed=1
    fi
  else
    rollback_failed=1
  fi
  if [[ "$rollback_failed" -ne 0 ]]; then
    printf '[lisflood] error: rollback failed; restore the existing proxy manually.\n' >&2
  fi
  exit "$status"
}

[[ "$EUID" -eq 0 ]] || fail "run as root"
[[ "$(nproc)" -ge 2 ]] || fail "at least 2 vCPU are required"
for required in \
  "$SCRIPT_DIR/lisflood_runner/data/dem.asc.gz" \
  "$SCRIPT_DIR/lisflood_runner/data/population.asc.gz" \
  "$SCRIPT_DIR/lisflood_runner/data/SHA256SUMS"; do
  [[ -s "$required" ]] || fail "missing tracked LISFLOOD data: $required"
done
(
  cd "$SCRIPT_DIR/lisflood_runner/data"
  sha256sum -c SHA256SUMS
) || fail "tracked LISFLOOD data checksum verification failed"
for existing_domain in wasp.hydroclimatex.com analytics.hydroclimatex.com telemetry.hydroclimatex.com; do
  certificate_is_valid "$existing_domain" || fail "missing or invalid certificate for $existing_domain"
done

records="$(dig +short A "$DOMAIN" | sed '/^[[:space:]]*$/d' | sort -u)"
[[ "$records" == "$EXPECTED_IP" ]] || fail "$DOMAIN must resolve exactly to $EXPECTED_IP"
[[ -z "$(dig +short AAAA "$DOMAIN" | sed '/^[[:space:]]*$/d')" ]] || fail "$DOMAIN must not publish an AAAA record before deployment"

install -d -m 0755 "$STATE_DIR/www/.well-known/acme-challenge"
cd "$SCRIPT_DIR"
docker compose config --quiet
PRIOR_NGINX_CONFIG="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wasp-nginx 2>/dev/null | sed -n 's/^NGINX_CONFIG=//p' || true)"
[[ "$PRIOR_NGINX_CONFIG" == "nginx.conf" || "$PRIOR_NGINX_CONFIG" == "nginx.analytics.conf" ]] || fail "existing WASP Nginx config is unsupported"
PRIOR_NGINX_IMAGE="$(docker inspect --format '{{.Image}}' wasp-nginx 2>/dev/null || true)"
[[ -n "$PRIOR_NGINX_IMAGE" ]] || fail "a running WASP Nginx image is required for rollback"
[[ "$(docker inspect --format '{{.State.Health.Status}}' wasp-nginx 2>/dev/null || true)" == "healthy" ]] || fail "the existing WASP Nginx container must be healthy"
info "Building the model runner and static web image."
docker compose build lisflood-runner nginx
info "Starting the LISFLOOD service before changing the public proxy."
docker compose up -d --build --wait lisflood-runner

trap restore_proxy ERR
if ! certificate_is_valid "$DOMAIN"; then
  printf 'ready\n' > "$STATE_DIR/www/.well-known/acme-challenge/wasp-bootstrap-ready"
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --no-build --force-recreate --wait --wait-timeout 120 nginx
  docker compose run --rm certbot certonly --webroot --webroot-path /var/www/certbot \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email --force-renewal -d "$DOMAIN"
  if ! certificate_is_valid "$DOMAIN"; then
    printf '[lisflood] error: LISFLOOD certificate remains invalid after renewal.\n' >&2
    restore_proxy 1
  fi
fi

NGINX_CONFIG=nginx.analytics.conf docker compose up -d --no-build --force-recreate --wait nginx
curl --fail --silent --show-error --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" | grep -qx healthy
curl --fail --silent --show-error --resolve "$DOMAIN:443:127.0.0.1" \
  "https://$DOMAIN/api/lisflood/config" | grep -q '"maxAreaKm2"'
trap - ERR
info "LISFLOOD Web is available at https://$DOMAIN/"
