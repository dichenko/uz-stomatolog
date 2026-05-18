# Dental Clinic Telegram Assistant

Python monorepo for a Telegram dental clinic administrative assistant MVP.

Current implementation includes the infrastructure foundation, database schema/repositories, Telegram webhook base with language selection, constrained clinic knowledge FAQ, and speech provider modules for Telegram voice input/output. LangGraph flows and calendar booking flows are intentionally not implemented yet.

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

## Clinic Knowledge FAQ

Initial clinic knowledge is stored in Markdown files:

```text
apps/bot/app/clinic_knowledge/ru.md
apps/bot/app/clinic_knowledge/uz.md
apps/bot/app/clinic_knowledge/en.md
```

On startup the app loads these files into `clinic_knowledge` if the table is empty. FAQ answers are constrained to the knowledge base; unknown questions receive a callback/admin clarification message instead of invented details.

## Speech

Voice messages are handled through isolated speech providers:

- Russian and English use OpenAI STT/TTS.
- Uzbek uses Muxlisa STT/TTS.
- Tests can use `MockSpeechProvider` without external API keys.

Temporary audio files are written to `SPEECH_TEMP_DIR` and deleted after transcription, TTS generation, and Telegram sending. OpenAI and Muxlisa API keys must stay in `.env`; they are never logged or sent to clients.

## Human Owner TODO

- Provide final Muxlisa credentials.
- Confirm exact OpenAI text/STT/TTS model choices after real voice QA.
- Configure Google Calendar service account.
- Provide real admin Telegram group ID.
- Provide final clinic knowledge base text in RU/UZ/EN.
- Provide final webhook domain.
- Decide whether Uzbek Cyrillic should become a separate UI language later.
- Decide whether reminders should support voice later.
- Confirm legal/medical disclaimer text before production launch.
