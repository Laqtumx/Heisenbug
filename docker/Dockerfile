# CleanSlate API, self-contained for hosts that offer no MongoDB.
#
# The managed hosts in play (Render free, and most equivalents) provide Postgres
# but not Mongo. The pipeline needs Mongo for the raw landing zone, the
# quarantine set and the ingest manifests, so MongoDB runs inside this image
# next to the API. One service, one managed dependency.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t cleanslate-api .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# mongodb-org-server for the embedded database, libfreetype6 for reportlab
# fonts, curl for the healthcheck.
#
# MongoDB is installed from the official tarball rather than the apt repo: the
# repo's signing key still uses SHA1, which current Debian images reject during
# apt-get update. The tarball has no such dependency and is pinned by version.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates libfreetype6 \
        libcurl4 libgssapi-krb5-2 liblzma5 libssl3 zlib1g \
    && curl -fsSL "https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-debian12-7.0.14.tgz" \
        -o /tmp/mongo.tgz \
    && tar -xzf /tmp/mongo.tgz -C /opt \
    && mv /opt/mongodb-linux-x86_64-debian12-7.0.14 /opt/mongodb \
    && ln -s /opt/mongodb/bin/mongod /usr/local/bin/mongod \
    && rm /tmp/mongo.tgz \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# The app, its migrations, and the scripts the entrypoint seeds with.
COPY backend/app ./app
COPY backend/db ./db
COPY scripts ./scripts
COPY docker/entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh && mkdir -p /app/var/uploads /data/db /app/data/legacy

EXPOSE 8000

# Render and Railway set PORT. Default to 8000.
CMD ["./entrypoint.sh"]
