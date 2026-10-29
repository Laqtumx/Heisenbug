#!/usr/bin/env bash
# Container entrypoint for the hosted demo.
#
# The host provides Postgres but no MongoDB, so MongoDB runs inside this
# container. One service, one managed dependency.
#
# Boot order matters:
#   1. start mongod
#   2. wait for Postgres
#   3. apply migrations (idempotent)
#   4. start uvicorn
#   5. seed in the background, only when the data is missing
#
# Uvicorn starts before the seed so the health check passes straight away. A
# synchronous seed would block startup past the host's health-check window and
# the service would be killed and restarted in a loop.
#
# On a host with an ephemeral filesystem the embedded MongoDB is wiped on every
# cold start, so the seed guard checks Mongo as well as Postgres. A warm restart
# with both intact skips the seed entirely.
set -uo pipefail

MONGO_DBPATH="${MONGO_DBPATH:-/data/db}"
MONGO_PORT="${MONGO_PORT:-27017}"
SEED_LOG=/tmp/seed.log

log() { printf '[entrypoint] %s\n' "$*"; }

# ---------------------------------------------------------------- mongodb --
mkdir -p "$MONGO_DBPATH" /var/log/mongodb

if [ -z "${MONGO_URL:-}" ] || [[ "${MONGO_URL}" == *127.0.0.1* ]] || [[ "${MONGO_URL}" == *localhost* ]]; then
  log "starting embedded mongod on port ${MONGO_PORT}"
  mongod \
    --dbpath "$MONGO_DBPATH" \
    --bind_ip 127.0.0.1 \
    --port "$MONGO_PORT" \
    --wiredTigerCacheSizeGB 0.25 \
    --logpath /var/log/mongodb/mongod.log \
    --fork >/dev/null 2>&1 || log "mongod already running or failed to start"

  for _ in $(seq 1 30); do
    if python -c "
import sys
from pymongo import MongoClient
try:
    MongoClient('mongodb://127.0.0.1:${MONGO_PORT}', serverSelectionTimeoutMS=1500).admin.command('ping')
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      log "mongod is ready"
      break
    fi
    sleep 1
  done
  export MONGO_URL="${MONGO_URL:-mongodb://127.0.0.1:${MONGO_PORT}}"
else
  log "using external MONGO_URL"
fi

# --------------------------------------------------------------- postgres --
log "waiting for postgres"
for i in $(seq 1 60); do
  if python -c "
import sys
from sqlalchemy import create_engine
from app.config import settings
try:
    create_engine(settings.sqlalchemy_url, pool_pre_ping=True).connect().close()
except Exception as exc:
    print(exc, file=sys.stderr); sys.exit(1)
" 2>/dev/null; then
    log "postgres is ready"
    break
  fi
  if [ "$i" -eq 60 ]; then
    log "postgres did not become ready in time"
  fi
  sleep 2
done

# -------------------------------------------------------------- migrations --
log "applying migrations"
python -m app.db.migrate || log "migration run reported an error; continuing"

# ------------------------------------------------------------------- serve --
log "starting uvicorn on port ${PORT:-8000}"
uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
API_PID=$!

# -------------------------------------------------------------------- seed --
# Two cases need a seed:
#   * users == 0            a brand new database
#   * raw records == 0      a cold start wiped the embedded MongoDB while
#                           Postgres survived
# In the second case the batches in Postgres are stale: the raw records they
# point at are gone. Re-running the pipeline without --reset would add a second
# batch and double every count, so pass --reset to clear them first.
seed_mode() {
  python - <<'PY'
import sys

from app.db import mongo
from app.db.engine import service_session
from sqlalchemy import text

try:
    with service_session() as session:
        users = session.execute(text("select count(*) from public.app_users")).scalar() or 0
except Exception:
    print("fresh")
    sys.exit(0)

if users == 0:
    print("fresh")
    sys.exit(0)

try:
    raw = mongo.collection(mongo.RAW_RECORDS).estimated_document_count()
except Exception:
    raw = 0

print("reset" if raw == 0 else "skip")
PY
}

MODE="$(seed_mode)"
log "seed decision: ${MODE}"

if [ "$MODE" != "skip" ]; then
  log "seeding in the background; see ${SEED_LOG}"
  (
    # The scripts add backend/ to sys.path relative to their own location. In
    # this image the app lives at /app, so point PYTHONPATH at it directly.
    export PYTHONPATH=/app
    set -- --run-pipeline
    [ "$MODE" = "reset" ] && set -- --run-pipeline --reset
    python /app/scripts/generate_demo_data.py --out /app/data/legacy --quiet \
      && python /app/scripts/seed.py "$@"
    log "seed finished with status $?"
  ) >"$SEED_LOG" 2>&1 &
else
  log "data already present; skipping the seed"
fi

wait "$API_PID"
