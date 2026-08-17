#!/bin/sh
set -eu

case "${NGINX_CONFIG:-nginx.conf}" in
  nginx.conf)
    cp /opt/wasp/nginx.conf /etc/nginx/conf.d/default.conf
    ;;
  nginx.bootstrap.conf)
    cp /opt/wasp/nginx.bootstrap.conf /etc/nginx/conf.d/default.conf
    ;;
  *)
    printf 'Unsupported NGINX_CONFIG: %s\n' "$NGINX_CONFIG" >&2
    exit 1
    ;;
esac
