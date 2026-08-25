#!/bin/sh
set -eu

psql --username "$POSTGRES_USER" --dbname postgres \
  --set=analytics_password="$ANALYTICS_DB_PASSWORD" \
  --set=umami_password="$UMAMI_DB_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE analytics LOGIN PASSWORD %L', :'analytics_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics') \gexec
SELECT format('CREATE ROLE umami LOGIN PASSWORD %L', :'umami_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'umami') \gexec
SELECT 'CREATE DATABASE analytics OWNER analytics'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'analytics') \gexec
SELECT 'CREATE DATABASE umami OWNER umami'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'umami') \gexec
SQL
