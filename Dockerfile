# Container image for the hosted (streamable HTTP) mode.
#
# Deliberately not Cloudflare-specific: the same image runs on Cloud Run, Fly.io
# or any container host. The Cloudflare wiring lives in deploy/cloudflare/ and
# points here.
#
# Base install only — the `[gcp]` extra is NOT installed. Those three tools are
# preview, have never run against a live GCP project, and would change the tool
# list a connector directory syncs from the server. The hosted server exposes
# exactly the 24 tools the catalog work was verified against.

FROM python:3.12-slim AS build

WORKDIR /src
# pyproject reads README.md at build time (readme = "README.md"), so it has to
# be present or hatchling fails on a file that has nothing to do with the code.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

COPY --from=build /install /usr/local

# The server never needs to write outside the cache directory, so it runs as a
# non-root user that owns exactly that.
RUN useradd --create-home --uid 10001 datosgobdo \
    && mkdir -p /cache \
    && chown datosgobdo:datosgobdo /cache
USER datosgobdo

ENV DATOSGOBDO_TRANSPORT=streamable-http \
    DATOSGOBDO_HOST=0.0.0.0 \
    DATOSGOBDO_PORT=8080 \
    DATOSGOBDO_NETGUARD=public-only \
    DATOSGOBDO_CACHE_DIR=/cache \
    DATOSGOBDO_CACHE_MAX_BYTES=536870912 \
    DATOSGOBDO_DUCKDB_MEMORY=512MB \
    DATOSGOBDO_DUCKDB_THREADS=2 \
    PYTHONUNBUFFERED=1

# DATOSGOBDO_HOST must be 0.0.0.0 here and nowhere else: the default is
# 127.0.0.1, which is right for a local run and invisible from outside a
# container.
EXPOSE 8080

CMD ["datosgobdo-mcp"]
