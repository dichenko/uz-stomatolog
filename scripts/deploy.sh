#!/usr/bin/env sh
set -eu

COMPOSE_FILE="infra/docker-compose.yml"
MSG_SUCCESS="✅ Deploy <b>successful</b> on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
MSG_FAILED="❌ Deploy <b>FAILED</b> on $(hostname) at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Load .env if present
if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

_notify() {
  _msg="$1"
  _token="${TELEGRAM_BOT_TOKEN:-}"
  _chat="${DEV_ADMIN_TG_ID:-}"
  if [ -n "$_token" ] && [ -n "$_chat" ]; then
    curl -sS -X POST "https://api.telegram.org/bot${_token}/sendMessage" \
      -d "chat_id=${_chat}" \
      -d "text=${_msg}" \
      -d "parse_mode=HTML" \
      > /dev/null 2>&1 || true
  fi
}

run_deploy() {

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

}

set +e
run_deploy
_exit_code=$?
set -e

if [ $_exit_code -eq 0 ]; then
  _notify "$MSG_SUCCESS"
else
  _notify "$MSG_FAILED"
  exit 1
fi
