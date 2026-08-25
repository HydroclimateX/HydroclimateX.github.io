#!/bin/sh
set -eu

case "${NGINX_CONFIG:-nginx.conf}" in
  nginx.conf)
    cp /opt/wasp/nginx.conf /etc/nginx/conf.d/default.conf
    ;;
  nginx.bootstrap.conf)
    cp /opt/wasp/nginx.bootstrap.conf /etc/nginx/conf.d/default.conf
    ;;
  nginx.analytics.conf)
    cp /opt/wasp/nginx.analytics.conf /etc/nginx/conf.d/default.conf
    ;;
  *)
    printf 'Unsupported NGINX_CONFIG: %s\n' "$NGINX_CONFIG" >&2
    exit 1
    ;;
esac

# Serve LISFLOOD from the same proxy only once its certificate exists, so a
# missing LISFLOOD deployment never blocks wasp/analytics/telemetry. The
# bootstrap proxy already answers LISFLOOD's ACME challenge itself.
if [ "${NGINX_CONFIG:-nginx.conf}" != "nginx.bootstrap.conf" ] &&
   [ -s /etc/letsencrypt/live/lisflood.hydroclimatex.com/fullchain.pem ] &&
   [ -s /etc/letsencrypt/live/lisflood.hydroclimatex.com/privkey.pem ]; then
  cat /opt/wasp/lisflood.conf >> /etc/nginx/conf.d/default.conf
fi
