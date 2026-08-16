#!/usr/bin/env bash
# Deploy the WASP API behind HTTPS at wasp.hydroclimatex.com.
# Before running, point that DNS name to this server and allow inbound 80/443.

set -euo pipefail

DOMAIN="wasp.hydroclimatex.com"
CERTBOT_EMAIL="ze.jiang@hhu.edu.cn"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CERT_FILE="$SCRIPT_DIR/certbot/conf/live/$DOMAIN/fullchain.pem"
RENEWAL_SCRIPT="/usr/local/sbin/renew-wasp-cert"
CRON_FILE="/etc/cron.d/wasp-cert-renew"

# Test-only path overrides keep deployment sequencing tests hermetic. Normal
# direct invocations retain the production paths declared above.
RENEWAL_SCRIPT="${WASP_RENEWAL_SCRIPT:-$RENEWAL_SCRIPT}"
CRON_FILE="${WASP_CRON_FILE:-$CRON_FILE}"
BOOTSTRAP_NGINX_STARTED=0

info() { printf '[wasp] %s\n' "$*"; }
fail() { printf '[wasp] error: %s\n' "$*" >&2; exit 1; }

require_root() {
  if [ "$EUID" -ne 0 ]; then
    fail "This script must be run as root (EUID 0) before it can make deployment changes."
  fi
}

stop_bootstrap_after_certificate_failure() {
  local status=$?
  trap - ERR
  if [ "$BOOTSTRAP_NGINX_STARTED" -eq 1 ]; then
    info "Certificate issuance failed; stopping the HTTP bootstrap proxy."
    docker compose stop nginx || true
    BOOTSTRAP_NGINX_STARTED=0
  fi
  exit "$status"
}

run_deployment() {
cd "$SCRIPT_DIR"
mkdir -p certbot/www certbot/conf

if ! getent hosts "$DOMAIN" >/dev/null 2>&1; then
  fail "DNS for $DOMAIN is not resolvable yet; create its A/AAAA record before requesting a certificate."
fi

HTTP_PROBE="$(curl --silent --show-error --max-time 15 \
  --write-out $'\n%{http_code}' "http://$DOMAIN/" || true)"
HTTP_STATUS="${HTTP_PROBE##*$'\n'}"
HTTP_BODY="${HTTP_PROBE%$'\n'*}"
if [[ "$HTTP_STATUS" == "403" || "$HTTP_BODY" == *"Non-compliance ICP Filing"* ]]; then
  fail "HTTP preflight for $DOMAIN is blocked (status $HTTP_STATUS; Non-compliance ICP Filing/403). Resolve ICP or HTTP routing before changing the proxy or requesting a certificate."
fi

if [ ! -f "$CERT_FILE" ]; then
  info "Starting the HTTP bootstrap proxy for the ACME webroot challenge."
  NGINX_CONFIG=nginx.bootstrap.conf docker compose up -d --build wasp-api nginx
  BOOTSTRAP_NGINX_STARTED=1
  trap stop_bootstrap_after_certificate_failure ERR
  info "Requesting the initial Let's Encrypt certificate for $DOMAIN."
  docker compose run --rm certbot certonly --webroot \
    --webroot-path /var/www/certbot \
    --email "$CERTBOT_EMAIL" --agree-tos --no-eff-email \
    -d "$DOMAIN"
  trap - ERR
  BOOTSTRAP_NGINX_STARTED=0
else
  info "An existing certificate was found; preserving HTTPS and skipping the HTTP bootstrap proxy."
  docker compose up -d --build wasp-api
fi

info "Starting Nginx with the HTTPS configuration."
NGINX_CONFIG=nginx.conf docker compose up -d --force-recreate nginx

cat > "$RENEWAL_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SCRIPT_DIR"
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

info "HTTPS API is available at https://$DOMAIN/"
info "API docs are available at https://$DOMAIN/docs"
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
