#!/usr/bin/env bash
# docker-entrypoint-initdb.d script — runs once at first container startup.
#
# Creates the `knowledge_pipeline` database on the same Postgres instance
# used by Dagster (POSTGRES_DB / DAGSTER_PG_DB). Safe to re-run: the
# SELECT ... \gexec pattern only issues CREATE DATABASE when the row is
# absent, so it is a no-op on subsequent runs (though initdb.d itself only
# runs when the data directory is empty).
#
# The script runs as POSTGRES_USER against the default database (POSTGRES_DB)
# because that is how docker-entrypoint-initdb.d works.

set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
    SELECT 'CREATE DATABASE knowledge_pipeline'
    WHERE NOT EXISTS (
        SELECT 1 FROM pg_database WHERE datname = 'knowledge_pipeline'
    )
    \gexec
EOSQL
