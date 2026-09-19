# Host Nginx front door

The Alibaba Cloud host Nginx owns public ports 80 and 443. It continues to
proxy the existing Nextcloud site through FRP on port 18082. The HydroclimateX
Docker Nginx listens only on `127.0.0.1:18080` and `127.0.0.1:18443` and serves
the other six application domains behind the host proxy.

Do not open ports 18080, 18443, or 18082 in the Alibaba Cloud security group.
Do not edit `/etc/nginx/sites-available/default`, restart FRP, or change the
Nextcloud tunnel during this rollout.

## Deploy

From the server checkout:

```bash
cd /opt/hydroclimatex-wasp/repo
git pull --ff-only origin main
docker compose config --quiet
sudo ./deploy-host-frontdoor.sh
```

The script verifies `https://cloud.hydroclimatex.com/status.php` before and
after the cutover, checks all six existing certificates, starts the Docker
proxy on loopback, runs `nginx -t`, and gracefully reloads the host Nginx. It
does not stop or restart Nextcloud or FRP. A rollback snapshot is retained
under `/opt/hydroclimatex-wasp/state/frontdoor-backups/`. It also updates the
existing certificate-renewal command so successful renewals reload both the
Docker proxy and the host Nginx.

## Verify

```bash
sudo ss -ltnp | grep -E ':(80|443|18080|18443|18082)\b'
sudo systemctl is-active nginx frps
sudo docker compose ps
sudo nginx -t

curl -fsS https://cloud.hydroclimatex.com/status.php
curl -fsS https://wasp.hydroclimatex.com/api/health
curl -fsS https://lisflood.hydroclimatex.com/health
curl -fsS https://analytics.hydroclimatex.com/health
curl -fsS https://telemetry.hydroclimatex.com/config.json
curl -fsS https://wqm.hydroclimatex.com/health
curl -fsS https://synthesis.hydroclimatex.com/health
```

Confirm in a Nextcloud client that an existing synchronized file can still be
updated in both directions. Check the public certificates with:

```bash
for domain in wasp lisflood analytics telemetry wqm synthesis; do
  echo | openssl s_client -connect "$domain.hydroclimatex.com:443" \
    -servername "$domain.hydroclimatex.com" 2>/dev/null \
    | openssl x509 -noout -subject -dates
done
```
