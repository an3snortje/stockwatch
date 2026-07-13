# Standalone stockwatch sidecar: a small long-lived container that sits next to
# your existing n8n and runs `stockwatch snapshot-all` on demand (via
# `docker exec`). It does NOT replace or modify your n8n container.
#
# Debian slim keeps the Microsoft ODBC Driver 18 install simple and well-supported.
#
# Build from the REPO ROOT (the package must be in the build context):
#   docker compose -f deploy/n8n/docker-compose.yml build
FROM python:3.12-slim

# --- Microsoft ODBC Driver 18 (Debian 12 / bookworm) -----------------------
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
    && sed -i 's|https://|[signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://|' /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -y gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# --- stockwatch ------------------------------------------------------------
COPY . /opt/stockwatch-src
RUN pip install --no-cache-dir /opt/stockwatch-src

# tables.yml ships in the package at /opt/stockwatch-src/config/tables.yml;
# the snapshot command points --config there. Mount your own copy over it to
# tune mappings without a rebuild (see README).

# Idle so the container stays up for `docker exec`. Snapshots run on demand,
# driven by your n8n (or a host cron). See README for the self-scheduling variant.
CMD ["sleep", "infinity"]
