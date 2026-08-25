#!/usr/bin/env bash
set -euo pipefail

backup_root="${ANALYTICS_BACKUP_DIR:-/opt/hydroclimatex-wasp/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_root"

docker compose exec -T analytics-postgres pg_dump \
  --username analytics --dbname analytics --format=custom \
  > "$backup_root/analytics-$timestamp.dump"
docker compose exec -T analytics-postgres pg_dump \
  --username umami --dbname umami --format=custom \
  > "$backup_root/umami-$timestamp.dump"

find "$backup_root" -type f \( -name 'analytics-*.dump' -o -name 'umami-*.dump' \) -mtime +29 -delete
