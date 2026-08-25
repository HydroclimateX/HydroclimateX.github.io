# HydroClimateX Private Analytics

The platform runs two public origins on the existing Hong Kong host:

- `analytics.hydroclimatex.com` — administrator login, Dashboard and protected APIs.
- `telemetry.hydroclimatex.com` — only `config.json`, `script.js` and `POST /api/send` are public.

PostgreSQL, Umami administration and WASP event ingestion have no host ports. Nginx access logs contain only timestamp, status, normalized route, method and duration.

## Production rollout

1. Provision at least approximately 2 vCPU and 4 GB RAM. Point both new A records to `8.210.252.61` and do not publish premature AAAA records.
2. Copy `.env.example` to `.env`. Generate the administrator hash with `python -m analytics_app.cli hash-password`, and generate the two service secrets with a cryptographically secure password manager. Use URL-safe database passwords in both the init variables and database URLs.
3. In Umami, create one website whose allowed domains are exactly `hydroclimatex.com,www.hydroclimatex.com`. Put its public website ID and the private Umami API credentials in `.env`.
4. Export the MaxMind license key only for the installation command, then run `scripts/install-geolite2-country.sh`. The key and database are runtime state and must not be committed.
5. Run `sudo ./deploy-analytics.sh`. It performs the resource/DNS preflight, starts PostgreSQL and Umami, migrates Analytics, obtains separate certificates, starts the private Dashboard, installs the daily 30-day-retention backup job and sends one SMTP test from `zejiang_hydrology@126.com` to `ze.jiang@hhu.edu.cn`.
6. Confirm the test message in the recipient mailbox, then create the verification marker printed by the deployment script.
7. After the first complete post-launch calendar month has ended, run `sudo scripts/enable-analytics-reports.sh`. The worker then sends at 08:00 Asia/Hong_Kong on day 1. It is deliberately behind the `scheduled-reports` Compose profile before that point.

No historical logs are imported. Set `ANALYTICS_COLLECTED_SINCE` to the UTC production-launch timestamp. Website/WASP values that cannot be verified are reported as unavailable, never estimated or replaced with zero.

## Operations

- Apply migrations: `docker compose run --rm analytics-api python -m analytics_app.cli migrate`
- Reset the admin password: run `docker compose run --rm analytics-api python -m analytics_app.cli reset-password` (it revokes every session), replace `ANALYTICS_ADMIN_PASSWORD_HASH` in `.env` with the printed hash, then recreate `analytics-api`.
- Send a report manually: `docker compose run --rm analytics-api python -m analytics_app.cli send-report YYYY-MM` (add `--force` only for an intentional resend)
- Test SMTP only: `docker compose run --rm analytics-api python -m analytics_app.cli test-email`
- Back up now: `scripts/backup-analytics-db.sh`

Before activation, restore the latest custom-format dumps to disposable databases with `pg_restore --no-owner` and verify the Analytics tables and Umami schema. Database backups retain 30 days; analytics events themselves are retained for all-time reporting.
