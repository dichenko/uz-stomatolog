# Dental Clinic Telegram Assistant

Python monorepo for a Telegram dental clinic administrative assistant MVP.

Current state: Milestone 0 foundation only. Telegram handlers, database models, LangGraph flows, speech providers, and external API integrations are intentionally not implemented yet.

## Stack

- Python 3.12
- FastAPI HTTP entrypoint for health checks and future Telegram webhook
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
