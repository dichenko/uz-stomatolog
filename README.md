# Dental Clinic Telegram Assistant

Python monorepo for a Telegram dental clinic administrative assistant MVP.

Current implementation includes the infrastructure foundation, database schema/repositories, and Telegram webhook base with language selection. LangGraph flows, speech providers, and external API integrations are intentionally not implemented yet.

## Stack

- Python 3.12
- FastAPI HTTP entrypoint for health checks and Telegram webhook
- PostgreSQL 16
- Docker Compose
- Pydantic Settings
- Structured JSON logs

## Local Setup

Create a local env file:

```sh
cp .env.example .env
```

Start the app and PostgreSQL:

```sh
docker compose -f infra/docker-compose.yml up -d --build
```

Check health:

```sh
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"OK"}
```

View logs:

```sh
docker compose -f infra/docker-compose.yml logs -f bot
```

## Development Notes

- Do not commit `.env` or credentials.
- Runtime secrets should live in `.env` locally and on the VPS.
- The bot service is mapped to `127.0.0.1:8000:8000` so Caddy can reverse proxy HTTPS traffic to it.

## Database

Run migrations inside the bot container:

```sh
docker compose -f infra/docker-compose.yml exec bot alembic upgrade head
```

Run repository tests locally from `apps/bot` after installing dev dependencies:

```sh
pip install -e ".[dev]"
pytest
```

## Telegram Webhook

The webhook endpoint is configured by `TELEGRAM_WEBHOOK_PATH` and defaults to:

```text
/telegram/webhook
```

Production startup registers the webhook only when `APP_ENV=prod` and `TELEGRAM_BOT_TOKEN` is configured. Local development can still receive webhook requests through a tunnel or dev domain.

Telegram webhook requests are checked against `TELEGRAM_WEBHOOK_SECRET` using the `X-Telegram-Bot-Api-Secret-Token` header when the secret is configured.

## Human Owner TODO

- Provide final Muxlisa API documentation and credentials.
- Choose exact OpenAI text/STT/TTS models.
- Configure Google Calendar service account.
- Provide real admin Telegram group ID.
- Provide final clinic knowledge base text in RU/UZ/EN.
- Provide final webhook domain.
- Decide whether Uzbek Cyrillic should become a separate UI language later.
- Decide whether reminders should support voice later.
- Confirm legal/medical disclaimer text before production launch.
