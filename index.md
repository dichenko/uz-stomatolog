# Index · Dental Bot (`01_uz-stomatolog`)

Quick-reference for developers working on the Madina VoiceFlow Telegram dental assistant. Language support: RU / UZ / EN.

---

## Directory Map

```
01_uz-stomatolog/
├── apps/bot/                    # Main application (monorepo, single package)
│   ├── app/                     # Source code
│   ├── tests/                   # Pytest test suite (asyncio_mode=auto)
│   ├── pyproject.toml           # Python 3.12, 18+ deps, ruff, pytest
│   ├── Dockerfile               # python:3.12-slim + ffmpeg
│   ├── alembic.ini              # Migrations pointer → app/db/migrations/
│   └── uv.lock                  # UV package lock
├── docs/                        # Agent tool documentation
│   └── tools.md
├── infra/                       # Docker Compose (prod + dev), Caddy config
│   ├── docker-compose.yml       # bot + postgres:16-alpine
│   ├── docker-compose.dev.yml   # pgAdmin on :5050
│   └── Caddyfile.example
├── scripts/                     # deploy.sh, webhook setup, firewall, Yandex test
├── .github/workflows/           # CI (test.yml), CD (deploy.yml → SSH → VPS)
└── *.md                         # Technical specs, integration guides, plan
```

---

## Source Code Layout (`apps/bot/app/`)

| Module | Files | Purpose |
|--------|-------|---------|
| **Root** | `main.py`, `config.py`, `logging.py`, `tracing.py` | FastAPI entrypoint, Pydantic settings (131 lines, all env vars), JSON logger, LangSmith + OpenTelemetry |
| `admin/` | `routes.py` (1013 L), `auth.py`, `settings_repository.py`, `settings_reader.py`, `audit_repository.py`, `history_repository.py` | Web admin panel. Telegram OIDC auth (PKCE), settings CRUD, message history viewer, audit log |
| `agent/` | `agent.py`, `tools.py` (453 L) | LangChain ReAct agent with 8 `@tool` functions (7 patient + 1 sales) |
| `calendar/` | `google_calendar.py` (238 L), `availability.py` (135 L) | Google Calendar API wrapper, slot availability engine, `BusyEvent`, `AvailabilitySlot` |
| `clinic_knowledge/` | `ru.md`, `uz.md`, `en.md` | Static clinic info in 3 languages. Loaded into DB on first start |
| `db/` | `models.py` (386 L), `repositories.py` (594 L), `session.py`, `migrations/` | SQLAlchemy 2.0 models (12 tables), repository classes per table, `async_session_factory` |
| `graph/` | `graph.py` (215 L), `state.py`, `nodes.py` (536 L), `intents.py` (331 L) | LangGraph workflow. 12 intent types, 3-tier classification (keyword → LLM → fallback), safety guard |
| `prompts/` | `system_ru.md`, `system_uz.md`, `system_en.md`, `intent_classifier.md`, `safety_guard.md`, `appointment_summary.md` | LLM prompt templates |
| `services/` | 9 service modules | Business logic: FAQ, booking (616 L), cancellation, rescheduling (443 L), owner sales (1259 L), clinic knowledge, admin notify, LLM context, text LLM (153 L) |
| `speech/` | `base.py`, `factory.py`, `openai_provider.py`, `muxlisa_provider.py`, `yandex_provider.py`, `azure_provider.py`, `mock_provider.py`, `temp_files.py` | STT/TTS abstraction. Protocol-based. Routing: UZ → Muxlisa, RU → Yandex TTS, else → OpenAI |
| `telegram/` | 10 files | Aiogram integration. `webhook.py`, `router.py`, `persistence.py` (middleware), handlers (start/messages/callbacks), `texts.py` (162 L, trilingual), `keyboards.py` |
| `workers/` | `reminder_worker.py` (203 L), `calendar_sync_worker.py` (269 L) | Background asyncio loops. Reminders every 30s, calendar reconciliation every 10 min |

---

## Entry Points

| Entry | Location | What it does |
|-------|----------|-------------|
| `main.py` → `lifespan()` | `apps/bot/app/main.py:35` | FastAPI app lifecycle: loads clinic knowledge, creates Telegram bot, starts background workers |
| `main.py` → `app` | `apps/bot/app/main.py:80` | FastAPI instance, registers `/health`, admin routes, Telegram webhook |
| `run_bot_graph()` | `apps/bot/app/graph/graph.py:17` | Main LangGraph execution. Called by every Telegram handler |
| `create_dispatcher()` | `apps/bot/app/telegram/router.py:10` | Wires aiogram Dispatcher with persistence middleware + routers |
| `setup_telegram()` | `apps/bot/app/telegram/webhook.py` | Initializes aiogram Bot + Dispatcher (polling or webhook mode) |

---

## Data Models (12 tables)

Defined in `apps/bot/app/db/models.py`:

| Model (table) | Key fields | Purpose |
|---------------|-----------|---------|
| `User` | `telegram_user_id` (bigint, unique), `preferred_language` (2-char), `patient_name`, `primary_phone` | Telegram user identity |
| `UserPhone` | FK → `users.id` | Multiple phones per user |
| `Conversation` | FK → `users.id`, `current_flow`, `summary` (JSON draft), `total_messages` | Per-user state machine for multi-step flows |
| `Message` | FK → `users.id`, `trace_id`, `role` (user/assistant), `type` (text/voice), `original_text` | All incoming/outgoing messages |
| `Appointment` | FK → `users.id`, `service_type`, `doctor_type`, `start_at`/`end_at` (UTC), `status`, `google_event_id`, `google_calendar_id`, `trace_id`, `phone` | Booked appointment with calendar sync |
| `AppointmentHistory` | FK → `appointment.id`, `field`, `old_value`, `new_value`, `trace_id` | Audit trail of appointment edits |
| `ReminderJob` | FK → `appointment.id`, `scheduled_at`, `sent_at`, `reminder_type` (24h/2h/now) | Reminder schedule + delivery tracking |
| `Escalation` | FK → `users.id`, `reason`, `resolved_at`, `resolved_by`, `escalation_type` | Admin escalations (emergency, medical, owner leads) |
| `ExecutionRun` | `trace_id`, FK → `users.id`, FK → `conversations.id`, `graph_input`, `graph_output`, `langgraph_metadata`, `node_name` | LangGraph execution tracing |
| `ClinicKnowledge` | `language` (ru/uz/en), `section`, `content` | Localized clinic information |
| `AdminSettings` | `key` (unique), `value` (JSON), `updated_by` | Key-value admin config |
| `AdminAuditLog` | `admin_tg_id`, `action`, `entity_type`, `entity_key`, `old_value`, `new_value` | Admin panel change log |

Repositories are in `apps/bot/app/db/repositories.py` (one class per table, all accept `AsyncSession` in constructor).

---

## Intent Classification Flow

Defined in `apps/bot/app/graph/intents.py`:

| Intent | Trigger | Action |
|--------|---------|--------|
| `owner_sales` | Keywords: "войсфлоу", "демо", "цена", "клиника", "собственник" | `services/owner_sales.py` — staged sales conversation |
| `book_appointment` | Keywords: "запись", "приём", "yozil", "qabul", "appointment" | `services/booking.py` — 2-stage: collect patient → select slot |
| `cancel_appointment` | Keywords: "отмен", "bekor", "cancel" | `services/cancellation.py` — show active → confirm → cancel |
| `reschedule_appointment` | Keywords: "перенес", "boshqa", "reschedule" | `services/rescheduling.py` — select appointment → select new slot |
| `view_appointments` | Keywords: "мои записи", "qabullarim", "my appointments" | Lists active appointments |
| `admin_faq` | Default + keywords: "цена", "услуги", "врачи", "адрес", "график" | `services/faq.py` — keyword extraction → section lookup → LLM answer |
| `medical_question` | Safety filter detects medical advice request | Safety guard blocks, suggests booking consultation |
| `emergency` | Keywords: "боль", "огри", "pain", "кровь", "травм" | Immediate escalation to admin via `services/admin_notify.py` |
| `discount_request` | Keywords: "скидк", "chegirm", "discount" | Refers to standard pricing |
| `non_standard_service` | Keywords: "имплант", "брекет", "implants", "braces" | Routes to FAQ with context |
| `angry_user` | Keywords: "ужас", "плох", "yomon", "terrible" | Escalation with context |
| `unknown` | LLM cannot classify confidently | Fallback FAQ response |

**Classification strategy** (`graph/intents.py` → `classify_intent()`):
1. Deterministic keyword matching (trilingual keyword sets)
2. LLM classification (Claude/OpenAI + structured JSON output), confidence ≥ 0.6
3. Text fallback keyword match

---

## LangGraph Workflow

Defined in `apps/bot/app/graph/graph.py` and `apps/bot/app/graph/nodes.py`:

```
START
  │
  ▼
load_user_context   — repo reads: user profile, conversation summary, active appointments
  │
  ▼
classify_intent     — 3-tier classification (keywords → LLM → fallback)
  │
  ▼
safety_guard        — checks for medical advice / emergency / angry → may block or escalate
  │
  ▼
route_intent        — dispatches to one of:
  │                   admin_faq_node, owner_sales_node, booking_node,
  │                   cancel_node, reschedule_node, view_appointments_node,
  │                   escalation_node, fallback_node
  ▼
END → GraphResult   — final_response_text + should_generate_voice + should_escalate
```

**State** (`graph/state.py` → `BotState` TypedDict): `trace_id`, `telegram_user_id`, `input_text`, `intent`, `safety_status`, `service_type`, `doctor_type`, `proposed_slots`, `selected_slot`, `active_appointments`, `missing_fields`, `final_response_text`, etc.

---

## Speech Processing

**Routing** (`speech/factory.py` → `SpeechProviders`):

| Language | STT | TTS |
|----------|-----|-----|
| Uzbek (`uz`) | Muxlisa | Muxlisa |
| Russian (`ru`) | OpenAI | Yandex SpeechKit |
| English / other | OpenAI | OpenAI |

**Providers** (implement `SpeechToTextProvider` / `TextToSpeechProvider` protocols from `speech/base.py`):

| Provider | File | Key Settings (see `config.py`) |
|----------|------|-------------------------------|
| OpenAI | `speech/openai_provider.py` | `openai_api_key`, `openai_stt_model`, `openai_tts_model`, `openai_tts_voice` |
| Muxlisa (Uzbek) | `speech/muxlisa_provider.py` | `muxlisa_api_key`, `muxlisa_base_url` |
| Yandex SpeechKit | `speech/yandex_provider.py` | `yandex_speechkit_api_key`, `yandex_speechkit_tts_voice` |
| Azure (backup) | `speech/azure_provider.py` | `azure_speech_key`, `azure_speech_region` |
| Mock (testing) | `speech/mock_provider.py` | No external deps |

---

## Telegram Integration

| File | Purpose |
|------|---------|
| `telegram/webhook.py` | FastAPI webhook route + `setup_telegram()` + `shutdown_telegram()` |
| `telegram/router.py` | `create_dispatcher()` — wires PersistenceMiddleware + 3 routers |
| `telegram/persistence.py` | `PersistenceMiddleware` (163 L). Generates `trace_id`, opens DB session, upserts User/Conversation, saves incoming Message |
| `telegram/handlers_start.py` | `/start` command → language picker keyboard |
| `telegram/handlers_messages.py` | Text/voice messages → runs `run_bot_graph()` → sends response |
| `telegram/handlers_callbacks.py` | Inline keyboard callbacks (language select, slot select, confirm/cancel) |
| `telegram/keyboards.py` | Inline keyboard builders for language picker, slot selection, confirmations |
| `telegram/texts.py` | `Language` type (`"ru" | "uz" | "en"`), `TEXTS` dict with all user-facing strings in 3 languages, `text()` and `normalize_language()` helpers |

---

## Business Services

| Service | File | Key Functions |
|---------|------|--------------|
| FAQ | `services/faq.py` | `generate_admin_faq_answer()`, keyword → section extraction, LLM answering |
| Booking | `services/booking.py` (616 L) | `handle_booking_message()`, `is_booking_in_progress()`. 2-stage flow: collect patient info → propose slots → confirm → create appointment + reminders + calendar event |
| Cancellation | `services/cancellation.py` | `handle_cancellation_message()`. Show active appointments → confirm → mark cancelled + cancel calendar + notify admin |
| Rescheduling | `services/rescheduling.py` (443 L) | `handle_reschedule_message()`, `is_rescheduling_in_progress()`. Select appointment → select new slot → update DB + calendar |
| Owner Sales | `services/owner_sales.py` (1259 L) | `handle_owner_sales_message()`, `is_owner_sales_in_progress()`. Multi-stage: warm lead → clinic name → demo → pricing → hot lead → handoff to `@softretail` |
| Admin Notify | `services/admin_notify.py` | `send_admin_notification()` — sends to admin Telegram chat |
| Clinic Knowledge | `services/clinic_knowledge.py` | `load_clinic_knowledge_if_empty()` — loads MD files into DB on startup. `get_clinic_knowledge()` — retrieves by section/language |
| Text LLM | `services/text_llm.py` | `complete_text()` — routes to Claude (Anthropic API) or OpenAI. Claude-first strategy |
| LLM Context | `services/llm_context.py` | `build_llm_context()`, `build_openai_context_messages()` — builds message arrays with clinic info, user profile, history |

---

## Agent Tools (LangChain)

Defined in `apps/bot/app/agent/tools.py` (453 lines). All tools receive `RunnableConfig` for dependency injection.

| # | Tool | Purpose |
|---|------|---------|
| 1 | `get_clinic_information` | Fetch clinic info from knowledge base |
| 2 | `check_slot_availability` | Check if a time slot is available |
| 3 | `find_available_slots` | Search for open slots (service type, date range) |
| 4 | `book_appointment` | Create appointment + calendar event + reminders |
| 5 | `cancel_appointment` | Cancel appointment + notify admin |
| 6 | `reschedule_appointment` | Move appointment to new slot |
| 7 | `view_appointments` | List user's active appointments |
| 8 | `capture_owner_lead` | Record owner/sales lead for human followup |

---

## Background Workers

| Worker | File | Interval | What it does |
|--------|------|----------|-------------|
| Reminder | `workers/reminder_worker.py` | 30s | Sends 24h/2h Telegram reminders to patients. Queries `reminder_jobs` where `scheduled_at <= now AND sent_at IS NULL` |
| Calendar Sync | `workers/calendar_sync_worker.py` | 10 min | Reconciles DB `appointments` with Google Calendar. Deletes stale DB entries (cancelled in Google), updates event details |

Both started in `main.py` → `lifespan()` as `asyncio.create_task()` with a shared `stop_event`.

---

## Admin Web Panel

Routes mounted at `/admin` in `apps/bot/app/admin/routes.py` (1013 lines):

| Route | Purpose |
|-------|---------|
| `GET /admin/` | Dashboard (redirects to login if not authed) |
| `GET /admin/login` | OIDC PKCE flow → Telegram auth |
| `GET /admin/callback` | OAuth2 callback → verify ID token → start session |
| `GET /admin/logout` | Clear session |
| `GET /admin/settings` | View/edit clinic info, welcome messages, system prompts, TTS prompts |
| `POST /admin/settings` | Save settings (audit-logged) |
| `GET /admin/history` | Message history viewer with date/time/user filters |
| `GET /admin/audit` | Admin action audit log |

**Auth**: Telegram OIDC (PKCE). Verified by `admin/auth.py` → `verify_id_token()`. `Starlette SessionMiddleware` stores `tg_id`, `username`, `name`, `picture` in signed cookies.

---

## Configuration (`config.py`)

`Settings` class in `apps/bot/app/config.py` — 131 lines, Pydantic `BaseSettings` from `.env`.

**Key groups**:
- `app_env`, `app_base_url`, `app_timezone`
- `telegram_*` — bot token, webhook secret, admin chat, OIDC
- `postgres_*` — host, port, db, user, password → `database_url` (computed field)
- `openai_*` — API key, text/STT/TTS models, voices, timeouts
- `claude_*` — API key, model, timeout, max tokens
- `muxlisa_*`, `yandex_*`, `azure_*` — speech provider credentials
- `google_*` — calendar credentials file path, calendar ID
- `langsmith_*`, `opentelemetry_*` — observability

---

## Testing (17 files)

Framework: `pytest` + `pytest-asyncio` (`asyncio_mode=auto`). In-memory SQLite via `aiosqlite` (fixture in `conftest.py`).

| Test File | What it Covers |
|-----------|---------------|
| `test_telegram_base.py` | Aiogram dispatcher, persistence middleware |
| `test_graph_flow.py` | Full graph execution for each intent |
| `test_booking_flow.py` | Booking state machine (collect → slots → confirm) |
| `test_cancel_flow.py` | Cancellation flow |
| `test_reschedule_flow.py` | Rescheduling flow |
| `test_restart_flow.py` | Flow restart / reset |
| `test_repositories.py` | All DB repository CRUD operations |
| `test_speech.py` | STT/TTS providers (with mock) |
| `test_text_llm.py` | Text LLM (Claude/OpenAI) |
| `test_clinic_knowledge.py` | Knowledge base loading |
| `test_calendar_integration.py` | Google Calendar API |
| `test_calendar_sync.py` | Calendar sync worker logic |
| `test_llm_context.py` | LLM context building |
| `test_reminders.py` | Reminder scheduling & delivery |
| `test_admin_history.py` | Admin history queries |
| `test_admin_notify.py` | Admin notification delivery |
| `test_tracing.py` | LangSmith + OpenTelemetry config |

Run: `pytest -v` from `apps/bot/`.

---

## Request Lifecycle (End-to-End)

```
Telegram sends update to POST /telegram/webhook
    │
    ▼
FastAPI webhook handler (webhook.py)
    │
    ▼
aiogram Dispatcher → PersistenceMiddleware (persistence.py)
    │  ├─ trace_id = uuid4().hex
    │  ├─ Opens DB session
    │  ├─ Upserts User, Conversation
    │  ├─ Saves incoming Message (role=user)
    │  └─ Adds db_session, db_user, db_conversation, trace_id → handler data
    │
    ▼
Handler (handlers_messages.py or handlers_callbacks.py)
    │  ├─ Speech-to-text via providers.stt_for_language() if voice message
    │  └─ Calls run_bot_graph()
    │
    ▼
run_bot_graph() (graph/graph.py)
    │  ├─ Compiles StateGraph with build_nodes()
    │  ├─ Nodes: load_user_context → classify_intent → safety_guard → [intent node]
    │  ├─ Each node calls business logic services (app/services/)
    │  └─ Returns GraphResult with final_response_text
    │
    ▼
Handler sends response via aiogram (text + optional inline keyboard + optional voice via TTS)
    │
    ▼
PersistenceMiddleware saves outgoing Message (role=assistant)
```

---

## Key Patterns

1. **Repository pattern** — Every table has a dedicated repository class in `db/repositories.py`
2. **Protocol-based abstraction** — Speech providers use Python Protocols (`speech/base.py`)
3. **Factory pattern** — `speech/factory.py` → `SpeechProviders`, `calendar/google_calendar.py` → `create_google_calendar_service()`
4. **Middleware pattern** — `telegram/persistence.py` → `PersistenceMiddleware` transparently injects DB session + trace_id
5. **Draft-based state** — Multi-step flows store intermediate state in `conversations.summary` as serialized JSON
6. **Trace ID propagation** — Every request gets `trace_id` (UUID4 hex), propagated to DB, logs, calendar events
7. **Graceful degradation** — Google Calendar optional, admin bot optional, speech providers individually toggleable
8. **Claude-first** — Default `TEXT_LLM_PROVIDER=claude`, OpenAI fallback
9. **Structured JSON logging** — `logging.py` → `JsonFormatter`, all logs include `trace_id`
10. **Environment-based config** — 94 env vars in `.env.example`, Pydantic `Settings` with computed fields

---

## Quick File Reference

| What | Where |
|------|-------|
| App entrypoint | `apps/bot/app/main.py` |
| All env vars | `apps/bot/app/config.py` |
| All DB models | `apps/bot/app/db/models.py` |
| All DB queries | `apps/bot/app/db/repositories.py` |
| Graph state | `apps/bot/app/graph/state.py` |
| Graph nodes | `apps/bot/app/graph/nodes.py` |
| Intent keywords | `apps/bot/app/graph/intents.py` |
| LangChain tools | `apps/bot/app/agent/tools.py` |
| User-facing texts | `apps/bot/app/telegram/texts.py` |
| Admin panel routes | `apps/bot/app/admin/routes.py` |
| Clinis knowledge | `apps/bot/app/clinic_knowledge/*.md` |
| LLM prompts | `apps/bot/app/prompts/*.md` |
| Booking logic | `apps/bot/app/services/booking.py` |
| Owner sales flow | `apps/bot/app/services/owner_sales.py` |
| Docker Compose | `infra/docker-compose.yml` |
| CI workflow | `.github/workflows/test.yml` |
| CD workflow | `.github/workflows/deploy.yml` |
| Project config | `apps/bot/pyproject.toml` |

---

## Commands

```
# Install deps
cd apps/bot && pip install -e ".[dev]"

# Run locally
cd apps/bot && python -m app.main

# Run tests
cd apps/bot && pytest -v

# Lint
cd apps/bot && ruff check

# Docker (production)
docker compose -f infra/docker-compose.yml up -d --build

# DB migrations
cd apps/bot && alembic upgrade head

# CI/CD
# On push to main: GitHub Actions runs test.yml then deploy.yml (SSH → VPS)
```
