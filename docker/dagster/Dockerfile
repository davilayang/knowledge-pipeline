# Shared image for dagster-webserver and dagster-daemon.
# Contains only Dagster infrastructure packages — no user code.
FROM python:3.13-slim

RUN pip install --no-cache-dir \
    dagster \
    dagster-graphql \
    dagster-webserver \
    dagster-postgres

ENV DAGSTER_HOME=/opt/dagster/home/
RUN mkdir -p $DAGSTER_HOME

COPY configs/dagster.yaml configs/workspace.yaml $DAGSTER_HOME

WORKDIR $DAGSTER_HOME
