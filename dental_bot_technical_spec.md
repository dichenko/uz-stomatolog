# Technical Specification — Telegram Dental Clinic Assistant MVP

## 1. Project Overview

Build a Telegram bot for a dental clinic in Uzbekistan.

The bot must work in one Telegram bot instance and support three languages:

- Russian;
- Uzbek;
- English.

The bot is an administrative assistant, not a medical consultant.

It helps users:

- ask administrative questions;
- learn about clinic services, prices, schedule, doctors, address and contacts;
- book an appointment;
- cancel an appointment;
- reschedule an appointment;
- leave a phone number for admin callback in emergency, unclear or fallback cases.

The project must be written in Python using LangGraph.

The project must be developed locally, committed to GitHub, and automatically deployed to VPS through GitHub Actions.

Everything must run in Docker.

The VPS already has Caddy installed. The domain will be pointed to the VPS by the owner.

---

## 2. Main MVP Goal

The goal of the MVP is to prove that the full core flow works:

1. User opens Telegram bot.
2. User chooses language.
3. User sends text or voice.
4. Bot understands the request.
5. Bot replies in the selected language.
6. If user sent voice, bot replies with both text and voice.
7. Bot answers administrative questions from clinic knowledge base.
8. Bot safely refuses medical advice and offers appointment.
9. Bot proposes free slots.
10. Bot creates appointment in Google Calendar.
11. Bot saves all data to PostgreSQL.
12. Bot can cancel and reschedule appointments.
13. Bot sends reminders 24 hours and 2 hours before appointment.
14. Bot sends admin notifications to a Telegram admin group.
15. Bot syncs DB with Google Calendar every 10 minutes.
16. Calendar is treated as the source of truth.

---

## 3. Out of Scope for MVP

Do not implement in the first MVP:

- web admin panel;
- CRM integration;
- payment integration;
- Excel export;
- multi-clinic logic;
- multiple Google Calendars;
- medical diagnosis;
- medication recommendations;
- complex doctor schedule management UI;
- real patient production launch without manual testing.

---

## 4. Technology Stack

Use the following stack unless there is a strong reason to change it:

- Python 3.12
- LangGraph
- aiogram 3
- PostgreSQL 16
- SQLAlchemy 2 async
- asyncpg
- Alembic
- Pydantic Settings
- httpx
- APScheduler or simple asyncio scheduler for workers
- Docker Compose
- GitHub Actions
- Google Calendar API
- OpenAI API
- Muxlisa API
- LangSmith for traces
- Optional OpenTelemetry

---

## 5. Deployment Model

### 5.1 Local Development

The owner will work locally.

Local workflow:

1. Clone repository.
2. Create `.env`.
3. Run project with Docker Compose.
4. Test Telegram webhook using a public HTTPS tunnel or temporary dev domain.
5. Commit and push changes to GitHub.

### 5.2 GitHub

The repository must be a monorepo.

All code, Dockerfiles, migrations, README and deployment scripts must live in one repository.

### 5.3 GitHub Actions

On push to `main`, GitHub Actions must connect to the VPS through SSH and deploy the project.

Deployment flow:

1. SSH to VPS.
2. Go to project directory.
3. Run `git pull`.
4. Rebuild only changed services where possible.
5. Run DB migrations.
6. Restart bot service.
7. Show service status and recent logs.

Secrets must be stored in GitHub Actions Secrets.

Do not commit secrets into the repository.

### 5.4 VPS

The VPS already has Caddy installed.

The domain will be pointed to the VPS by DNS.

The app itself must run in Docker Compose.

Caddy on VPS must reverse proxy the Telegram webhook domain to the bot container or to a local exposed bot port.

Recommended deployment pattern:

- Bot container exposes internal HTTP app port, for example `8000`.
- Docker Compose maps it to localhost only, for example `127.0.0.1:8000:8000`.
- Caddy proxies public HTTPS traffic to `127.0.0.1:8000`.

Example Caddy block:

```caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

The actual domain will be provided later.

---

## 6. Required Repository Structure

Create the project as a monorepo:

```text
dental-bot/
  apps/
    bot/
      app/
        main.py
        config.py
        logging.py

        telegram/
          router.py
          handlers_start.py
          handlers_messages.py
          handlers_callbacks.py
          webhook.py
          keyboards.py

        graph/
          state.py
          graph.py
          nodes.py
          intents.py
          tools.py
          safety.py

        services/
          admin_notify.py
          clinic_knowledge.py
          reminders.py
          summaries.py

        speech/
          base.py
          openai_provider.py
          muxlisa_provider.py
          temp_files.py

        calendar/
          google_calendar.py
          availability.py
          sync_worker.py

        db/
          session.py
          models.py
          repositories.py
          migrations/
            env.py
            versions/

        workers/
          reminder_worker.py
          calendar_sync_worker.py

        prompts/
          system_ru.md
          system_uz.md
          system_en.md
          intent_classifier.md
          safety_guard.md
          appointment_summary.md

        clinic_knowledge/
          ru.md
          uz.md
          en.md

      tests/
        test_language_selection.py
        test_booking_flow.py
        test_cancel_flow.py
        test_reschedule_flow.py
        test_safety.py
        test_voice_pipeline.py
        test_calendar_sync.py

      Dockerfile
      pyproject.toml

  infra/
    docker-compose.yml
    docker-compose.dev.yml
    Caddyfile.example

  scripts/
    deploy.sh
    set_telegram_webhook.sh

  .github/
    workflows/
      deploy.yml

  .env.example
  README.md
```

---

## 7. Environment Variables

Create `.env.example`.

```env
APP_ENV=dev
APP_BASE_URL=https://bot.example.com
APP_TIMEZONE=Asia/Tashkent

TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_WEBHOOK_PATH=/telegram/webhook
ADMIN_TELEGRAM_CHAT_ID=

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=dental_bot
POSTGRES_USER=dental_bot
POSTGRES_PASSWORD=
DATABASE_URL=postgresql+asyncpg://dental_bot:password@postgres:5432/dental_bot

OPENAI_API_KEY=
OPENAI_TEXT_MODEL=
OPENAI_STT_MODEL=
OPENAI_TTS_MODEL=

MUXLISA_API_KEY=
MUXLISA_BASE_URL=

GOOGLE_CALENDAR_ID=
GOOGLE_SERVICE_ACCOUNT_JSON_PATH=/run/secrets/google_service_account.json

LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=dental-telegram-mvp

OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_SERVICE_NAME=dental-telegram-bot
```

---

## 8. Telegram Bot Requirements

Use aiogram 3.

Production must use webhook mode.

Long polling is allowed only for local debugging if explicitly useful, but production must be webhook-based.

### Required commands

Implement:

- `/start`
- `/language`
- `/help`
- `/my_appointments`

Optional but recommended:

- `/cancel`
- `/reschedule`

### First start

If user has no selected language:

Show inline keyboard:

- Русский
- O‘zbekcha
- English

Save selected language to DB.

### Language switching

User can switch language with `/language`.

The selected language controls the bot's output language.

If user selected Russian but asks in Uzbek, the bot should understand if possible but answer in Russian.

Same rule for all languages.

---

## 9. Language Rules

Use enum:

```python
Language = Literal["ru", "uz", "en"]
```

### Uzbek

For MVP:

- Uzbek output should use Latin script.
- Input may be Latin or Cyrillic.
- Do not build a separate transliteration module unless absolutely necessary.
- Store original user text exactly as received or transcribed.

---

## 10. Voice Processing

Voice processing must be implemented as separate modules outside the main LangGraph agent.

The main graph receives text only.

### 10.1 Input voice pipeline

1. Receive Telegram voice message.
2. Download voice file from Telegram.
3. Save temporarily on VPS.
4. Transcribe:
   - `uz` → Muxlisa STT;
   - `ru` / `en` → OpenAI STT.
5. Delete temporary audio file.
6. Send transcribed text to main LangGraph flow.
7. Save transcription in DB as incoming message text.

### 10.2 Output voice pipeline

Generate voice only when the original user message was voice.

1. Main graph returns final text response.
2. Generate TTS:
   - `uz` → Muxlisa TTS;
   - `ru` / `en` → OpenAI TTS.
3. Send text response to user.
4. Send generated voice/audio response to user.
5. Delete generated temporary audio file.

### 10.3 Audio storage

Do not store audio long term.

Allowed:

- temporary local files only during STT/TTS/send.

Required:

- delete temporary files after use;
- log cleanup success or failure.

---

## 11. AI / LangGraph Architecture

The assistant must not be an uncontrolled free agent.

Use a controlled LangGraph workflow with explicit states and typed tools.

### 11.1 Normalized graph input

```python
{
    "telegram_user_id": 123,
    "telegram_chat_id": 123,
    "input_text": "...",
    "input_type": "text" | "voice",
    "preferred_language": "ru" | "uz" | "en",
    "telegram_profile": {...}
}
```

### 11.2 Main graph responsibilities

The graph must:

1. Load user profile and conversation context.
2. Classify intent.
3. Apply medical safety rules.
4. Retrieve clinic knowledge.
5. Route to proper flow:
   - administrative FAQ;
   - booking;
   - cancellation;
   - rescheduling;
   - emergency/escalation;
   - fallback.
6. Call DB and Calendar tools.
7. Return final response text.
8. Return metadata for Telegram layer:
   - whether voice response is needed;
   - whether admin notification was sent;
   - proposed slot buttons if needed.

---

## 12. Intent Classes

Use enum:

```python
Intent = Literal[
    "admin_faq",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "medical_question",
    "emergency",
    "discount_request",
    "non_standard_service",
    "angry_user",
    "unknown"
]
```

Fallback cases:

- bot does not understand user;
- user is angry;
- user asks medical question;
- user asks for discount;
- user asks for non-standard service;
- calendar conflict;
- emergency situation.

---

## 13. Suggested LangGraph Flow

```text
START
  -> load_user_context
  -> classify_intent
  -> safety_guard
  -> route_intent

route_intent:
  - admin_faq
  - start_booking
  - continue_booking
  - cancel_appointment
  - reschedule_appointment
  - emergency_or_escalation
  - fallback

admin_faq
  -> generate_admin_answer
  -> END

start_booking / continue_booking
  -> collect_missing_booking_fields
  -> find_available_slots
  -> propose_slots
  -> confirm_slot
  -> create_calendar_event
  -> create_db_appointment
  -> schedule_reminders
  -> notify_admins
  -> generate_booking_confirmation
  -> END

cancel_appointment
  -> find_user_appointments
  -> confirm_cancellation
  -> cancel_calendar_event
  -> update_db_appointment
  -> cancel_reminders
  -> notify_admins
  -> END

reschedule_appointment
  -> find_user_appointments
  -> collect_new_time_preference
  -> find_available_slots
  -> confirm_new_slot
  -> update_calendar_event
  -> update_db_appointment
  -> reschedule_reminders
  -> notify_admins
  -> END

emergency_or_escalation
  -> collect_phone_if_missing
  -> create_escalation
  -> notify_admins
  -> END

fallback
  -> generate_safe_fallback_response
  -> optionally_escalate
  -> END
```

---

## 14. Graph State

Create typed state model.

```python
class BotState(TypedDict):
    trace_id: str
    telegram_user_id: int
    telegram_chat_id: int
    input_text: str
    input_type: Literal["text", "voice"]
    preferred_language: Literal["ru", "uz", "en"]

    user_profile: dict | None
    conversation_summary: str | None

    intent: str | None
    safety_status: str | None

    service_type: str | None
    doctor_type: str | None
    requested_date: str | None
    requested_time_of_day: str | None

    proposed_slots: list[dict]
    selected_slot: dict | None

    missing_fields: list[str]

    final_response_text: str | None
    should_generate_voice: bool

    should_escalate: bool
    escalation_reason: str | None
```

---

## 15. Tools

Implement tools with typed input/output.

Required tools:

- `get_user_profile`
- `upsert_user_profile`
- `save_message`
- `get_user_active_appointments`
- `get_clinic_knowledge`
- `find_available_slots`
- `create_calendar_event`
- `update_calendar_event`
- `cancel_calendar_event`
- `create_db_appointment`
- `update_db_appointment`
- `cancel_reminders`
- `schedule_reminders`
- `create_escalation`
- `send_admin_notification`

---

## 16. Clinic Rules

### 16.1 Clinic

- One clinic.
- Two cabinets.
- Two doctors:
  - therapist;
  - surgeon.
- Working days:
  - Monday to Saturday.
- Working hours:
  - 09:00–21:00.
- Timezone:
  - `Asia/Tashkent`.

### 16.2 Services

Initial services and durations:

- consultation: 30 minutes;
- cleaning: 60 minutes;
- treatment: 90 minutes.

### 16.3 Service mapping

Default behavior:

- Pain, problem, uncertainty, question like “what should I do?” → consultation with therapist.
- Cleaning request → cleaning.
- Surgery/extraction/surgeon request → surgeon.
- Unclear request → consultation.

### 16.4 Medical safety

The bot must never:

- diagnose;
- prescribe medicine;
- suggest painkillers;
- suggest antibiotics;
- suggest treatment plan;
- interpret symptoms medically.

Allowed behavior:

- say that the bot cannot provide medical advice;
- offer to book consultation;
- suggest nearest available appointment slot;
- escalate urgent/unclear situations to admin group.

---

## 17. Booking Flow

### 17.1 Required user data

For booking, collect and store:

- Telegram user id;
- Telegram username;
- Telegram first name;
- Telegram last name;
- selected language;
- patient name;
- one or more phone numbers.

Use Telegram contact request button if possible.

If user refuses contact sharing, allow manual phone input.

### 17.2 Slot proposal

When user wants to book:

1. Determine service and duration.
2. Ask missing name/phone if needed.
3. Find nearest available slots.
4. Offer 3 nearest slots as inline buttons.
5. If user rejects them, ask preferred date and time of day.
6. Re-check availability before final booking.
7. Create Google Calendar event.
8. Save DB appointment.
9. Schedule reminders.
10. Send confirmation to user.
11. Notify admin group.

### 17.3 Cancellation

User can cancel any time.

Flow:

1. Find active future appointments by Telegram ID.
2. If one appointment, ask confirmation.
3. If several, show list.
4. On confirmation:
   - cancel/delete Google Calendar event;
   - update DB;
   - cancel reminders;
   - notify admin group;
   - confirm to user.

### 17.4 Rescheduling

User can reschedule any time.

Flow:

1. Find active future appointments.
2. Ask user to select appointment if several.
3. Ask preferred new time or propose nearest slots.
4. User selects new slot.
5. Re-check availability.
6. Update Google Calendar event.
7. Update DB.
8. Reschedule reminders.
9. Notify admin group.
10. Confirm to user.

---

## 18. Google Calendar Integration

Use one shared Google Calendar.

Calendar is the source of truth.

### 18.1 Event title format

```text
[Bot] {service_type} — {patient_name} — {phone}
```

Example:

```text
[Bot] Consultation — Ali Karimov — +998901234567
```

### 18.2 Event description

Event description must contain:

```text
Created by Telegram bot

Patient:
- Name:
- Phone:
- Telegram ID:
- Telegram username:
- Language:

Appointment:
- Service:
- Doctor:
- Duration:
- Conversation summary:

Internal:
- DB appointment ID:
- Trace ID:
```

### 18.3 Extended properties

Use Google Calendar `extendedProperties.private`:

- `appointment_id`
- `telegram_user_id`
- `service_type`
- `doctor_type`
- `created_by=telegram_bot`

### 18.4 Availability logic

There are two cabinets and two doctors.

MVP availability rules:

- therapist can have only one appointment at the same time;
- surgeon can have only one appointment at the same time;
- total simultaneous appointments must not exceed 2;
- events without metadata should be treated conservatively as blocking one generic cabinet.

Before creating or updating an appointment:

- re-check the selected slot;
- if conflict appeared, tell user the slot is no longer available and propose new slots.

---

## 19. Calendar Sync Worker

Run every 10 minutes.

The worker must:

1. Read Google Calendar events:
   - from now minus 7 days;
   - to now plus 60 days.
2. Compare calendar events with DB appointments.
3. Treat calendar as source of truth.
4. If event time changed in Calendar, update DB.
5. If event deleted/cancelled in Calendar, mark DB appointment as cancelled.
6. If event exists in Calendar but not DB and has bot metadata, restore/create DB record where possible.
7. Write sync logs.

For MVP, simple window polling is acceptable.

Incremental sync token can be added later.

---

## 20. PostgreSQL Data Model

Use SQLAlchemy 2 async and Alembic migrations.

### 20.1 users

Fields:

- `id`
- `telegram_user_id` unique not null
- `telegram_username`
- `telegram_first_name`
- `telegram_last_name`
- `preferred_language`
- `created_at`
- `updated_at`

### 20.2 user_phones

Fields:

- `id`
- `user_id`
- `phone`
- `is_primary`
- `source`
- `created_at`

### 20.3 conversations

Fields:

- `id`
- `user_id`
- `telegram_chat_id`
- `current_flow`
- `current_state`
- `summary`
- `created_at`
- `updated_at`
- `last_message_at`

### 20.4 messages

Store full message history.

Fields:

- `id`
- `user_id`
- `conversation_id`
- `telegram_message_id`
- `direction`: `in` / `out`
- `message_type`: `text` / `voice` / `callback` / `system`
- `language`
- `text`
- `raw_payload` JSONB
- `trace_id`
- `created_at`

Do not store audio files long term.

### 20.5 appointments

Fields:

- `id`
- `user_id`
- `calendar_event_id` unique
- `calendar_etag`
- `status`: `scheduled` / `cancelled` / `rescheduled` / `completed`
- `service_type`
- `doctor_type`
- `start_at`
- `end_at`
- `timezone`
- `patient_name`
- `primary_phone`
- `conversation_summary`
- `created_trace_id`
- `created_at`
- `updated_at`
- `cancelled_at`

### 20.6 appointment_history

Fields:

- `id`
- `appointment_id`
- `action`
- `actor`: `user` / `bot` / `calendar_sync` / `admin_manual_calendar`
- `old_data` JSONB
- `new_data` JSONB
- `created_at`

### 20.7 clinic_knowledge

Fields:

- `id`
- `language`
- `content`
- `version`
- `is_active`
- `created_at`
- `updated_at`

For MVP, initial data can be loaded from Markdown files:

```text
apps/bot/app/clinic_knowledge/ru.md
apps/bot/app/clinic_knowledge/uz.md
apps/bot/app/clinic_knowledge/en.md
```

### 20.8 escalations

Fields:

- `id`
- `user_id`
- `reason`
- `status`: `new` / `in_progress` / `resolved`
- `summary`
- `phone`
- `admin_chat_id`
- `admin_message_id`
- `created_at`
- `updated_at`

### 20.9 reminder_jobs

Fields:

- `id`
- `appointment_id`
- `reminder_type`: `day_before` / `two_hours_before`
- `send_at`
- `status`: `pending` / `sent` / `cancelled` / `failed`
- `sent_at`
- `error`
- `created_at`
- `updated_at`

### 20.10 execution_runs

Fields:

- `id`
- `trace_id`
- `user_id`
- `conversation_id`
- `input_message_id`
- `intent`
- `status`
- `started_at`
- `finished_at`
- `duration_ms`
- `graph_input` JSONB
- `graph_output` JSONB
- `tool_calls` JSONB
- `error`

---

## 21. Admin Notifications

Use Telegram admin group.

Env:

```env
ADMIN_TELEGRAM_CHAT_ID=
```

Send admin notification when:

- new appointment is created;
- appointment is cancelled;
- appointment is rescheduled;
- emergency/escalation is created;
- calendar conflict occurs;
- bot repeatedly fails for the same user.

### 21.1 New appointment admin message

```text
New appointment created

Patient:
Name: ...
Phone: ...
Telegram: @... / id ...

Appointment:
Service: ...
Doctor: ...
Time: ...
Duration: ...

Conversation summary:
...

Calendar event:
...
```

### 21.2 Escalation admin message

```text
Escalation required

Reason: emergency / medical question / bot did not understand / angry user / discount / non-standard service / conflict

Patient:
Name:
Phone:
Telegram:

User message:
...

Conversation summary:
...
```

---

## 22. Reminders

Send reminders:

- 24 hours before appointment;
- 2 hours before appointment.

Before sending reminder:

1. Check DB appointment status.
2. Check Google Calendar event still exists.
3. Check event time is unchanged.
4. If changed, reschedule reminders.
5. If cancelled, cancel reminders.

Reminder messages are text-only in MVP.

---

## 23. Tracing and Observability

### 23.1 Required

Implement:

- structured JSON logs;
- `trace_id` for every Telegram update;
- `execution_runs` DB table;
- logging for every graph node;
- logging for every external API call;
- error logs with stack traces;
- no secrets in logs.

Store `trace_id` in:

- messages;
- appointments;
- execution_runs;
- calendar event description.

### 23.2 LangSmith

Integrate LangSmith tracing.

If no API key is provided, the app must still work with tracing disabled.

Env:

```env
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=dental-telegram-mvp
```

### 23.3 OpenTelemetry

OpenTelemetry is optional.

Add env flags:

```env
OTEL_ENABLED=false
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_SERVICE_NAME=dental-telegram-bot
```

Do not overbuild dashboards in MVP.

---

## 24. Prompt Files

Create prompt files:

```text
apps/bot/app/prompts/system_ru.md
apps/bot/app/prompts/system_uz.md
apps/bot/app/prompts/system_en.md
apps/bot/app/prompts/intent_classifier.md
apps/bot/app/prompts/safety_guard.md
apps/bot/app/prompts/appointment_summary.md
```

System behavior:

```text
You are a dental clinic administrative assistant.
You help users book, cancel, and reschedule appointments.
You answer only administrative questions using the clinic knowledge base.
You never diagnose, prescribe, or recommend medical treatment.
If the user asks for medical advice, offer to book a consultation.
If the situation seems urgent, ask for phone number and escalate to admins.
Always answer in the user's selected language.
```

---

## 25. Docker Requirements

Create Docker setup for:

- bot app;
- PostgreSQL;
- optional pgAdmin for local development only.

Production should not require pgAdmin.

Use separate dev and production compose files if needed:

```text
infra/docker-compose.yml
infra/docker-compose.dev.yml
```

Bot must expose internal HTTP server for Telegram webhook.

Recommended internal app port:

```text
8000
```

Recommended VPS port mapping:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

Caddy proxies HTTPS domain to this local port.

---

## 26. GitHub Actions Requirements

Create:

```text
.github/workflows/deploy.yml
```

The workflow must:

1. Run on push to `main`.
2. Connect to VPS via SSH.
3. Go to project directory.
4. Pull latest code.
5. Build/restart Docker services.
6. Run Alembic migrations.
7. Show container status.
8. Show recent bot logs.

Required GitHub Secrets:

```text
VPS_HOST
VPS_USER
VPS_SSH_KEY
VPS_PROJECT_DIR
```

Optional:

```text
VPS_PORT
```

Secrets for app runtime should live in `.env` on the VPS, not in the repository.

---

## 27. Tests

Add automated tests with mocks.

Required tests:

1. Language selection.
2. Text FAQ answer.
3. Voice input with mocked STT.
4. Voice output with mocked TTS.
5. Booking flow.
6. Slot conflict handling.
7. Google Calendar event creation with mocked API.
8. Cancellation flow.
9. Rescheduling flow.
10. Reminder scheduling.
11. Calendar sync updates DB from calendar.
12. Medical question refusal.
13. Emergency escalation.
14. Unknown fallback.
15. Admin notification sending.

---

## 28. Acceptance Criteria

The MVP is accepted when all scenarios below work in dev environment.

### Scenario 1: First start

- User opens bot.
- Bot asks for language.
- User chooses Russian / Uzbek / English.
- Bot saves preferred language.

### Scenario 2: Text FAQ

- User asks about clinic schedule or prices.
- Bot answers in selected language.
- Message is saved to DB.

### Scenario 3: Voice message

- User sends voice.
- Bot downloads audio temporarily.
- Bot transcribes it.
- Bot processes request.
- Bot replies with text and voice.
- Temporary audio files are deleted.

### Scenario 4: Booking

- User asks to book appointment.
- Bot collects name and phone.
- Bot proposes nearest slots.
- User selects slot.
- Bot creates Google Calendar event.
- Bot saves appointment to DB.
- Bot sends user confirmation.
- Bot sends admin group notification.

### Scenario 5: Rescheduling

- User asks to reschedule.
- Bot finds active appointment.
- Bot proposes new slots.
- User selects new slot.
- Bot updates Google Calendar.
- Bot updates DB.
- Bot sends confirmation and admin notification.

### Scenario 6: Cancellation

- User asks to cancel.
- Bot finds active appointment.
- User confirms.
- Bot cancels Google Calendar event.
- Bot updates DB.
- Bot cancels reminders.
- Bot sends confirmation and admin notification.

### Scenario 7: Reminders

- Bot sends reminder 24 hours before appointment.
- Bot sends reminder 2 hours before appointment.

### Scenario 8: Calendar sync

- Admin manually changes event time in Google Calendar.
- Within 10 minutes, bot updates DB to match calendar.

### Scenario 9: Medical safety

User asks:

```text
У меня болит зуб, что выпить?
```

Bot must not recommend medicine.

Bot must offer consultation and nearest slot.

### Scenario 10: Escalation

- User describes urgent or unclear situation.
- Bot asks for phone if missing.
- Bot creates escalation.
- Bot sends admin group summary.

---

## 29. Human Owner TODO

Implementation should not be blocked by these, but document them clearly in README:

1. Final Muxlisa API documentation and credentials.
2. Exact OpenAI models for text/STT/TTS.
3. Google Calendar service account setup.
4. Real admin Telegram group ID.
5. Final clinic knowledge base text in RU/UZ/EN.
6. Final domain for Telegram webhook.
7. Whether Uzbek Cyrillic should become a separate user interface option later.
8. Whether reminders should support voice later.
9. Whether production launch requires approved legal/medical disclaimer text.
