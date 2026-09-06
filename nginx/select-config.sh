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

# The two static research tools are deployed as one unit. Loading neither
# configuration until both certificates exist keeps a partial ACME run from
# preventing the existing proxy from starting.
if [ "${NGINX_CONFIG:-nginx.conf}" != "nginx.bootstrap.conf" ] &&
   [ -s /etc/letsencrypt/live/wqm.hydroclimatex.com/fullchain.pem ] &&
   [ -s /etc/letsencrypt/live/wqm.hydroclimatex.com/privkey.pem ] &&
   [ -s /etc/letsencrypt/live/synthesis.hydroclimatex.com/fullchain.pem ] &&
   [ -s /etc/letsencrypt/live/synthesis.hydroclimatex.com/privkey.pem ]; then
  cat /opt/wasp/static-tools.conf >> /etc/nginx/conf.d/default.conf
fi
