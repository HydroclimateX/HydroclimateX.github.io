#!/usr/bin/env bash
# Deploy the WASP application and API behind HTTPS on the Hong Kong server.
# Before running, point DNS to EXPECTED_IP and allow inbound TCP 80/443.

set -euo pipefail

DOMAIN="wasp.hydroclimatex.com"
EXPECTED_IP="8.210.252.61"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROVIDED_WASP_STATE_DIR="${WASP_STATE_DIR:-}"
APP_ROOT="/opt/hydroclimatex-wasp"
WASP_STATE_DIR="$APP_ROOT/state"
WASP_STATE_DIR="${PROVIDED_WASP_STATE_DIR:-$WASP_STATE_DIR}"
export WASP_STATE_DIR
CERT_FILE="$WASP_STATE_DIR/conf/live/$DOMAIN/fullchain.pem"
CERT_KEY_FILE="$WASP_STATE_DIR/conf/live/$DOMAIN/privkey.pem"
BOOTSTRAP_PROBE="$WASP_STATE_DIR/www/.well-known/acme-challenge/wasp-bootstrap-ready"
RENEWAL_SCRIPT="/usr/local/sbin/renew-wasp-cert"
CRON_FILE="/etc/cron.d/wasp-cert-renew"
API_IMAGE="hydroclimatex/wasp-api:current"
NGINX_IMAGE="hydroclimatex/wasp-nginx:current"
TLS_CONFIG="nginx.conf"
if [ -s "$WASP_STATE_DIR/conf/live/analytics.hydroclimatex.com/fullchain.pem" ] &&
  [ -s "$WASP_STATE_DIR/conf/live/analytics.hydroclimatex.com/privkey.pem" ] &&
  [ -s "$WASP_STATE_DIR/conf/live/telemetry.hydroclimatex.com/fullchain.pem" ] &&
  [ -s "$WASP_STATE_DIR/conf/live/telemetry.hydroclimatex.com/privkey.pem" ]; then
  TLS_CONFIG="nginx.analytics.conf"
fi

# Test-only path overrides keep deployment sequencing tests hermetic. Normal
# direct invocations retain the production paths declared above.
RENEWAL_SCRIPT="${WASP_RENEWAL_SCRIPT:-$RENEWAL_SCRIPT}"
CRON_FILE="${WASP_CRON_FILE:-$CRON_FILE}"
PRIOR_API_IMAGE=""
PRIOR_NGINX_IMAGE=""
CERT_RECOVERY_DIR=""
FAILED_CERT_RECOVERY_DIR=""

info() { printf '[wasp] %s\n' "$*"; }
fail() { printf '[wasp] error: %s\n' "$*" >&2; exit 1; }

require_root() {
  if [ "$EUID" -ne 0 ]; then
    fail "This script must be run as root (EUID 0) before it can make deployment changes."
  fi
}

verify_https_health() {
  local response
  if ! response="$(curl --fail --silent --show-error \
      --retry 12 --retry-delay 5 --retry-all-errors --max-time 10 \
      --noproxy '*' \
      --resolve "$DOMAIN:443:127.0.0.1" \
      "https://$DOMAIN/api/health")"; then
    printf '[wasp] error: HTTPS health endpoint is unreachable.\n' >&2
    return 1
  fi
  if [[ "$response" != *healthy* ]]; then
    printf '[wasp] error: HTTPS health endpoint returned an unexpected response.\n' >&2
    return 1
  fi
}

certificate_key_pair_matches() {
  local certificate_public_key private_public_key
  [ -s "$CERT_FILE" ] &&
    [ -s "$CERT_KEY_FILE" ] &&
    certificate_public_key="$(openssl x509 -in "$CERT_FILE" -pubkey -noout 2>/dev/null)" &&
    private_public_key="$(openssl pkey -in "$CERT_KEY_FILE" -pubout 2>/dev/null)" &&
    [ -n "$certificate_public_key" ] &&
    [ "$certificate_public_key" = "$private_public_key" ]
}

certificate_is_valid() {
  certificate_key_pair_matches &&
    openssl x509 -checkend 86400 -noout -in "$CERT_FILE" >/dev/null 2>&1
}

certificate_state_exists() {
  [ -e "$WASP_STATE_DIR/conf/live/$DOMAIN" ] ||
    [ -e "$WASP_STATE_DIR/conf/archive/$DOMAIN" ] ||
    [ -e "$WASP_STATE_DIR/conf/renewal/$DOMAIN.conf" ]
}

move_current_certificate_state_to() {
  local recovery_root="$1" source destination
  for source in \
    "$WASP_STATE_DIR/conf/live/$DOMAIN" \
    "$WASP_STATE_DIR/conf/archive/$DOMAIN" \
    "$WASP_STATE_DIR/conf/renewal/$DOMAIN.conf"; do
    [ -e "$source" ] || continue
    case "$source" in
      */live/*) destination="$recovery_root/live/$DOMAIN" ;;
      */archive/*) destination="$recovery_root/archive/$DOMAIN" ;;
      *) destination="$recovery_root/renewal/$DOMAIN.conf" ;;
    esac
    mkdir -p "$(dirname "$destination")"
    mv "$source" "$destination"
  done
}

recover_invalid_certificate_state() {
  CERT_RECOVERY_DIR="$WASP_STATE_DIR/recovery/${DOMAIN}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
  info "Certificate state is incomplete, mismatched, or expires within 24 hours; moving it to $CERT_RECOVERY_DIR."
  move_current_certificate_state_to "$CERT_RECOVERY_DIR"
}

restore_recovered_certificate_state() {
  local source destination
  [ -n "$CERT_RECOVERY_DIR" ] || return 0

  if certificate_is_valid; then
    info "The replacement certificate is valid; retaining it for the restored containers."
    return 0
  fi

  if certificate_state_exists; then
    FAILED_CERT_RECOVERY_DIR="$WASP_STATE_DIR/recovery/failed-replacement-${DOMAIN}-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    info "Saving failed replacement certificate state to $FAILED_CERT_RECOVERY_DIR."
    move_current_certificate_state_to "$FAILED_CERT_RECOVERY_DIR" || return 1
  fi

  info "Restoring the previous certificate state from $CERT_RECOVERY_DIR."
  for source in \
    "$CERT_RECOVERY_DIR/live/$DOMAIN" \
    "$CERT_RECOVERY_DIR/archive/$DOMAIN" \
    "$CERT_RECOVERY_DIR/renewal/$DOMAIN.conf"; do
    [ -e "$source" ] || continue
    case "$source" in
      */live/*) destination="$WASP_STATE_DIR/conf/live/$DOMAIN" ;;
      */archive/*) destination="$WASP_STATE_DIR/conf/archive/$DOMAIN" ;;
      *) destination="$WASP_STATE_DIR/conf/renewal/$DOMAIN.conf" ;;
    esac
    mkdir -p "$(dirname "$destination")"
    mv "$source" "$destination" || return 1
  done

  certificate_key_pair_matches
}

rollback_after_failure() {
  local original_status=$?
  trap - ERR
  set +e

  if ! restore_recovered_certificate_state; then
    info "The previous certificate state could not be restored safely."
    info "Deployment failed readiness checks; stopping Nginx."
    docker compose stop nginx || true
    exit "$original_status"
  fi

  if [ -n "$PRIOR_API_IMAGE" ] && [ -n "$PRIOR_NGINX_IMAGE" ]; then
    info "Candidate deployment failed; restoring the previous API and Nginx images."
    if docker image tag "$PRIOR_API_IMAGE" "$API_IMAGE" &&
      docker image tag "$PRIOR_NGINX_IMAGE" "$NGINX_IMAGE" &&
      WASP_ROLLBACK=1 NGINX_CONFIG="$TLS_CONFIG" docker compose up -d --no-build \
        --force-recreate --wait --wait-timeout 180 wasp-api nginx &&
      verify_https_health; then
      info "Previous WASP deployment restored and HTTPS health verified."
      exit "$original_status"
    fi
    info "Automatic rollback did not become healthy."
  else
    info "No complete previous image pair is available for rollback."
  fi

  info "Deployment failed readiness checks; stopping Nginx."
  docker compose stop nginx || true
  exit "$original_status"
}

run_deployment() {
cd "$SCRIPT_DIR"

A_RECORDS="$(dig +short A "$DOMAIN" | sed '/^[[:space:]]*$/d' | sort -u)"
AAAA_RECORDS="$(dig +short AAAA "$DOMAIN" | sed '/^[[:space:]]*$/d' | sort -u)"
if [ "$A_RECORDS" != "$EXPECTED_IP" ]; then
  fail "DNS for $DOMAIN must resolve exactly to the single A record $EXPECTED_IP (resolved: ${A_RECORDS:-none})."
fi
if [ -n "$AAAA_RECORDS" ]; then
  fail "DNS for $DOMAIN must not publish an AAAA record before deployment (resolved: ${AAAA_RECORDS//$'\n'/, })."
fi

HTTP_PROBE="$(curl --silent --show-error --max-time 15 \
  --write-out $'\n%{http_code}' "http://$DOMAIN/" || true)"
HTTP_STATUS="${HTTP_PROBE##*$'\n'}"
HTTP_BODY="${HTTP_PROBE%$'\n'*}"
if [[ "$HTTP_STATUS" == "403" || "$HTTP_BODY" == *"Non-compliance ICP Filing"* ]]; then
  fail "HTTP preflight for $DOMAIN is blocked (status $HTTP_STATUS; Non-compliance ICP Filing/403). Resolve ICP or HTTP routing before changing the proxy or requesting a certificate."
fi

mkdir -p "$(dirname "$BOOTSTRAP_PROBE")" "$WASP_STATE_DIR/conf"
printf 'ready\n' > "$BOOTSTRAP_PROBE"

if certificate_state_exists && ! certificate_is_valid; then
  recover_invalid_certificate_state
fi

PRIOR_API_IMAGE="$(docker inspect --format '{{.Image}}' wasp-api 2>/dev/null || true)"
PRIOR_NGINX_IMAGE="$(docker inspect --format '{{.Image}}' wasp-nginx 2>/dev/null || true)"
PRIOR_API_HEALTH="$(docker inspect --format '{{.State.Health.Status}}' wasp-api 2>/dev/null || true)"
PRIOR_NGINX_HEALTH="$(docker inspect --format '{{.State.Health.Status}}' wasp-nginx 2>/dev/null || true)"
if [ "$PRIOR_API_HEALTH" != "healthy" ] || [ "$PRIOR_NGINX_HEALTH" != "healthy" ]; then
  PRIOR_API_IMAGE=""
  PRIOR_NGINX_IMAGE=""
fi

trap rollback_after_failure ERR
info "Building candidate API and baked Nginx images."
docker compose build wasp-api nginx

if ! certificate_is_valid; then
  info "Starting the HTTP bootstrap proxy for the ACME webroot challenge."
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --no-build \
    --force-recreate --wait --wait-timeout 180 wasp-api nginx
  info "Requesting the initial Let's Encrypt certificate for $DOMAIN."
  docker compose run --rm certbot certonly --webroot \
    --webroot-path /var/www/certbot \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email \
    -d "$DOMAIN"
  if ! certificate_is_valid; then
    printf '[wasp] error: Certbot returned without installing a valid 24-hour certificate/key pair.\n' >&2
    return 1
  fi
else
  info "An existing certificate was found; preserving HTTPS and skipping the HTTP bootstrap proxy."
fi

info "Starting Nginx with the HTTPS configuration."
NGINX_CONFIG="$TLS_CONFIG" docker compose up -d --no-build --force-recreate \
  --wait --wait-timeout 180 wasp-api nginx
info "Verifying the externally routed HTTPS API locally."
verify_https_health
trap - ERR

cat > "$RENEWAL_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SCRIPT_DIR"
export WASP_STATE_DIR="$WASP_STATE_DIR"
docker compose run --rm certbot renew --webroot --webroot-path /var/www/certbot
docker compose exec -T nginx nginx -s reload
EOF
chmod 0755 "$RENEWAL_SCRIPT"

cat > "$CRON_FILE" <<EOF
# Managed by HydroclimateX WASP deployment. Certbot renews only when needed.
17 3 * * * root $RENEWAL_SCRIPT >> /var/log/wasp-cert-renew.log 2>&1
EOF
chmod 0644 "$CRON_FILE"
if command -v systemctl >/dev/null 2>&1; then
  systemctl enable --now cron
fi

info "WASP is available at https://$DOMAIN/"
info "API health is available at https://$DOMAIN/api/health"
info "API docs are available at https://$DOMAIN/api/docs"
info "Renew safely with: $RENEWAL_SCRIPT (renews the certificate and reloads wasp-nginx)."
info "Installed daily renewal schedule: $CRON_FILE"
}

main() {
  require_root
  run_deployment
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
