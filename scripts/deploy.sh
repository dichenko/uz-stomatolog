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
PG_USER="${POSTGRES_USER:-dental_bot}"
PG_DB="${POSTGRES_DB:-dental_bot}"
docker compose -f "$COMPOSE_FILE" exec -T bot \
  sh -c "while ! pg_isready -h postgres -U \"$PG_USER\" -d \"$PG_DB\" 2>/dev/null; do sleep 2; done" || true
sleep 3

echo "=== Running DB migrations ==="
docker compose -f "$COMPOSE_FILE" exec -T bot alembic upgrade head

echo "=== Container status ==="
docker compose -f "$COMPOSE_FILE" ps

echo "=== Recent logs ==="
docker compose -f "$COMPOSE_FILE" logs --tail=50 bot
