#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
ENV_FILE="${HYDROCLIMATEX_ENV_FILE:-$SCRIPT_DIR/.env}"
HOST_NGINX_ROOT="${HOST_NGINX_ROOT:-/etc/nginx}"
HOST_SITE_AVAILABLE="$HOST_NGINX_ROOT/sites-available/hydroclimatex-apps"
HOST_SITE_ENABLED="$HOST_NGINX_ROOT/sites-enabled/hydroclimatex-apps"
HOST_PROXY_SNIPPET="$HOST_NGINX_ROOT/snippets/hydroclimatex-docker-proxy.conf"
RENEWAL_SCRIPT="${WASP_RENEWAL_SCRIPT:-/usr/local/sbin/renew-wasp-cert}"
NGINX_HTTP_PUBLISH="127.0.0.1:18080"
NGINX_HTTPS_PUBLISH="127.0.0.1:18443"
DOMAINS=(
  wasp.hydroclimatex.com
  lisflood.hydroclimatex.com
  analytics.hydroclimatex.com
  telemetry.hydroclimatex.com
  wqm.hydroclimatex.com
  synthesis.hydroclimatex.com
)

BACKUP_DIR=""
ROLLBACK_ARMED=0
PRIOR_NGINX_RUNNING="false"
PRIOR_NGINX_CONFIG="nginx.analytics.conf"
PRIOR_NGINX_IMAGE=""

info() { printf '[frontdoor] %s\n' "$*"; }
fail() { printf '[frontdoor] error: %s\n' "$*" >&2; exit 1; }

backup_path() {
  local source="$1" name="$2"
  if [[ -L "$source" ]]; then
    readlink "$source" > "$BACKUP_DIR/$name.symlink"
  elif [[ -e "$source" ]]; then
    cp -a "$source" "$BACKUP_DIR/$name.file"
  fi
}

restore_path() {
  local destination="$1" name="$2"
  rm -f "$destination"
  if [[ -f "$BACKUP_DIR/$name.symlink" ]]; then
    ln -s "$(<"$BACKUP_DIR/$name.symlink")" "$destination"
  elif [[ -e "$BACKUP_DIR/$name.file" ]]; then
    cp -a "$BACKUP_DIR/$name.file" "$destination"
  fi
}

set_env_value() {
  local key="$1" value="$2" temporary
  temporary="$(mktemp)"
  if [[ -f "$ENV_FILE" ]]; then
    awk -v key="$key" 'index($0, key "=") != 1 { print }' "$ENV_FILE" > "$temporary"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$temporary"
  install -m 0600 "$temporary" "$ENV_FILE"
  rm -f "$temporary"
}

install_renewal_script() {
  local temporary
  temporary="$(mktemp)"
  cat > "$temporary" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SCRIPT_DIR"
export WASP_STATE_DIR="$STATE_DIR"
docker compose run --rm certbot renew --webroot --webroot-path /var/www/certbot
docker compose exec -T nginx nginx -s reload
nginx -t
systemctl reload nginx
EOF
  install -m 0755 "$temporary" "$RENEWAL_SCRIPT"
  rm -f "$temporary"
}

certificate_is_valid() {
  local domain="$1" cert key cert_public key_public
  cert="$STATE_DIR/conf/live/$domain/fullchain.pem"
  key="$STATE_DIR/conf/live/$domain/privkey.pem"
  [[ -s "$cert" && -s "$key" ]] || return 1
  openssl x509 -checkend 86400 -noout -in "$cert" >/dev/null 2>&1 || return 1
  cert_public="$(openssl x509 -in "$cert" -pubkey -noout 2>/dev/null | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum)" || return 1
  key_public="$(openssl pkey -in "$key" -pubout -outform DER 2>/dev/null | sha256sum)" || return 1
  [[ "$cert_public" == "$key_public" ]]
}

ensure_publish_port_available_or_owned() {
  local host_port="$1" container_port="$2"
  if ! ss -ltn | grep -Eq "[:.]${host_port}[[:space:]]"; then
    return 0
  fi
  docker port wasp-nginx "${container_port}/tcp" 2>/dev/null \
    | grep -qx "127.0.0.1:${host_port}"
}

verify_local_backend() {
  local domain="$1" path="$2"
  curl --fail --silent --show-error --max-time 20 --noproxy '*' \
    --resolve "$domain:18443:127.0.0.1" "https://$domain:18443$path" >/dev/null
}

verify_public_endpoint() {
  local domain="$1" path="$2"
  curl --fail --silent --show-error --max-time 20 --noproxy '*' \
    "https://$domain$path" >/dev/null
}

rollback() {
  local status="${1:-1}"
  trap - ERR INT TERM
  set +e
  [[ "$ROLLBACK_ARMED" -eq 1 ]] || exit "$status"
  info "Deployment failed; restoring the previous host proxy and Docker binding."

  restore_path "$ENV_FILE" env
  restore_path "$HOST_SITE_AVAILABLE" site-available
  restore_path "$HOST_SITE_ENABLED" site-enabled
  restore_path "$HOST_PROXY_SNIPPET" proxy-snippet
  restore_path "$RENEWAL_SCRIPT" renewal
  unset NGINX_HTTP_PUBLISH NGINX_HTTPS_PUBLISH

  if [[ -n "$PRIOR_NGINX_IMAGE" ]]; then
    docker image tag "$PRIOR_NGINX_IMAGE" hydroclimatex/wasp-nginx:current || true
  fi

  if [[ "$PRIOR_NGINX_RUNNING" == "true" ]]; then
    NGINX_CONFIG="$PRIOR_NGINX_CONFIG" docker compose up -d --no-build --force-recreate nginx || true
  else
    docker compose stop nginx >/dev/null 2>&1 || true
  fi
  if nginx -t; then
    systemctl reload nginx || true
  fi
  exit "$status"
}

main() {
  [[ "$EUID" -eq 0 || "${FRONTDOOR_ALLOW_NON_ROOT:-0}" == "1" ]] || fail "run as root"
  export WASP_STATE_DIR="$STATE_DIR"
  for command in docker curl openssl sha256sum nginx systemctl ss; do
    command -v "$command" >/dev/null 2>&1 || fail "missing required command: $command"
  done
  systemctl is-active --quiet nginx || fail "host Nginx is not active"
  systemctl is-active --quiet frps || fail "FRP server is not active"
  ss -ltn | grep -Eq '[:.]18082[[:space:]]' || fail "FRP Nextcloud endpoint is not listening on port 18082"
  curl --fail --silent --show-error --max-time 20 --noproxy '*' \
    https://cloud.hydroclimatex.com/status.php >/dev/null || fail "Nextcloud status endpoint is unavailable"
  for domain in "${DOMAINS[@]}"; do
    certificate_is_valid "$domain" || fail "missing, mismatched, or expiring certificate for $domain"
  done
  ensure_publish_port_available_or_owned 18080 80 || fail "port 18080 is already owned by another process"
  ensure_publish_port_available_or_owned 18443 443 || fail "port 18443 is already owned by another process"

  cd "$SCRIPT_DIR"
  docker compose config --quiet
  PRIOR_NGINX_RUNNING="$(docker inspect --format '{{.State.Running}}' wasp-nginx 2>/dev/null || printf false)"
  PRIOR_NGINX_IMAGE="$(docker inspect --format '{{.Image}}' wasp-nginx 2>/dev/null || true)"
  PRIOR_NGINX_CONFIG="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' wasp-nginx 2>/dev/null | sed -n 's/^NGINX_CONFIG=//p' || true)"
  [[ "$PRIOR_NGINX_CONFIG" == "nginx.conf" || "$PRIOR_NGINX_CONFIG" == "nginx.analytics.conf" ]] || PRIOR_NGINX_CONFIG="nginx.analytics.conf"

  install -d -m 0700 "$STATE_DIR/frontdoor-backups"
  BACKUP_DIR="$(mktemp -d "$STATE_DIR/frontdoor-backups/cutover-XXXXXXXX")"
  backup_path "$ENV_FILE" env
  backup_path "$HOST_SITE_AVAILABLE" site-available
  backup_path "$HOST_SITE_ENABLED" site-enabled
  backup_path "$HOST_PROXY_SNIPPET" proxy-snippet
  backup_path "$RENEWAL_SCRIPT" renewal
  ROLLBACK_ARMED=1
  trap 'rollback $?' ERR INT TERM

  set_env_value NGINX_HTTP_PUBLISH "$NGINX_HTTP_PUBLISH"
  set_env_value NGINX_HTTPS_PUBLISH "$NGINX_HTTPS_PUBLISH"
  export NGINX_HTTP_PUBLISH NGINX_HTTPS_PUBLISH

  info "Building the Docker proxy with host-frontdoor client address handling."
  docker compose build nginx
  info "Starting the Docker proxy on loopback ports 18080 and 18443."
  NGINX_CONFIG=nginx.analytics.conf docker compose up -d --no-build --force-recreate --wait --wait-timeout 180 nginx
  verify_local_backend wasp.hydroclimatex.com /api/health
  verify_local_backend lisflood.hydroclimatex.com /health
  verify_local_backend analytics.hydroclimatex.com /health
  verify_local_backend telemetry.hydroclimatex.com /config.json
  verify_local_backend wqm.hydroclimatex.com /health
  verify_local_backend synthesis.hydroclimatex.com /health

  info "Installing application virtual hosts without changing the Nextcloud site."
  install -d -m 0755 "$HOST_NGINX_ROOT/sites-available" "$HOST_NGINX_ROOT/sites-enabled" "$HOST_NGINX_ROOT/snippets"
  install -m 0644 "$SCRIPT_DIR/host-nginx/hydroclimatex-apps.conf" "$HOST_SITE_AVAILABLE"
  install -m 0644 "$SCRIPT_DIR/host-nginx/hydroclimatex-docker-proxy.conf" "$HOST_PROXY_SNIPPET"
  ln -sfn "$HOST_SITE_AVAILABLE" "$HOST_SITE_ENABLED"
  nginx -t
  systemctl reload nginx

  verify_public_endpoint cloud.hydroclimatex.com /status.php
  verify_public_endpoint wasp.hydroclimatex.com /api/health
  verify_public_endpoint lisflood.hydroclimatex.com /health
  verify_public_endpoint analytics.hydroclimatex.com /health
  verify_public_endpoint telemetry.hydroclimatex.com /config.json
  verify_public_endpoint wqm.hydroclimatex.com /health
  verify_public_endpoint synthesis.hydroclimatex.com /health
  install_renewal_script

  trap - ERR INT TERM
  ROLLBACK_ARMED=0
  info "Host front door is active; Nextcloud and FRP were not restarted."
  info "Rollback snapshot retained at $BACKUP_DIR"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
