# Shared image for dagster-webserver and dagster-daemon.
# Contains only Dagster infrastructure packages — no user code.
FROM python:3.13-slim

# Pin to the same versions resolved by uv.lock for the code-server image.
# Mismatched gRPC versions between webserver/daemon and the code server will
# surface as confusing "incompatible" errors at runtime.
ARG DAGSTER_VERSION=1.13.3
ARG DAGSTER_POSTGRES_VERSION=0.29.3

# Separate pip layer so editing dagster.yaml/workspace.yaml doesn't bust the cache.
RUN pip install --no-cache-dir \
    "dagster==${DAGSTER_VERSION}" \
    "dagster-graphql==${DAGSTER_VERSION}" \
    "dagster-webserver==${DAGSTER_VERSION}" \
    "dagster-postgres==${DAGSTER_POSTGRES_VERSION}"

ENV DAGSTER_HOME=/opt/dagster/home/
RUN mkdir -p $DAGSTER_HOME

COPY configs/dagster.yaml configs/workspace.yaml $DAGSTER_HOME

WORKDIR $DAGSTER_HOME
