# Custom n8n image with the stockwatch CLI + Microsoft ODBC Driver 18 baked in,
# so an n8n "Execute Command" node can run `stockwatch snapshot-all` directly
# against the iSync Azure SQL database.
#
# Build from the REPO ROOT so the whole package is in the build context:
#   docker compose -f deploy/n8n/docker-compose.yml build
ARG N8N_VERSION=1.70.0
FROM docker.n8n.io/n8nio/n8n:${N8N_VERSION}

USER root

# --- Microsoft ODBC Driver 18 (Alpine / musl build) ------------------------
# Version-pinned .apk from Microsoft. Bump MSODBC_VERSION when a newer Alpine
# build ships: https://learn.microsoft.com/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos#alpine17
ARG MSODBC_VERSION=18.4.1.1-1
RUN apk add --no-cache curl \
    && curl -fsSL -O "https://download.microsoft.com/download/3/5/5/355d7943-a338-41a7-858d-53b259ea33f5/msodbcsql18_${MSODBC_VERSION}_amd64.apk" \
    && apk add --allow-untrusted "msodbcsql18_${MSODBC_VERSION}_amd64.apk" \
    && rm -f "msodbcsql18_${MSODBC_VERSION}_amd64.apk"

# --- Python + stockwatch ---------------------------------------------------
# unixodbc is the runtime; the .build group compiles pyodbc, then is removed.
RUN apk add --no-cache python3 py3-pip unixodbc \
    && apk add --no-cache --virtual .build gcc g++ musl-dev python3-dev unixodbc-dev
COPY . /opt/stockwatch-src
RUN python3 -m venv /opt/stockwatch \
    && /opt/stockwatch/bin/pip install --no-cache-dir /opt/stockwatch-src \
    && ln -s /opt/stockwatch/bin/stockwatch /usr/local/bin/stockwatch \
    && apk del .build

# config/tables.yml lives at /opt/stockwatch-src/config/tables.yml — the
# snapshot workflow points --config at it. To tune mappings without a rebuild,
# mount your own tables.yml over that path (see README).

USER node
