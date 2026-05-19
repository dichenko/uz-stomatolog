# Implementation Plan — Telegram Dental Clinic Assistant MVP

## 1. Development and Deployment Workflow

The owner will work as follows:

```text
Local development
  -> git commit
  -> git push to GitHub
  -> GitHub Actions
  -> SSH to VPS
  -> git pull on VPS
  -> Docker Compose build/restart
  -> Alembic migrations
  -> app runs behind existing Caddy
```

The VPS already has Caddy installed.

The owner will point a domain/subdomain to the VPS.

The app must run entirely in Docker.

Caddy on the VPS will terminate HTTPS and reverse proxy requests to the bot container.

---

## 2. Recommended Milestones

The project should be implemented in controlled stages.

Each stage must leave the project in a runnable state.

Do not try to implement the entire project in one large step.

### Current implementation status

- [x] Milestone 0 - Repository and Infrastructure Skeleton.
  Completed in commit `1e3b7db` and pushed to `origin/main`.
- [x] Milestone 1 - Database Foundation.
  Completed in commit `1e3b7db` and pushed to `origin/main`.
- [x] Milestone 2 - Telegram Bot Base.
  Completed in commit `0cc076a` and pushed to `origin/main`.
- [x] Milestone 3 - Clinic Knowledge Base and Text FAQ.
  Completed in commit `787027a` and pushed to `origin/main`.
- [x] Milestone 4 - Speech Modules.
  Completed in commit `a77fad8` and pushed to `origin/main`.
- [x] Milestone 5 - LangGraph Controlled Flow.
  Completed in commit `a77fad8` and pushed to `origin/main`.
- [x] Milestone 6 - Medical Safety and Escalation.
  Completed in commit `7f6ebdd` and pushed to `origin/main`.
- [x] Milestone 7 - Google Calendar Integration.
  Completed in commit `7f6ebdd` and pushed to `origin/main`.
- [x] Milestone 8 - Booking Flow.
  Completed in commit `0bf7489` and pushed to `origin/main`.
- [x] Milestone 9 - Cancellation Flow.
  Completed in commit `2d473a1`.
- [x] Milestone 10 - Rescheduling Flow.
  Completed in commit `ac9f270`.
- [x] Milestone 11 - Reminder Worker.
  Completed in commit `ac9f270`.
- [x] Milestone 12 - Calendar Sync Worker.
  Completed in commit `dabd504`.
- [x] Milestone 13 - Tracing and Observability.
  Completed in commit `dabd504`.
- [ ] Milestone 14 - GitHub Actions Deployment.
- [ ] Milestone 15 - VPS and Caddy Integration.
- [ ] Milestone 16 - Automated Tests.
- [ ] Milestone 17 - Final MVP QA.

---

## Milestone 0 — Repository and Infrastructure Skeleton

**Status:** Done.

Implemented:

- Monorepo structure with `apps/bot`, `infra`, `scripts`, `.github/workflows`.
- Python 3.12 bot project with FastAPI health endpoint.
- Dockerfile and Docker Compose for bot and PostgreSQL.
- `.env.example`, README, Caddy example, deployment and webhook scripts.
- Structured JSON logging foundation.

Verified:

- `docker compose -f infra/docker-compose.yml up -d --build`.
- `GET /health` returns `{"status":"OK"}`.
- Bot and PostgreSQL containers start successfully.

### Goal

Create a clean monorepo foundation that can run locally and later deploy to VPS.

### Tasks

1. Create monorepo structure:

```text
dental-bot/
  apps/bot/
  infra/
  scripts/
  .github/workflows/
  .env.example
  README.md
```

2. Create Python project in `apps/bot`.

3. Add dependencies:
   - aiogram 3
   - LangGraph
   - LangChain core packages if needed
   - SQLAlchemy 2 async
   - asyncpg
   - Alembic
   - Pydantic Settings
   - httpx
   - pytest
   - APScheduler or equivalent scheduler
   - Google Calendar client libraries
   - OpenAI SDK
   - LangSmith

4. Create Dockerfile for bot.

5. Create Docker Compose:
   - bot;
   - postgres;
   - optional pgAdmin for dev.

6. Create `.env.example`.

7. Create base `README.md`.

8. Add JSON logging foundation.

### Acceptance Criteria

- `docker compose up -d --build` starts PostgreSQL and bot container.
- Bot container starts without Telegram logic yet.
- App reads env vars.
- App writes structured JSON logs.
- README explains how to run locally.

---

## Milestone 1 — Database Foundation

**Status:** Done.

Implemented:

- Async SQLAlchemy session setup.
- Alembic configuration and initial migration.
- Tables: `users`, `user_phones`, `conversations`, `messages`, `appointments`, `appointment_history`, `clinic_knowledge`, `escalations`, `reminder_jobs`, `execution_runs`.
- Repository layer for users, conversations, messages, appointments, clinic knowledge, escalations, reminders, and execution runs.
- Basic repository CRUD tests.

Verified:

- `alembic upgrade head` creates all required tables in PostgreSQL.
- Repository test suite passed.

### Goal

Create DB connection, models and migrations.

### Tasks

1. Configure async SQLAlchemy.
2. Configure Alembic.
3. Create initial migrations.
4. Implement tables:
   - users;
   - user_phones;
   - conversations;
   - messages;
   - appointments;
   - appointment_history;
   - clinic_knowledge;
   - escalations;
   - reminder_jobs;
   - execution_runs.
5. Add repository layer:
   - UserRepository;
   - MessageRepository;
   - AppointmentRepository;
   - ClinicKnowledgeRepository;
   - EscalationRepository;
   - ReminderRepository;
   - ExecutionRunRepository.

### Acceptance Criteria

- `alembic upgrade head` creates all tables.
- Basic repository tests pass.
- User can be created/updated by Telegram ID.
- Messages can be saved.
- Appointments can be created/updated/cancelled.

---

## Milestone 2 — Telegram Bot Base

**Status:** Done.

Implemented:

- aiogram dispatcher and Telegram webhook route at `/telegram/webhook`.
- Telegram webhook secret validation.
- `/start`, `/language`, `/help`, `/my_appointments`.
- Inline language keyboard for Russian, Uzbek, and English.
- Language persistence in DB.
- Incoming and outgoing message persistence in DB.
- `trace_id` per Telegram update.

Verified:

- Docker rebuild and app startup.
- `/health` works after Telegram integration.
- Telegram base tests passed.

### Goal

Implement Telegram webhook bot with language selection.

### Tasks

1. Create aiogram app.
2. Add webhook endpoint.
3. Add startup hook to register webhook.
4. Add `/start`.
5. Add `/language`.
6. Add `/help`.
7. Add `/my_appointments`.
8. Implement language selection inline keyboard:
   - Русский;
   - O‘zbekcha;
   - English.
9. Save selected language to DB.
10. Save incoming/outgoing messages to DB.
11. Add trace_id per update.

### Acceptance Criteria

- User can open Telegram bot.
- Bot asks for language on first start.
- Bot saves language to DB.
- User can change language with `/language`.
- Every incoming message is saved.
- Every outgoing message is saved.
- Webhook works locally through tunnel or dev domain.

---

## Milestone 3 — Clinic Knowledge Base and Text FAQ

**Status:** Done.

Implemented:

- Clinic knowledge Markdown files: `ru.md`, `uz.md`, `en.md`.
- Prompt files: `system_ru.md`, `system_uz.md`, `system_en.md`, `intent_classifier.md`, `safety_guard.md`, `appointment_summary.md`.
- Startup loader that imports Markdown knowledge into `clinic_knowledge` when the table is empty.
- `get_clinic_knowledge(language)`.
- Constrained FAQ service that answers from knowledge base and refuses medical advice.
- Unknown-question fallback that avoids invented details.

Verified:

- Startup loaded 3 `clinic_knowledge` records.
- Full test suite passed: `13 passed`.

### Goal

Bot can answer administrative questions from clinic knowledge base.

### Tasks

1. Add clinic knowledge files:
   - `ru.md`
   - `uz.md`
   - `en.md`
2. Add DB table loading from files on startup if DB is empty.
3. Implement `get_clinic_knowledge(language)`.
4. Add prompt files:
   - `system_ru.md`
   - `system_uz.md`
   - `system_en.md`
   - `intent_classifier.md`
   - `safety_guard.md`
   - `appointment_summary.md`
5. Implement basic LLM response generation for administrative FAQ.
6. Ensure bot does not invent prices/services outside knowledge base.
7. If answer is unknown, fallback to admin escalation or callback offer.

### Acceptance Criteria

- User asks schedule/price/contact question.
- Bot answers in selected language.
- Bot uses clinic knowledge text.
- Bot does not hallucinate unknown information.
- All messages are saved.

---

## Milestone 4 — Speech Modules

**Status:** Done.

Implemented:

- Speech provider interfaces and typed STT/TTS result models.
- OpenAI STT/TTS provider for Russian and English.
- Muxlisa STT/TTS provider for Uzbek.
- Mock speech provider for tests.
- Telegram voice handler that downloads voice files, transcribes them, routes text through the existing FAQ flow, replies with text plus generated audio, and deletes temporary files.
- Temporary audio helpers, file size validation, Muxlisa WAV conversion helper, retry handling for retryable provider failures, and localized voice error messages.
- `.env.example` entries for OpenAI and Muxlisa speech settings.

Verified:

- Targeted lint for changed speech/Telegram files passed.
- Test suite passed with mocked providers: `17 passed`.

### Goal

Support voice input and voice output.

### Tasks

1. Create speech provider interface:

```python
class SpeechToTextProvider:
    async def transcribe(self, file_path: str, language: str) -> str:
        ...

class TextToSpeechProvider:
    async def synthesize(self, text: str, language: str) -> str:
        ...
```

2. Implement OpenAI STT for `ru` and `en`.
3. Implement OpenAI TTS for `ru` and `en`.
4. Implement Muxlisa STT for `uz`.
5. Implement Muxlisa TTS for `uz`.
6. Implement mock providers for tests.
7. Implement Telegram voice download.
8. Save temporary input audio.
9. Delete temporary input audio after transcription.
10. Generate temporary output audio only for voice-originated messages.
11. Delete generated output audio after sending.
12. Log all temp file cleanup.

### Acceptance Criteria

- User sends voice.
- Bot transcribes it.
- Bot processes text.
- Bot replies with text and voice.
- Temporary files are deleted.
- Voice path works with mocked providers even without real API keys.

---

## Milestone 5 — LangGraph Controlled Flow

**Status:** Done.

Implemented:

- Added graph package with typed `BotState`, intent classifier, route nodes, and LangGraph compilation.
- Connected text and voice Telegram handlers to `run_bot_graph`; incoming DB message IDs now feed `execution_runs`.
- Added controlled routes for admin FAQ, booking start, cancellation placeholder, rescheduling placeholder, emergency/escalation placeholder, and fallback.
- Added execution run start/finish persistence with graph input, graph output, intent, status, duration, and tool-call metadata.
- Added node-level structured logging for key graph nodes.
- Added graph tests for intent classification, FAQ flow, booking start, medical safety, and execution run persistence.

Verified:

- Targeted lint for graph and Telegram adapter files passed.
- Test suite passed: `21 passed`.

### Goal

Implement the main controlled AI workflow.

### Tasks

1. Create typed `BotState`.
2. Create graph nodes:
   - load_user_context;
   - classify_intent;
   - safety_guard;
   - route_intent;
   - admin_faq;
   - start_booking;
   - continue_booking;
   - cancel_appointment;
   - reschedule_appointment;
   - emergency_or_escalation;
   - fallback.
3. Implement intent enum:
   - admin_faq;
   - book_appointment;
   - cancel_appointment;
   - reschedule_appointment;
   - medical_question;
   - emergency;
   - discount_request;
   - non_standard_service;
   - angry_user;
   - unknown.
4. Implement graph input adapter from Telegram update.
5. Implement graph output adapter to Telegram messages/callbacks.
6. Add execution_runs persistence.
7. Add node-level logging.
8. Add tool-call logging.

### Acceptance Criteria

- Text message goes through LangGraph.
- Intent is classified.
- FAQ route works.
- Medical question route does not give advice.
- Booking route can start controlled flow.
- execution_runs contains graph input/output and status.

---

## Milestone 6 — Medical Safety and Escalation

**Status:** Done.

Implemented:

- Adding DB escalation creation, admin notification service, phone detection, and graph routes for emergency/unclear fallback cases.
- Added `admin_notify` service and wired graph escalation nodes to create DB escalations and send Telegram admin summaries when `ADMIN_TELEGRAM_CHAT_ID` is configured.
- Emergency, angry user, discount, non-standard service, and unknown FAQ cases now create `escalations`.
- Admin summaries include escalation id, reason, Telegram user, phone when present, user message, conversation summary, and trace id.
- The graph asks for phone only when the escalation message does not already contain one.
- Medical advice requests still route through safety refusal and do not recommend medicines.
- Added tests for emergency escalation with admin notification and unknown FAQ escalation without admin bot.

Verified:

- Targeted lint for safety/escalation files passed.
- Test suite passed: `23 passed`.

### Goal

Make the bot safe for administrative-only dental use.

### Tasks

1. Implement safety guard prompt/rules.
2. Detect medical advice requests.
3. Detect possible emergency/urgent cases.
4. Detect angry user/fallback cases.
5. Implement escalation creation in DB.
6. Implement admin notification service.
7. Ask user for phone if missing.
8. Send admin group summary.

### Required bot behavior

If user asks:

```text
У меня болит зуб, что выпить?
```

Bot must answer approximately:

```text
Я не могу давать медицинские рекомендации или советовать лекарства. Давайте я запишу вас на консультацию к терапевту. Ближайшее свободное время: ...
```

### Acceptance Criteria

- Bot never recommends medicine.
- Bot offers consultation.
- Emergency/unclear cases create escalation.
- Admin group receives summary.
- Escalation is saved in DB.

---

## Milestone 7 — Google Calendar Integration

**Status:** Done.

Implemented:

- Adding Google Calendar service abstraction, event models, service account client factory, availability calculation, and mocked tests.
- Added calendar package with Google service account client factory, event CRUD wrapper, event body formatting, Calendar-to-busy-event parsing, and availability slot rules.
- Implemented service methods for list/get/create/update/cancel calendar events.
- Event creation includes title, description, start/end timezone, and `extendedProperties.private` bot metadata.
- Availability respects Monday-Saturday, 09:00-21:00, service durations, doctor capacity, two-cabinet capacity, and conservative unknown-event blocking.
- Added mocked tests for Google Calendar CRUD payloads and availability conflict logic.

Verified:

- Targeted lint for calendar files passed.
- Test suite passed: `28 passed`.

### Goal

Implement Calendar read/write and availability logic.

### Tasks

1. Add Google service account support.
2. Configure calendar ID through env.
3. Implement calendar service:
   - list events;
   - create event;
   - update event;
   - cancel/delete event;
   - get event.
4. Implement availability calculation.
5. Respect clinic working hours:
   - Monday–Saturday;
   - 09:00–21:00;
   - timezone `Asia/Tashkent`.
6. Respect service durations:
   - consultation: 30 min;
   - cleaning: 60 min;
   - treatment: 90 min.
7. Respect capacity:
   - therapist max 1 appointment at same time;
   - surgeon max 1 appointment at same time;
   - total simultaneous appointments max 2.
8. Treat unknown calendar events conservatively.
9. Add extendedProperties.private to bot-created events.
10. Add appointment metadata to event description.

### Acceptance Criteria

- App can list calendar events.
- App can find free slots.
- App can create event.
- App can update event.
- App can cancel/delete event.
- Calendar conflicts are detected before final booking.

---

## Milestone 8 — Booking Flow

**Status:** Done.

Implemented:

- Adding booking draft state, patient data collection, contact request keyboard, slot proposal keyboard, booking slot callback, DB appointment creation, Calendar event creation, reminder jobs, and admin notification.
- Added booking service that stores temporary booking draft state in conversation state, collects patient name and phone, proposes three available slots, and confirms selected slot.
- Added Telegram contact request keyboard and inline slot selection keyboard.
- Added booking slot callback handler that re-checks availability, creates DB appointment, creates Google Calendar event when configured, schedules reminder jobs, saves phone, confirms to user, and notifies admins.
- Booking intent now continues existing booking flow even when the next user message only contains name/phone/contact.
- Added booking flow tests for data collection, slot proposal, appointment creation, calendar event creation, reminders, phone persistence, admin notification, and keyboard rendering.

Verified:

- Targeted lint for booking/Telegram files passed.
- Test suite passed: `30 passed`.

### Goal

User can book appointment end-to-end.

### Tasks

1. Detect booking intent.
2. Determine service type and doctor type.
3. Collect missing data:
   - patient name;
   - phone.
4. Implement Telegram contact request button.
5. Allow manual phone input.
6. Propose 3 nearest slots.
7. Show slots as inline buttons.
8. Handle slot selection callback.
9. Re-check availability.
10. Create Google Calendar event.
11. Create DB appointment.
12. Create reminder jobs.
13. Generate conversation summary.
14. Send confirmation to user.
15. Send admin notification.

### Acceptance Criteria

- User can book appointment fully through Telegram.
- Google Calendar event is created.
- DB appointment is created.
- Reminder jobs are created.
- Admin group receives summary.
- User receives localized confirmation.

---

## Milestone 9 — Cancellation Flow

### Goal

User can cancel appointment without calling admin.

### Tasks

1. Detect cancellation intent.
2. Find future active appointments by Telegram ID.
3. If one appointment, ask for confirmation.
4. If multiple appointments, show list.
5. On confirmation:
   - cancel/delete Google Calendar event;
   - update DB appointment status;
   - write appointment_history;
   - cancel reminder jobs;
   - notify admin group;
   - confirm to user.

### Acceptance Criteria

- User can cancel active appointment.
- Google Calendar is updated.
- DB is updated.
- Reminders are cancelled.
- Admin group is notified.

---

## Milestone 10 — Rescheduling Flow

### Goal

User can reschedule appointment.

### Tasks

1. Detect rescheduling intent.
2. Find active future appointments.
3. Ask user to choose appointment if several.
4. Ask preferred date/time or propose nearest slots.
5. Recalculate slots.
6. Show new options.
7. Re-check availability.
8. Update Google Calendar event.
9. Update DB appointment.
10. Write appointment_history.
11. Reschedule reminder jobs.
12. Notify admin group.
13. Confirm to user.

### Acceptance Criteria

- User can reschedule appointment.
- Google Calendar event time changes.
- DB reflects new time.
- Reminder jobs are updated.
- Admin group is notified.

---

## Milestone 11 — Reminder Worker

### Goal

Send appointment reminders 24 hours and 2 hours before appointment.

### Tasks

1. Implement reminder_jobs table usage.
2. On appointment creation, create:
   - day_before reminder;
   - two_hours_before reminder.
3. Implement reminder worker loop.
4. Worker finds pending reminders due now.
5. Before sending, worker verifies:
   - appointment is still scheduled;
   - Google Calendar event exists;
   - event time matches DB or syncs first.
6. Send localized reminder text.
7. Mark reminder as sent.
8. On failure, mark failed and store error.

### Acceptance Criteria

- 24-hour reminder is sent.
- 2-hour reminder is sent.
- Cancelled appointments do not receive reminders.
- Rescheduled appointments receive reminders at new time.

---

## Milestone 12 — Calendar Sync Worker

### Goal

Keep DB aligned with Google Calendar.

### Tasks

1. Run worker every 10 minutes.
2. Read calendar window:
   - now minus 7 days;
   - now plus 60 days.
3. Compare with DB.
4. If calendar event time changed, update DB.
5. If calendar event deleted/cancelled, mark appointment cancelled in DB.
6. If calendar event exists with bot metadata but DB record is missing, restore/create DB record when possible.
7. Write appointment_history.
8. Write sync logs.

### Acceptance Criteria

- Manual time change in Google Calendar updates DB within 10 minutes.
- Manual deletion/cancellation in Calendar updates DB.
- Worker logs sync results.

---

## Milestone 13 — Tracing and Observability

### Goal

Make project convenient to debug like n8n executions.

### Tasks

1. Add JSON logs everywhere.
2. Add trace_id per Telegram update.
3. Add execution_runs table writes.
4. Store graph input/output.
5. Store status and duration.
6. Store major tool calls.
7. Integrate LangSmith.
8. Add env flag so app works without LangSmith key.
9. Add optional OpenTelemetry flags.
10. Add README section: how to inspect an execution by trace_id.

### Acceptance Criteria

- Every user interaction has trace_id.
- Every trace_id can be found in logs.
- execution_runs contains result of each graph run.
- LangSmith traces appear when credentials are configured.
- App still works if LangSmith is disabled.

---

## Milestone 14 — GitHub Actions Deployment

### Goal

Push to GitHub automatically deploys to VPS.

### Tasks

1. Create `.github/workflows/deploy.yml`.
2. Add SSH deployment logic.
3. Add required GitHub Secrets:
   - VPS_HOST;
   - VPS_USER;
   - VPS_SSH_KEY;
   - VPS_PROJECT_DIR;
   - optional VPS_PORT.
4. On push to `main`, workflow:
   - connects to VPS;
   - enters project directory;
   - runs `git pull`;
   - runs `docker compose up -d --build bot`;
   - runs DB migrations;
   - shows `docker compose ps`;
   - shows recent logs.
5. Create `scripts/deploy.sh` for the VPS side.
6. Make deployment idempotent.

### Acceptance Criteria

- Push to `main` triggers deployment.
- VPS pulls latest code.
- Bot is rebuilt and restarted.
- Migrations run.
- Logs are visible in GitHub Actions output.

---

## Milestone 15 — VPS and Caddy Integration

### Goal

Make bot reachable through HTTPS domain on VPS.

### Tasks

1. Owner points domain/subdomain to VPS.
2. Add Caddy config example:

```caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

3. Docker Compose maps bot to localhost:

```yaml
ports:
  - "127.0.0.1:8000:8000"
```

4. Add Telegram webhook setup script:

```text
scripts/set_telegram_webhook.sh
```

5. Webhook URL format:

```text
https://bot.example.com/telegram/webhook
```

6. Use Telegram webhook secret.

### Acceptance Criteria

- Domain opens bot health endpoint.
- Caddy terminates HTTPS.
- Telegram webhook points to domain.
- Telegram updates reach bot.
- Secret token is checked.

---

## Milestone 16 — Automated Tests

### Goal

Cover critical flows with mocks.

### Tasks

Add tests for:

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

### Acceptance Criteria

- Tests run locally.
- External APIs are mocked.
- Tests can run in CI.
- Critical flows are covered.

---

## Milestone 17 — Final MVP QA

### Goal

Verify the entire MVP manually before real users.

### Manual QA Checklist

Test each language:

- Russian;
- Uzbek;
- English.

For each language test:

1. `/start`
2. language selection
3. text FAQ
4. voice FAQ
5. medical safety case
6. booking
7. cancellation
8. rescheduling
9. reminder simulation
10. admin notification
11. calendar sync after manual Calendar edit

### Uzbek QA

Uzbek quality must be checked by native/fluent Uzbek speakers.

Check:

- Uzbek Latin output readability;
- mixed Uzbek/Russian input;
- voice transcription quality;
- TTS quality;
- booking flow clarity.

### Acceptance Criteria

- All core scenarios pass.
- No long-term audio files remain on disk.
- Calendar and DB stay consistent.
- Admin group receives useful summaries.
- Logs/traces are enough to debug failures.

---

## 3. Suggested Work Order for Codex

Use this order when working with Codex:

1. Ask Codex to create project skeleton.
2. Ask Codex to add Docker Compose and config.
3. Ask Codex to add DB models and migrations.
4. Ask Codex to add Telegram `/start` and language selection.
5. Ask Codex to add message persistence.
6. Ask Codex to add clinic knowledge base.
7. Ask Codex to add basic LangGraph flow.
8. Ask Codex to add speech provider interfaces and mocks.
9. Ask Codex to add OpenAI/Muxlisa providers.
10. Ask Codex to add Google Calendar service.
11. Ask Codex to add booking flow.
12. Ask Codex to add cancellation.
13. Ask Codex to add rescheduling.
14. Ask Codex to add admin notifications.
15. Ask Codex to add reminders.
16. Ask Codex to add calendar sync.
17. Ask Codex to add LangSmith tracing and execution_runs.
18. Ask Codex to add tests.
19. Ask Codex to add GitHub Actions deploy.
20. Ask Codex to update README.

Do not ask Codex to implement everything in one prompt.

---

## 4. First Prompt for Codex

Use this as the first development prompt:

```text
You are building a Python monorepo project for a Telegram dental clinic assistant.

Start only with the project skeleton and infrastructure foundation.

Requirements:
- Python 3.12.
- aiogram 3 will be used later.
- LangGraph will be used later.
- PostgreSQL 16.
- Docker Compose.
- Monorepo structure:
  apps/bot/
  infra/
  scripts/
  .github/workflows/
- Create apps/bot Dockerfile.
- Create infra/docker-compose.yml with bot and postgres.
- Create .env.example.
- Create basic Pydantic settings.
- Create basic app entrypoint with health endpoint.
- Create structured JSON logging.
- Create README with local run instructions.
- Do not implement Telegram, DB models, LangGraph or external APIs yet.
- The app must run locally via Docker Compose.
- The bot HTTP server should expose port 8000 inside the container.
- Docker Compose should map it to 127.0.0.1:8000:8000 for Caddy compatibility on VPS.

Acceptance criteria:
- docker compose up -d --build starts bot and postgres.
- GET /health returns OK.
- App reads env vars.
- Logs are JSON.
- No secrets are committed.
```

---

## 5. Second Prompt for Codex

After skeleton is ready:

```text
Now add the database foundation.

Requirements:
- Use SQLAlchemy 2 async.
- Use asyncpg.
- Use Alembic.
- Add models and migrations for:
  users,
  user_phones,
  conversations,
  messages,
  appointments,
  appointment_history,
  clinic_knowledge,
  escalations,
  reminder_jobs,
  execution_runs.
- Add repository classes for common operations.
- Add tests for basic create/read/update operations.
- Update README with migration commands.

Acceptance criteria:
- alembic upgrade head creates all tables.
- Tests pass.
- App can connect to DB inside Docker Compose.
```

---

## 6. Third Prompt for Codex

After DB foundation:

```text
Now add Telegram webhook base.

Requirements:
- Use aiogram 3.
- Implement webhook endpoint at /telegram/webhook.
- Implement /start.
- Implement /language.
- Implement /help.
- Implement /my_appointments as placeholder.
- On first /start, show language selection inline keyboard:
  Russian, Uzbek, English.
- Save selected language to DB.
- Save incoming and outgoing messages to DB.
- Add trace_id for every update.
- Validate Telegram webhook secret token.

Acceptance criteria:
- Telegram webhook receives updates.
- User can select language.
- Language is saved to DB.
- Messages are saved to DB.
- Logs include trace_id.
```

---

## 7. Definition of Done for MVP

The MVP is done when:

1. Local Docker setup works.
2. GitHub Actions deployment works.
3. VPS deployment works behind existing Caddy.
4. Telegram webhook works through HTTPS domain.
5. User can choose language.
6. User can send text.
7. User can send voice.
8. Bot replies in selected language.
9. Voice input gets voice + text response.
10. Bot answers admin FAQ.
11. Bot refuses medical advice safely.
12. Bot books appointment into Google Calendar.
13. Appointment is saved to DB.
14. Bot cancels appointment.
15. Bot reschedules appointment.
16. Bot sends admin group notifications.
17. Bot sends 24h and 2h reminders.
18. Calendar sync updates DB after manual calendar change.
19. Full message history is saved.
20. Trace/logs allow debugging each execution.
```
