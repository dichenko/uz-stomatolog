#!/usr/bin/env sh
set -eu

COMPOSE_FILE="infra/docker-compose.yml"
PROJECT_NAME="dental-bot"

echo "=== Pulling latest images and rebuilding ==="
docker compose -f "$COMPOSE_FILE" pull --quiet 2>/dev/null || true
docker compose -f "$COMPOSE_FILE" build --pull --quiet bot

echo "=== Starting services ==="
docker compose -f "$COMPOSE_FILE" up -d --remove-orphans

echo "=== Waiting for PostgreSQL ==="
docker compose -f "$COMPOSE_FILE" exec -T bot \
  sh -c 'while ! pg_isready -h postgres -U dental_bot -d dental_bot 2>/dev/null; do sleep 2; done' || true
sleep 3

echo "=== Running DB migrations ==="
docker compose -f "$COMPOSE_FILE" exec -T bot alembic upgrade head

echo "=== Container status ==="
docker compose -f "$COMPOSE_FILE" ps

echo "=== Recent logs ==="
docker compose -f "$COMPOSE_FILE" logs --tail=50 bot
