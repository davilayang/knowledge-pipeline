#!/usr/bin/env bash
# docker-entrypoint-initdb.d script — runs once at first container startup,
# after 01-create-knowledge-db.sh has created the knowledge_pipeline DB.
#
# Loads packages/domains/src/domains/wiki/schema/wiki.sql into the
# knowledge_pipeline database (mounted at /wiki-schema in compose). All
# DDL is idempotent; safe-by-construction even if re-applied.
#
# Schema CHANGE workflow (per the rebuild-don't-migrate decision):
#   docker compose down -v   # destroys postgres_data volume
#   docker compose up -d postgres   # init scripts re-run on fresh volume

set -euo pipefail

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname knowledge_pipeline \
    -f /wiki-schema/wiki.sql
