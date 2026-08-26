# HydroClimateX Private Analytics

The platform runs two public origins on the existing Hong Kong host:

- `analytics.hydroclimatex.com` — administrator login, Dashboard and protected APIs.
- `telemetry.hydroclimatex.com` — only `config.json`, `script.js` and `POST /api/send` are public.

PostgreSQL, Umami administration and WASP event ingestion have no host ports. Nginx access logs contain only timestamp, status, normalized route, method and duration.

## Production rollout

1. Provision at least approximately 2 vCPU and 4 GB RAM. Point both new A records to `8.210.252.61` and do not publish premature AAAA records.
2. Supply the required values listed in `.env.example`. They may be stored in a mode-0600 `.env`, or exported directly in the root deployment shell when no secrets file should remain on the server. Generate the administrator hash with `python -m analytics_app.cli hash-password`, and generate the service secrets with a cryptographically secure password manager. Use URL-safe database passwords in both the init variables and database URLs. `UMAMI_API_USERNAME`, `UMAMI_API_PASSWORD` and `UMAMI_WEBSITE_ID` may be left blank; the deployment script fills them automatically. If they are absent (for example an initialization failure), the Dashboard marks Website Analytics `configuration missing` while WASP Analytics remains operational.
3. No manual Umami configuration is required. Umami has no host port and its admin UI stays private; `deploy-analytics.sh` runs an idempotent `init-umami` step inside the analytics container that creates a `dashboard-reader` Umami account (role `user`), creates one website whose allowed domains are exactly `hydroclimatex.com,www.hydroclimatex.com` (owned by that reader so it can read its stats), and prints the credentials. The script writes `UMAMI_API_USERNAME`, `UMAMI_API_PASSWORD` and `UMAMI_WEBSITE_ID` into `.env` and locks it to mode 600. Re-running the deployment never duplicates the user or the website.
4. Run `sudo ./deploy-analytics.sh`. It downloads the current DB-IP Country Lite MMDB database without an account or API key, validates it inside the WASP container, performs the resource/DNS preflight, starts PostgreSQL and Umami, migrates Analytics, initializes Umami, starts the private Dashboard, obtains separate certificates, installs the daily 30-day-retention backup job and sends one SMTP test from `zejiang_hydrology@126.com` to `ze.jiang@hhu.edu.cn`.
5. DB-IP Country Lite is updated monthly. Re-running `sudo scripts/install-dbip-country-lite.sh` refreshes the local country database; recreate `wasp-api` afterward so its reader opens the new file. The database is runtime state and must not be committed.
6. Confirm the test message in the recipient mailbox, then create the verification marker printed by the deployment script.
7. After the first complete post-launch calendar month has ended, run `sudo scripts/enable-analytics-reports.sh`. The worker then sends at 08:00 Asia/Hong_Kong on day 1. It is deliberately behind the `scheduled-reports` Compose profile before that point.

No historical logs are imported. Set `ANALYTICS_COLLECTED_SINCE` to the UTC production-launch timestamp. Website/WASP values that cannot be verified are reported as unavailable, never estimated or replaced with zero. `init-umami` authenticates with the Umami admin account (`admin`/`umami` by default); if that password was rotated manually, export `UMAMI_ADMIN_USERNAME` and `UMAMI_ADMIN_PASSWORD` in the deployment shell (or `.env`) before re-running it.

## Operations

- Apply migrations: `docker compose run --rm analytics-api python -m analytics_app.cli migrate`
- (Re)initialize Umami: `docker compose run --rm analytics-api python -m analytics_app.cli init-umami` — idempotent; prints `UMAMI_API_USERNAME`, `UMAMI_API_PASSWORD`, `UMAMI_WEBSITE_ID` for a host-side `.env` update and `chmod 600` if run outside the deployment script.
- Reset the admin password: run `docker compose run --rm analytics-api python -m analytics_app.cli reset-password` and enter a password containing at least 10 characters (it revokes every session), replace `ANALYTICS_ADMIN_PASSWORD_HASH` in `.env` with the printed hash, then recreate `analytics-api`.
- Send a report manually: `docker compose run --rm analytics-api python -m analytics_app.cli send-report YYYY-MM` (add `--force` only for an intentional resend)
- Test SMTP only: `docker compose run --rm analytics-api python -m analytics_app.cli test-email`
- Back up now: `scripts/backup-analytics-db.sh`

Before activation, restore the latest custom-format dumps to disposable databases with `pg_restore --no-owner` and verify the Analytics tables and Umami schema. Database backups retain 30 days; analytics events themselves are retained for all-time reporting.

Country-level geolocation uses the local [DB-IP Country Lite](https://db-ip.com) database under the Creative Commons Attribution 4.0 license. Only the resulting ISO country code is retained; source IP addresses are discarded immediately.
