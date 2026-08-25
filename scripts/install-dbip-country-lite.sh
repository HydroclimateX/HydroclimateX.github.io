#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
temporary="$(mktemp -d)"
trap 'rm -rf "$temporary"' EXIT

current_release="$(date -u +%Y-%m)"
if previous_release="$(date -u -d '1 month ago' +%Y-%m 2>/dev/null)"; then
  :
else
  previous_release="$(date -u -v-1m +%Y-%m)"
fi
archive="$temporary/dbip-country-lite.mmdb.gz"
release=""

for candidate in "$current_release" "$previous_release"; do
  url="https://download.db-ip.com/free/dbip-country-lite-${candidate}.mmdb.gz"
  if curl --fail --silent --show-error --location --retry 3 --retry-delay 2 \
      "$url" --output "$archive"; then
    release="$candidate"
    break
  fi
  rm -f "$archive"
done

[[ -n "$release" && -s "$archive" ]] || {
  printf 'Unable to download the current or previous DB-IP Country Lite release\n' >&2
  exit 1
}

gzip -t "$archive"
database="$temporary/dbip-country-lite.mmdb"
gzip -dc "$archive" > "$database"
[[ "$(wc -c < "$database")" -ge 100000 ]] || {
  printf 'Downloaded DB-IP database is unexpectedly small\n' >&2
  exit 1
}
LC_ALL=C grep -a -q 'MaxMind.com' "$database" || {
  printf 'Downloaded file does not contain an MMDB metadata marker\n' >&2
  exit 1
}

install -d -m 0755 "$SCRIPT_DIR/geoip"
staged="$SCRIPT_DIR/geoip/.dbip-country-lite.mmdb.new"
install -m 0644 "$database" "$staged"
mv -f "$staged" "$SCRIPT_DIR/geoip/dbip-country-lite.mmdb"
printf 'Installed DB-IP Country Lite %s. Source IPs are used transiently and never stored.\n' "$release"
