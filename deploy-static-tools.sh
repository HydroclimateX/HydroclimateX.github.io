#!/usr/bin/env bash
set -euo pipefail

DOMAINS=(wqm.hydroclimatex.com synthesis.hydroclimatex.com)
EXPECTED_IP="8.210.252.61"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
NGINX_IMAGE="hydroclimatex/wasp-nginx:current"
PRIOR_NGINX_IMAGE=""
PRIOR_NGINX_CONFIG=""
export WASP_STATE_DIR="$STATE_DIR"

fail() { printf '[static-tools] error: %s\n' "$*" >&2; exit 1; }
info() { printf '[static-tools] %s\n' "$*"; }

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
  info "Deployment failed; restoring the existing proxy image and configuration."
  if [[ -n "$PRIOR_NGINX_IMAGE" ]] &&
     docker image tag "$PRIOR_NGINX_IMAGE" "$NGINX_IMAGE" &&
     NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up -d --no-build --force-recreate --wait --wait-timeout 120 nginx; then
    info "Existing proxy restored."
  else
    printf '[static-tools] error: rollback failed; restore the existing proxy manually.\n' >&2
  fi
  exit "$status"
}

[[ "$EUID" -eq 0 ]] || fail "run as root"
for domain in "${DOMAINS[@]}"; do
  records="$(dig +short A "$domain" | sed '/^[[:space:]]*$/d' | sort -u)"
  [[ "$records" == "$EXPECTED_IP" ]] || fail "$domain must resolve exactly to $EXPECTED_IP"
  [[ -z "$(dig +short AAAA "$domain" | sed '/^[[:space:]]*$/d')" ]] || fail "$domain must not publish an AAAA record before deployment"
done

install -d -m 0755 "$STATE_DIR/www/.well-known/acme-challenge"
cd "$SCRIPT_DIR"
docker compose config --quiet
PRIOR_NGINX_CONFIG="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wasp-nginx 2>/dev/null | sed -n 's/^NGINX_CONFIG=//p' || true)"
[[ "$PRIOR_NGINX_CONFIG" == "nginx.conf" || "$PRIOR_NGINX_CONFIG" == "nginx.analytics.conf" ]] || fail "existing Nginx config is unsupported"
PRIOR_NGINX_IMAGE="$(docker inspect --format '{{.Image}}' wasp-nginx 2>/dev/null || true)"
[[ -n "$PRIOR_NGINX_IMAGE" ]] || fail "a running Nginx image is required for rollback"
[[ "$(docker inspect --format '{{.State.Health.Status}}' wasp-nginx 2>/dev/null || true)" == "healthy" ]] || fail "the existing Nginx container must be healthy"

info "Building the static web image."
docker compose build nginx
trap restore_proxy ERR

needs_certificate=0
for domain in "${DOMAINS[@]}"; do
  certificate_is_valid "$domain" || needs_certificate=1
done
if [[ "$needs_certificate" -eq 1 ]]; then
  printf 'ready\n' > "$STATE_DIR/www/.well-known/acme-challenge/wasp-bootstrap-ready"
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --no-build --force-recreate --wait --wait-timeout 120 nginx
  for domain in "${DOMAINS[@]}"; do
    if ! certificate_is_valid "$domain"; then
      docker compose run --rm certbot certonly --webroot --webroot-path /var/www/certbot \
        --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email -d "$domain"
      if ! certificate_is_valid "$domain"; then
        printf '[static-tools] error: certificate remains invalid for %s\n' "$domain" >&2
        restore_proxy 1
      fi
    fi
  done
fi

NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up -d --no-build --force-recreate --wait --wait-timeout 120 nginx
for domain in "${DOMAINS[@]}"; do
  curl --fail --silent --show-error --resolve "$domain:443:127.0.0.1" "https://$domain/health" | grep -qx healthy
done
trap - ERR
info "WQM and synthesis are available over HTTPS."
