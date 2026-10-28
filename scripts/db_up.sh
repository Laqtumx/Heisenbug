#!/usr/bin/env bash
# Start the local Postgres and Mongo containers.
#
# Both run with --network host to avoid the container NAT quirks that make
# local database access unreliable in sandboxed environments. Ports are
# deliberately non-default (5433, 27018) so they never collide with a database
# the developer already has running.
set -uo pipefail

PG_PORT="${PG_PORT:-5433}"
MONGO_PORT="${MONGO_PORT:-27018}"
DATA_DIR="${DATA_DIR:-/workspace/.data}"

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Starting it..."
  sudo nohup dockerd >/tmp/dockerd.log 2>&1 &
  for _ in $(seq 1 30); do
    docker info >/dev/null 2>&1 && break
    sleep 1
  done
  sudo chmod 666 /var/run/docker.sock 2>/dev/null || true
fi

if ! docker info >/dev/null 2>&1; then
  echo "Could not reach the Docker daemon. See /tmp/dockerd.log" >&2
  exit 1
fi

mkdir -p "$DATA_DIR/pg" "$DATA_DIR/mongo"

start_pg() {
  if docker ps --format '{{.Names}}' | grep -qx cleanslate-pg; then
    echo "cleanslate-pg already running"
    return
  fi
  docker rm -f cleanslate-pg >/dev/null 2>&1 || true
  docker run -d --name cleanslate-pg --network host \
    -e POSTGRES_PASSWORD=cleanslate \
    -e POSTGRES_USER=cleanslate \
    -e POSTGRES_DB=cleanslate \
    -v "$DATA_DIR/pg:/var/lib/postgresql/data" \
    postgres:16 -c port="$PG_PORT" >/dev/null
  echo "cleanslate-pg starting on port $PG_PORT"
}

start_mongo() {
  if docker ps --format '{{.Names}}' | grep -qx cleanslate-mongo; then
    echo "cleanslate-mongo already running"
    return
  fi
  docker rm -f cleanslate-mongo >/dev/null 2>&1 || true
  docker run -d --name cleanslate-mongo --network host \
    -v "$DATA_DIR/mongo:/data/db" \
    mongo:7 --port "$MONGO_PORT" >/dev/null
  echo "cleanslate-mongo starting on port $MONGO_PORT"
}

start_pg
start_mongo

echo "waiting for databases to accept connections..."
for _ in $(seq 1 60); do
  pg_ok=false
  mongo_ok=false
  docker exec cleanslate-pg pg_isready -p "$PG_PORT" -U cleanslate >/dev/null 2>&1 && pg_ok=true
  docker exec cleanslate-mongo mongosh --port "$MONGO_PORT" --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1 && mongo_ok=true
  if $pg_ok && $mongo_ok; then
    echo "Postgres and Mongo are ready."
    exit 0
  fi
  sleep 1
done

echo "Databases did not become ready in time." >&2
docker logs --tail 20 cleanslate-pg 2>&1 | sed 's/^/  pg: /' >&2 || true
docker logs --tail 20 cleanslate-mongo 2>&1 | sed 's/^/  mongo: /' >&2 || true
exit 1
