# Миграция на LangChain Agent с @tool

**Цель:** LangGraph workflow (ручной intent-роутинг + хардкод tool_calls) → классический LangChain ReAct Agent с @tool-декорированными инструментами

**Дата начала:** 29.05.2026

---

## Этап 1. Зависимости

- [x] 1.1 Добавить `langchain>=0.3`, `langchain-openai>=0.2`, `langchain-anthropic>=0.2` в `apps/bot/pyproject.toml`
- [ ] 1.2 Установить новые зависимости

## Этап 2. Инструменты — `apps/bot/app/agent/tools.py`

### Режим A (Пациент)
- [x] 2.1 `search_knowledge_base(query)` — поиск в Markdown-базе знаний клиники
- [x] 2.2 `check_calendar_slots(date_from, date_to, doctor?, service?)` — свободные слоты из Google Calendar
- [x] 2.3 `create_appointment(patient_name, phone, service, datetime, doctor?)` — создать запись в БД + Google Calendar
- [x] 2.4 `update_appointment(appointment_id, new_datetime?, new_doctor?)` — перенос записи
- [x] 2.5 `cancel_appointment(appointment_id, reason?)` — отмена записи
- [x] 2.6 `view_appointments()` — показать активные записи пользователя
- [x] 2.7 `escalate_to_admin(summary, patient_contact, urgency)` — эскалация администратору

### Режим B (Собственник)
- [x] 2.8 `notify_sales(stage, owner_name?, clinic_name?, owner_contact?, locations?, details?)` — оповещение в чат администраторов

## Этап 3. Middleware — `apps/bot/app/agent/tools.py`

- [x] 3.1 Инъекция `session`, `user`, `conversation`, `admin_bot`, `calendar_service` в tool calls через `RunnableConfig`
- [x] 3.2 Логирование `tool_calls` в `execution_runs`

## Этап 4. Агент — `apps/bot/app/agent/agent.py`

- [x] 4.1 `create_agent()` с `create_react_agent` (LangGraph prebuilt)
- [x] 4.2 Системный промпт (адаптированный v1.2)
- [x] 4.3 `ChatAnthropic` / `ChatOpenAI` с поддержкой tool calling
- [x] 4.4 `run_agent()` — точка входа для Telegram-хендлеров

## Этап 5. Интеграция в Telegram-хендлеры

- [x] 5.1 Заменить `run_bot_graph()` на `run_agent()` в `handlers_messages.py`
- [x] 5.2 Обработка ошибок агента с fallback-сообщением

## Этап 6. Очистка

- [x] 6.1 Обновить `graph/__init__.py` — экспорт `run_agent` + `run_bot_graph`
- [ ] 6.2 Удалить/закомментировать старый код после верификации

## Этап 7. Описание инструментов для промпта

- [x] 7.1 Составить секцию «Инструменты» с именами, параметрами и правилами вызова
