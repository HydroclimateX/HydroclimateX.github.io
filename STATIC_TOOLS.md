# WQM and synthesis deployment checklist

The two interactive applications are static assets served by the existing Nginx container. They add no API, database, persistent volume or Compose service.

WQM remains a verified static reference run: the browser exposes the station, fixed QDM/Morlet configuration, actual CWT level count, wet-day threshold and ensemble member, then redraws the held-out results when the user runs the analysis. Arbitrary method, wavelet or level choices require `WQM::bc_cwt()` in R and are intentionally not simulated in JavaScript.

## Before deployment

- Point `wqm.hydroclimatex.com` and `synthesis.hydroclimatex.com` to the single A record `8.210.252.61`; do not publish AAAA records yet.
- Confirm the current `wasp-nginx` container is healthy and its existing certificates are valid.
- Pull this revision onto the server. Do not copy certificate material into the repository.

## Local checks

```sh
python -m unittest tests.test_https_pages_deployment tests.test_homepage_styles lisflood_runner.test_web
docker compose config --quiet
bash -n deploy-static-tools.sh
sh -n nginx/select-config.sh
```

With Docker running, build the Nginx image in a non-production checkout before deployment:

```sh
docker compose build nginx
```

## Deploy

Run from the repository root on the production host:

```sh
sudo ./deploy-static-tools.sh
```

The script verifies DNS, records the current Nginx image and configuration, obtains only missing certificates, restores the previous base configuration with both tool sites enabled, and checks both HTTPS health endpoints. An error after the proxy changes triggers restoration of the saved image and configuration.

## After deployment

- Confirm `https://wqm.hydroclimatex.com/health` and `https://synthesis.hydroclimatex.com/health` return `healthy`.
- Open both sites, change every model/site selector and download one CSV from each.
- Confirm the existing WASP, analytics, telemetry and LISFLOOD endpoints remain healthy.
- Leave the existing global `certbot renew` schedule unchanged.
