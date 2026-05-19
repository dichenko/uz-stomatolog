#!/usr/bin/env sh
set -eu

COMPOSE_FILE="infra/docker-compose.yml"

# Load .env if present so postgres vars are available for pg_isready
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

echo "=== Pulling latest images and rebuilding ==="
docker compose -f "$COMPOSE_FILE" pull --quiet 2>/dev/null || true
docker compose -f "$COMPOSE_FILE" build --pull --quiet bot

echo "=== Starting services ==="
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "=== Waiting for PostgreSQL ==="
MAX_WAIT=30
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
  if docker compose -f "$COMPOSE_FILE" exec -T postgres pg_isready -q 2>/dev/null; then
    echo "PostgreSQL ready after ${WAITED}s"
    break
  fi
  sleep 2
  WAITED=$((WAITED + 2))
done
if [ $WAITED -ge $MAX_WAIT ]; then
  echo "Warning: PostgreSQL not ready after ${MAX_WAIT}s, proceeding anyway..."
fi

echo "=== Running DB migrations ==="
docker compose -f "$COMPOSE_FILE" exec -T bot alembic upgrade head

echo "=== Container status ==="
docker compose -f "$COMPOSE_FILE" ps

echo "=== Recent logs ==="
docker compose -f "$COMPOSE_FILE" logs --tail=50 bot
