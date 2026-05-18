#!/usr/bin/env sh
set -eu

if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
  echo "TELEGRAM_BOT_TOKEN is required" >&2
  exit 1
fi

if [ -z "${APP_BASE_URL:-}" ]; then
  echo "APP_BASE_URL is required" >&2
  exit 1
fi

TELEGRAM_WEBHOOK_PATH="${TELEGRAM_WEBHOOK_PATH:-/telegram/webhook}"
WEBHOOK_URL="${APP_BASE_URL}${TELEGRAM_WEBHOOK_PATH}"

curl -fsS \
  -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${WEBHOOK_URL}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET:-}"
