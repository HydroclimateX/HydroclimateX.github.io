#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${WASP_STATE_DIR:-/opt/hydroclimatex-wasp/state}"
SMTP_MARKER="$STATE_DIR/smtp-test-verified"
[[ -f "$SMTP_MARKER" ]] || { printf 'Verify the test email, then create %s\n' "$SMTP_MARKER" >&2; exit 1; }

collected_since="$(awk -F= '$1 == "ANALYTICS_COLLECTED_SINCE" {print $2}' "$SCRIPT_DIR/.env" | tail -1)"
[[ -n "$collected_since" ]] || { printf 'ANALYTICS_COLLECTED_SINCE is missing\n' >&2; exit 1; }
first_schedule_date="$(date -d "${collected_since:0:7}-01 +2 months" +%F)"
today_hk="$(TZ=Asia/Hong_Kong date +%F)"
if [[ "$today_hk" < "$first_schedule_date" ]]; then
  printf 'The first complete post-launch month has not ended (enable on or after %s).\n' "$first_schedule_date" >&2
  exit 1
fi

cd "$SCRIPT_DIR"
docker compose --profile scheduled-reports up -d analytics-worker
printf 'Monthly Analytics reports enabled for 08:00 Asia/Hong_Kong on day 1.\n'
