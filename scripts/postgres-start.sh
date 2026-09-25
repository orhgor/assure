#!/bin/sh
# Start PostgreSQL and make the role's password follow POSTGRES_PASSWORD.
#
# The official image sets the password only when it initialises an empty data
# directory. Every later `.env` change (scripts/gen-env.sh regenerates
# POSTGRES_PASSWORD on each run; the first `docker compose up` may have created
# the volume with the default) leaves the volume on the old password and the
# app dies at boot with "password authentication failed for user assure"
# (EC2, 2026-09-25). The container's own unix socket is trust-authenticated, so
# after the server is up we ALTER the role to whatever .env says — no old
# password needed. Idempotent; runs on every start.
set -eu
docker-entrypoint.sh postgres \
  -c shared_buffers=128MB -c max_connections=100 -c log_min_duration_statement=500 &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null' TERM INT

user="${POSTGRES_USER:-assure}"
db="${POSTGRES_DB:-assure}"
# ' → '' for the SQL literal
pw=$(printf '%s' "${POSTGRES_PASSWORD:-assure}" | sed "s/'/''/g")
i=0
until pg_isready -q -U "$user" -d "$db" -h /var/run/postgresql 2>/dev/null; do
  i=$((i + 1)); [ "$i" -ge 120 ] && break; sleep 1
done
if psql -q -v ON_ERROR_STOP=1 -h /var/run/postgresql -U "$user" -d "$db" \
     -c "ALTER USER \"$user\" PASSWORD '$pw'" >/dev/null 2>&1; then
  echo "assure-postgres-start: role \"$user\" password set from POSTGRES_PASSWORD"
else
  echo "assure-postgres-start: could not sync the password (server not ready?)" >&2
fi
wait "$pid"
