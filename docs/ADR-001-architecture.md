# ADR-001: HydroclimateX and WASP production architecture

**Date:** 2026-08-17
**Status:** Accepted

## Context

HydroclimateX needs a research-team website, a durable scientific description
of WASP, and a compute-backed application. These have different publishing and
security requirements, so they are deliberately separate deployments.

## Decision

The public mapping is:

| URL | Responsibility | Platform |
|---|---|---|
| `https://hydroclimatex.com` | HydroclimateX research-team homepage | GitHub Pages |
| `https://hydroclimatex.com/showcase/wasp-web/` | WASP overview, methodology, publications, input contract, and software links | GitHub Pages |
| `https://wasp.hydroclimatex.com` | Interactive WASP application and API | Alibaba Cloud Hong Kong Lightweight Application Server |

The Pages workflow stages the homepage, the scientific introduction, figures,
and public data. It explicitly excludes `wasp-app/`, the backend, container
configuration, certificates, and runtime state.

The Hong Kong server runs two long-lived containers from explicitly tagged,
locally built images:

- `wasp-nginx` uses a baked Nginx image containing `wasp-app/` and both the
  bootstrap and final proxy configurations. Repository bind mounts cannot
  silently change the running application.
- `wasp-api` runs FastAPI on Docker's internal network and only `expose`s port
  8000. The host never publishes that port.

The browser uses same-origin paths such as `/api/health`, `/api/demo-data`, and
`/api/wasp/predict`. Nginx preserves the `/api` prefix when proxying. This avoids
mixed content and removes cross-origin dependencies from normal production use.

## Deployment responsibilities

`scripts/bootstrap-hk-server.sh` prepares a fresh Ubuntu 24.04 host. It installs
Docker Engine, the Compose plugin, Git, curl, CA certificates, cron, DNS tools,
and OpenSSL; creates
`/opt/hydroclimatex-wasp`; clones or safely fast-forwards the selected branch;
then invokes `deploy.sh`.

`deploy.sh` is the application deployment guard. It verifies DNS resolves to
`8.210.252.61`, prepares the persistent state directory, starts a restricted
HTTP configuration for the ACME challenge, obtains or reuses the certificate,
switches to the TLS configuration, waits for container health, and verifies
`https://wasp.hydroclimatex.com/api/health` locally before reporting success.
Before candidate images are built, the guard records both running image IDs.
If candidate readiness fails, automatic rollback retags and recreates both old
images, then checks the restored HTTPS health endpoint. Nginx is stopped only
when no prior healthy pair exists or the rollback itself fails. The guard also
installs the daily certificate-renewal command and reloads Nginx only after a
successful renewal.

Nginx permits an 11 MB request body so multipart framing has headroom, while
FastAPI independently enforces a 10 MiB maximum uploaded file.

Certificates and ACME files live under `/opt/hydroclimatex-wasp/state`; the Git
checkout lives under `/opt/hydroclimatex-wasp/repo`. Updating code therefore
does not replace certificate state.

## Consequences

- The research site remains static, inexpensive, and independently deployable.
- The scientific introduction remains available even during compute downtime.
- The application has one canonical origin and a private backend port.
- Deployment success means both container health and a real TLS API response,
  rather than merely a successful `docker compose up` exit code.
