# Dental Clinic Telegram Assistant

Python monorepo for a Telegram dental clinic administrative assistant MVP.

Current implementation includes: infrastructure foundation, database schema/repositories, Telegram webhook base with language selection, clinic knowledge FAQ, speech provider modules, LangGraph controlled flows, medical safety/escalation, Google Calendar integration, booking/cancellation/rescheduling flows, worker loops (reminders and calendar sync), and tracer integration (LangSmith + OpenTelemetry flags).

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

## Debugging and Observability

### Logs

All logs are structured JSON. To inspect logs:

```sh
docker compose -f infra/docker-compose.yml logs -f bot
```

Every log entry includes a `timestamp`, `level`, `logger`, and `message`. Extra fields are embedded directly in the JSON record.

### Trace ID

Every Telegram update receives a `trace_id` (hex string). This ID is stored in:

- `messages` table (`trace_id` column)
- `appointments` table (`created_trace_id` column)
- `execution_runs` table (`trace_id` column)
- Calendar event descriptions
- Log entries (via `extra={"trace_id": "..."}`)

To inspect an execution by trace ID:

```sh
# View the execution run summary
docker compose -f infra/docker-compose.yml exec bot \
  python -c "
from app.db.session import async_session_factory
from app.db.repositories import ExecutionRunRepository
import asyncio

async def inspect(trace_id):
    async with async_session_factory() as session:
        repo = ExecutionRunRepository(session)
        # Query by trace_id via SQLAlchemy
        from sqlalchemy import select
        from app.db.models import ExecutionRun
        result = await session.execute(
            select(ExecutionRun).where(ExecutionRun.trace_id == trace_id)
        )
        run = result.scalar_one_or_none()
        if run:
            print(f'Intent: {run.intent}')
            print(f'Status: {run.status}')
            print(f'Duration: {run.duration_ms}ms')
            print(f'Input: {run.graph_input}')
            print(f'Output: {run.graph_output}')
            print(f'Tool calls: {run.tool_calls}')
            print(f'Error: {run.error}')

asyncio.run(inspect('YOUR_TRACE_ID'))
"
```

### LangSmith

Set `LANGSMITH_TRACING=true` and provide `LANGSMITH_API_KEY` in `.env` to enable LangSmith traces. The app also sets `LANGSMITH_PROJECT` (default: `dental-telegram-mvp`). When disabled the app continues working without LangSmith.

### OpenTelemetry

OpenTelemetry integration is optional. Set `OTEL_ENABLED=true` and `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces to an OTLP collector. When disabled the app continues working without OTel.

## VPS Deployment

### Prerequisites

1. VPS with Docker and Docker Compose installed.
2. Caddy installed on the host (outside Docker).
3. A domain/subdomain pointed to the VPS IP (DNS A record).
4. GitHub Secrets configured: `VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`, `VPS_PROJECT_DIR`, `VPS_PORT` (optional).

### Initial VPS Setup

```sh
# Clone the repository on the VPS
git clone https://github.com/your-org/dental-bot.git /opt/dental-bot
cd /opt/dental-bot

# Create .env from template and fill in secrets
cp .env.example .env
vim .env

# Start the app (first run)
docker compose -f infra/docker-compose.yml up -d --build

# Run DB migrations
docker compose -f infra/docker-compose.yml exec bot alembic upgrade head
```

### Caddy Integration

Copy the Caddy config template and reload:

```sh
cp infra/Caddyfile.example /etc/caddy/sites-enabled/dental-bot.conf
# Edit the domain name:
vim /etc/caddy/sites-enabled/dental-bot.conf
caddy reload
```

The `Caddyfile.example` reverse proxies HTTPS traffic from your domain to `127.0.0.1:8000` inside the container. Docker Compose already maps port 8000 to localhost only.

### Telegram Webhook Registration

After the domain is reachable:

```sh
# Set required env vars
export TELEGRAM_BOT_TOKEN="your-token"
export APP_BASE_URL="https://bot.example.com"
export TELEGRAM_WEBHOOK_SECRET="your-secret"

sh scripts/set_telegram_webhook.sh
```

The app also registers the webhook automatically on startup when `APP_ENV=prod`.

### Automatic Deployment

Push to `main` triggers `.github/workflows/deploy.yml` which:
1. SSH to VPS
2. `git pull --ff-only`
3. Rebuild and restart `bot` container
4. Run DB migrations
5. Show container status and recent logs

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
