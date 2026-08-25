#!/usr/bin/env bash
set -euo pipefail

[[ -n "${MAXMIND_LICENSE_KEY:-}" ]] || { printf 'MAXMIND_LICENSE_KEY is required\n' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

curl --fail --silent --show-error --location \
  "https://download.maxmind.com/app/geoip_download?edition_id=GeoLite2-Country&license_key=${MAXMIND_LICENSE_KEY}&suffix=tar.gz" \
  --output "$temporary/geolite.tar.gz"
tar -xzf "$temporary/geolite.tar.gz" -C "$temporary"
database="$(find "$temporary" -type f -name GeoLite2-Country.mmdb -print -quit)"
[[ -n "$database" ]] || { printf 'GeoLite2 database was not present in the archive\n' >&2; exit 1; }
install -d -m 0755 "$SCRIPT_DIR/geoip"
install -m 0644 "$database" "$SCRIPT_DIR/geoip/GeoLite2-Country.mmdb"
printf 'Installed GeoLite2-Country.mmdb. The source IP is used transiently and never stored.\n'
