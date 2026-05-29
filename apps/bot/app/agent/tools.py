"""LangChain @tool definitions for the Madina VoiceFlow agent."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import (
    CalendarConfigError,
    CalendarEventCreate,
    CalendarEventUpdate,
    GoogleCalendarService,
    calendar_events_to_busy_events,
    create_google_calendar_service,
    find_available_slots,
    is_slot_available,
)
from app.db.repositories import (
    AppointmentRepository,
    EscalationRepository,
    ReminderRepository,
)
from app.services.admin_notify import send_admin_notification
from app.services.clinic_knowledge import get_clinic_knowledge
from app.telegram.texts import Language

DEFAULT_TIMEZONE = "Asia/Tashkent"


def _get_config(config: RunnableConfig) -> dict[str, Any]:
    return config.get("configurable", {})


def _get_session(config: RunnableConfig) -> AsyncSession:
    return _get_config(config)["session"]


def _get_user(config: RunnableConfig) -> Any:
    return _get_config(config)["user"]


def _get_language(config: RunnableConfig) -> Language:
    return _get_config(config).get("language", "ru")


def _get_calendar(config: RunnableConfig) -> GoogleCalendarService | None:
    return _get_config(config).get("calendar_service")


def _get_admin_bot(config: RunnableConfig) -> Any | None:
    return _get_config(config).get("admin_bot")


def _resolve_calendar(calendar_service: GoogleCalendarService | None) -> GoogleCalendarService | None:
    if calendar_service is not None:
        return calendar_service
    try:
        return create_google_calendar_service()
    except CalendarConfigError:
        return None


# ──────────────────── Patient tools (Режим A) ────────────────────


class SearchKnowledgeBaseInput(BaseModel):
    query: str = Field(description="Поисковый запрос: услуга, цена, врач, адрес, часы работы, FAQ")


@tool(args_schema=SearchKnowledgeBaseInput)
async def search_knowledge_base(query: str, config: RunnableConfig) -> str:
    """Поиск по базе знаний клиники: цены, услуги, врачи, адрес, часы работы, FAQ."""
    session = _get_session(config)
    language = _get_language(config)
    knowledge = await get_clinic_knowledge(session, language)
    if not knowledge:
        return "База знаний пуста. Ответь: «Уточню у администратора и вернусь.»"
    return f"База знаний клиники:\n{knowledge}"


class CheckCalendarSlotsInput(BaseModel):
    date_from: str = Field(description="Дата начала в ISO формате YYYY-MM-DD")
    date_to: str = Field(description="Дата окончания в ISO формате YYYY-MM-DD")
    doctor: str | None = Field(default=None, description="Тип врача: therapist или surgeon")
    service: str | None = Field(default=None, description="Тип услуги: consultation, cleaning, treatment")


@tool(args_schema=CheckCalendarSlotsInput)
async def check_calendar_slots(
    date_from: str,
    date_to: str,
    doctor: str | None = None,
    service: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Проверить свободные слоты в Google Calendar. Вызывать ПЕРЕД тем, как предложить пациенту время."""
    calendar_service = _get_calendar(config)
    resolved = _resolve_calendar(calendar_service)
    service_type = service or "consultation"
    doctor_type = doctor or "therapist"

    try:
        start_dt = datetime.fromisoformat(date_from)
        end_dt = datetime.fromisoformat(date_to)
    except ValueError:
        return "Ошибка: неверный формат даты. Используй YYYY-MM-DD."

    if resolved is None:
        tz = ZoneInfo(DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        slots = [
            {"start": (now + timedelta(hours=h)).isoformat(), "end": (now + timedelta(hours=h + 1)).isoformat()}
            for h in range(1, 6)
            if (now + timedelta(hours=h)).weekday() != 6
        ]
        return f"Календарь не подключён. Доступны демо-слоты: {slots}"

    events = await resolved.list_events(time_min=start_dt, time_max=end_dt)
    busy_events = calendar_events_to_busy_events(events)
    slots = find_available_slots(
        busy_events=busy_events,
        service_type=service_type,
        doctor_type=doctor_type,
        start_from=start_dt,
        limit=5,
    )
    if not slots:
        return "Свободных слотов нет. Предложи пациенту другой день или передай администратору."
    return "Свободные слоты:\n" + "\n".join(
        f"- {s.start_at.isoformat()} ({s.service_type}, {s.doctor_type})" for s in slots
    )


class CreateAppointmentInput(BaseModel):
    patient_name: str = Field(description="Имя пациента")
    phone: str = Field(description="Номер телефона пациента")
    service: str = Field(description="Тип услуги: consultation, cleaning, treatment")
    datetime_str: str = Field(description="Дата и время записи в ISO формате YYYY-MM-DDTHH:MM")
    doctor: str | None = Field(default=None, description="Тип врача: therapist или surgeon")


@tool(args_schema=CreateAppointmentInput)
async def create_appointment(
    patient_name: str,
    phone: str,
    service: str,
    datetime_str: str,
    doctor: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Создать запись пациента в БД и Google Calendar. Вызывать ТОЛЬКО после явного согласия пациента."""
    session = _get_session(config)
    user = _get_user(config)
    calendar_service = _get_calendar(config)
    admin_bot = _get_admin_bot(config)
    service_type = service or "consultation"
    doctor_type = doctor or "therapist"

    try:
        start_at = datetime.fromisoformat(datetime_str)
    except ValueError:
        return "Ошибка: неверный формат даты. Используй YYYY-MM-DDTHH:MM."

    duration_minutes = {"consultation": 30, "cleaning": 60, "treatment": 90}.get(service_type, 30)
    end_at = start_at + timedelta(minutes=duration_minutes)
    tz = ZoneInfo(DEFAULT_TIMEZONE)

    resolved = _resolve_calendar(calendar_service)
    if resolved is not None:
        busy_events = calendar_events_to_busy_events(
            await resolved.list_events(time_min=start_at - timedelta(minutes=1), time_max=end_at + timedelta(minutes=1))
        )
        if not is_slot_available(busy_events=busy_events, start_at=start_at, end_at=end_at, doctor_type=doctor_type, timezone=DEFAULT_TIMEZONE):
            return "Слот занят. Проверь календарь заново и предложи другое время."

    repo = AppointmentRepository(session)
    appointment = await repo.create(
        user_id=user.id,
        service_type=service_type,
        doctor_type=doctor_type,
        start_at=start_at,
        end_at=end_at,
        patient_name=patient_name,
        primary_phone=phone,
        timezone=DEFAULT_TIMEZONE,
    )

    calendar_event_id: str | None = None
    if resolved is not None:
        event_data = CalendarEventCreate(
            service_type=service_type,
            doctor_type=doctor_type,
            start_at=start_at,
            end_at=end_at,
            timezone=DEFAULT_TIMEZONE,
            patient_name=patient_name,
            phone=phone,
            telegram_user_id=user.telegram_user_id,
            telegram_username=user.telegram_username,
            language=_get_language(config),
            conversation_summary=None,
            appointment_id=appointment.id,
            trace_id="",
        )
        event = await resolved.create_event(event_data)
        calendar_event_id = event.get("id")
        appointment.calendar_event_id = calendar_event_id
        await session.flush()

    await _schedule_reminders(session, appointment)
    await send_admin_notification(
        bot=admin_bot,
        message_text=f"Новая запись: {patient_name}, {phone}, {service_type}, {start_at.isoformat()}",
    )

    formatted = start_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
    return f"Запись создана: {formatted}, {service_type}, врач {doctor_type}. ID записи: {appointment.id}."


class UpdateAppointmentInput(BaseModel):
    appointment_id: int = Field(description="ID записи для переноса")
    new_datetime: str | None = Field(default=None, description="Новая дата и время в ISO формате YYYY-MM-DDTHH:MM")
    new_doctor: str | None = Field(default=None, description="Новый тип врача: therapist или surgeon")


@tool(args_schema=UpdateAppointmentInput)
async def update_appointment(
    appointment_id: int,
    new_datetime: str | None = None,
    new_doctor: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Перенести запись на новое время или к другому врачу."""
    session = _get_session(config)
    calendar_service = _get_calendar(config)
    from app.db.models import Appointment as AppointmentModel

    appointment = await session.get(AppointmentModel, appointment_id)
    if appointment is None or appointment.status != "scheduled":
        return "Запись не найдена или не в статусе scheduled."

    repo = AppointmentRepository(session)
    old_start = appointment.start_at
    if new_datetime:
        try:
            new_start = datetime.fromisoformat(new_datetime)
        except ValueError:
            return "Ошибка: неверный формат даты. Используй YYYY-MM-DDTHH:MM."
        duration = int((appointment.end_at - old_start).total_seconds() // 60)
        new_end = new_start + timedelta(minutes=duration)
        appointment.start_at = new_start
        appointment.end_at = new_end

    if new_doctor:
        appointment.doctor_type = new_doctor

    await session.flush()
    await repo.add_history(
        appointment_id=appointment.id,
        action="rescheduled",
        actor="bot",
        old_data={"start_at": old_start.isoformat()},
        new_data={"start_at": appointment.start_at.isoformat()},
    )

    resolved = _resolve_calendar(calendar_service)
    if resolved is not None and appointment.calendar_event_id:
        await resolved.update_event(
            appointment.calendar_event_id,
            CalendarEventUpdate(start_at=appointment.start_at, end_at=appointment.end_at),
        )

    await ReminderRepository(session).cancel_for_appointment(appointment_id)
    await _schedule_reminders(session, appointment)

    tz = ZoneInfo(appointment.timezone)
    formatted = appointment.start_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
    return f"Запись перенесена на {formatted}, врач {appointment.doctor_type}."


class CancelAppointmentInput(BaseModel):
    appointment_id: int = Field(description="ID записи для отмены")
    reason: str | None = Field(default=None, description="Причина отмены")


@tool(args_schema=CancelAppointmentInput)
async def cancel_appointment(
    appointment_id: int,
    reason: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Отменить запись пациента."""
    session = _get_session(config)
    user = _get_user(config)
    calendar_service = _get_calendar(config)
    admin_bot = _get_admin_bot(config)

    from app.db.models import Appointment as AppointmentModel

    appointment = await session.get(AppointmentModel, appointment_id)
    if appointment is None or appointment.user_id != user.id:
        return "Запись не найдена или не принадлежит пользователю."
    if appointment.status != "scheduled":
        return f"Запись уже в статусе {appointment.status}."

    resolved = _resolve_calendar(calendar_service)
    if resolved is not None and appointment.calendar_event_id:
        await resolved.cancel_event(appointment.calendar_event_id)

    repo = AppointmentRepository(session)
    await repo.cancel(appointment_id=appointment_id, actor="user")
    await ReminderRepository(session).cancel_for_appointment(appointment_id)

    await send_admin_notification(
        bot=admin_bot,
        message_text=f"Отмена записи: {appointment.patient_name}, {appointment.primary_phone}, {appointment.service_type}, {appointment.start_at.isoformat()}. Причина: {reason or '-'}",
    )

    return f"Запись #{appointment_id} отменена."


@tool
async def view_appointments(config: RunnableConfig) -> str:
    """Показать активные записи текущего пользователя."""
    session = _get_session(config)
    user = _get_user(config)
    appointments = await AppointmentRepository(session).get_active_future_by_user(user_id=user.id)
    if not appointments:
        return "У вас нет активных записей."
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    lines = ["Ваши активные записи:"]
    for a in appointments:
        start = a.start_at.astimezone(tz).strftime("%d.%m.%Y %H:%M")
        lines.append(f"- #{a.id}: {start}, {a.service_type}, врач {a.doctor_type}")
    return "\n".join(lines)


class OrderTaxiInput(BaseModel):
    pickup: str = Field(description="Адрес подачи (адрес клиники или пациента)")
    destination: str = Field(description="Адрес назначения")
    when: str | None = Field(default=None, description="Когда подать: сейчас или дата/время")


@tool(args_schema=OrderTaxiInput)
async def order_taxi(pickup: str, destination: str, when: str | None = None, config: RunnableConfig = None) -> str:
    """Заказать такси для пациента. Только по явной просьбе пациента."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    esc_repo = EscalationRepository(session)
    esc = await esc_repo.create(
        user_id=user.id,
        reason="taxi",
        summary=f"Заказ такси: от {pickup} до {destination}, когда: {when or 'сейчас'}",
    )
    await send_admin_notification(
        bot=admin_bot,
        message_text=f"Заказ такси: pickup={pickup}, destination={destination}, when={when or 'сейчас'}, user_id={user.id}",
    )
    await session.flush()
    return f"Заявка на такси создана (escalation #{esc.id}). Администратор свяжется для уточнения."


class OrderPharmacyInput(BaseModel):
    items: str = Field(description="Список лекарств через запятую")
    address: str = Field(description="Адрес доставки")
    when: str | None = Field(default=None, description="Когда доставить")


@tool(args_schema=OrderPharmacyInput)
async def order_pharmacy(items: str, address: str, when: str | None = None, config: RunnableConfig = None) -> str:
    """Заказать лекарства из аптеки. Только по явной просьбе пациента."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    esc_repo = EscalationRepository(session)
    esc = await esc_repo.create(
        user_id=user.id,
        reason="pharmacy",
        summary=f"Заказ лекарств: {items}, адрес: {address}, когда: {when or 'сейчас'}",
    )
    await send_admin_notification(
        bot=admin_bot,
        message_text=f"Заказ аптеки: items={items}, address={address}, when={when or 'сейчас'}, user_id={user.id}",
    )
    await session.flush()
    return f"Заявка на лекарства создана (escalation #{esc.id}). Администратор свяжется для уточнения."


class EscalateToAdminInput(BaseModel):
    summary: str = Field(description="Краткое описание ситуации")
    patient_contact: str = Field(description="Контакт пациента: телефон или @username")
    urgency: str = Field(default="normal", description="Срочность: low, normal, high")


@tool(args_schema=EscalateToAdminInput)
async def escalate_to_admin(summary: str, patient_contact: str, urgency: str = "normal", config: RunnableConfig = None) -> str:
    """Эскалировать вопрос администратору клиники. Использовать при жалобах, возвратах, юридических вопросах,
    запросе медкарты, просьбе соединить с человеком, или если услуга/цена не найдена после двух поисков."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    esc_repo = EscalationRepository(session)
    esc = await esc_repo.create(
        user_id=user.id,
        reason=f"escalation_{urgency}",
        summary=summary,
        phone=patient_contact,
    )
    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=f"Эскалация ({urgency}):\n{summary}\nКонтакт: {patient_contact}\nUser: {user.telegram_user_id}",
    )
    if notification.admin_chat_id:
        esc.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id:
        esc.admin_message_id = notification.admin_message_id
    await session.flush()
    return f"Эскалация #{esc.id} создана. Администратор свяжется с пациентом."


# ──────────────────── Owner/Sales tools (Режим B) ────────────────────

SHEVTSOV_TG = "@softretail"
SHEVTSOV_PHONE = "+998 50 890 98 33"
AMIR_BOT = "@Ai_Soft_Retail_a8i_bot"


class NotifySalesInput(BaseModel):
    stage: str = Field(description="Этап продаж: warm, hot, cold_lead")
    clinic_name: str | None = Field(default=None, description="Название клиники")
    owner_contact: str = Field(description="Контакт собственника: @username или телефон")
    details: str | None = Field(default=None, description="Детали: имя, локации, сумма")


@tool(args_schema=NotifySalesInput)
async def notify_sales(
    stage: str,
    clinic_name: str | None,
    owner_contact: str,
    details: str | None = None,
    config: RunnableConfig = None,
) -> str:
    """Отправить алерт в Telegram-канал «Заказы от Мадины».
    stage='warm' — собственник назвал клинику, идёт примерка.
    stage='hot' — собственник согласился подключиться.
    stage='cold_lead' — собственник не ответил после возвратного сообщения."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    summary = f"Sales lead\n\nStage: {stage}\nClinic: {clinic_name or '-'}\nContact: {owner_contact}\nDetails: {details or '-'}"
    esc = await EscalationRepository(session).create(user_id=user.id, reason=f"sales_{stage}", summary=summary)
    notification = await send_admin_notification(bot=admin_bot, message_text=summary)
    if notification.admin_chat_id:
        esc.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id:
        esc.admin_message_id = notification.admin_message_id
    await session.flush()
    return f"Уведомление продаж stage={stage} отправлено."


class HandoffToAmirInput(BaseModel):
    clinic_name: str | None = Field(default=None, description="Название клиники")
    owner_contact: str = Field(description="Контакт собственника")
    conversation_summary: str = Field(description="Краткое резюме разговора")
    development_zone: str = Field(description="Зона интереса: data_privacy, general_ai_interest, hesitating, post_purchase")


@tool(args_schema=HandoffToAmirInput)
async def handoff_to_amir(
    clinic_name: str | None,
    owner_contact: str,
    conversation_summary: str,
    development_zone: str,
    config: RunnableConfig = None,
) -> str:
    """Передать собственника коллеге Амиру для консультации по AI-инструментам.
    Использовать когда: собственник копает в безопасность данных, интересуется другими AI-продуктами, колеблется."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    summary = f"Amir handoff\n\nStage: handoff\nClinic: {clinic_name or '-'}\nContact: {owner_contact}\nZone: {development_zone}\n{conversation_summary}"
    esc = await EscalationRepository(session).create(user_id=user.id, reason="handoff_to_amir", summary=summary)
    notification = await send_admin_notification(bot=admin_bot, message_text=summary)
    if notification.admin_chat_id:
        esc.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id:
        esc.admin_message_id = notification.admin_message_id
    await session.flush()
    return f"Собственник передан Амиру (development_zone={development_zone}). Сообщи собственнику: «Амир — мой коллега — подскажет, какие из наших AI-инструментов будут вам полезны. Бесплатно: {AMIR_BOT}»"


class ScheduleFollowupInput(BaseModel):
    owner_contact: str = Field(description="Контакт собственника")
    when_iso: str = Field(description="Дата и время возврата в ISO формате YYYY-MM-DDTHH:MM:SS+TZ")
    context_summary: str = Field(description="О чём говорили, что нужно продолжить")
    timezone: str = Field(default=DEFAULT_TIMEZONE, description="Часовой пояс")


@tool(args_schema=ScheduleFollowupInput)
async def schedule_followup(
    owner_contact: str,
    when_iso: str,
    context_summary: str,
    timezone: str = DEFAULT_TIMEZONE,
    config: RunnableConfig = None,
) -> str:
    """Запланировать возврат к разговору в согласованное время. Только ОДИН возврат.
    Если собственник не ответит — вызвать notify_sales(stage='cold_lead')."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    summary = f"Sales follow-up\n\nStage: followup\nContact: {owner_contact}\nWhen: {when_iso}\nTimezone: {timezone}\nContext: {context_summary}"
    await EscalationRepository(session).create(user_id=user.id, reason="sales_followup", summary=summary)
    await send_admin_notification(bot=admin_bot, message_text=summary)
    await session.flush()
    return f"Возврат к разговору запланирован на {when_iso} ({timezone}). Подтверди собственнику время и не пиши больше до этого момента."


class SendInvoiceInput(BaseModel):
    owner_name: str = Field(description="Имя собственника")
    clinic_name: str = Field(description="Название клиники")
    phone: str = Field(description="Номер телефона собственника")
    amount: str = Field(description="Сумма в долларах, например $200/мес")
    locations: int = Field(default=1, description="Количество локаций")


@tool(args_schema=SendInvoiceInput)
async def send_invoice(
    owner_name: str,
    clinic_name: str,
    phone: str,
    amount: str,
    locations: int = 1,
    config: RunnableConfig = None,
) -> str:
    """Отправить счёт на оплату собственнику клиники. Вызывать после закрытия сделки (stage='hot').
    Сейчас плейсхолдер — создаёт escalation для ручной отправки счёта."""
    session = _get_session(config)
    user = _get_user(config)
    admin_bot = _get_admin_bot(config)

    summary = (
        f"INVOICE REQUEST\n\n"
        f"Owner: {owner_name}\n"
        f"Clinic: {clinic_name}\n"
        f"Phone: {phone}\n"
        f"Amount: {amount}\n"
        f"Locations: {locations}\n"
        f"Contact: @{user.telegram_username} / id {user.telegram_user_id}"
    )
    esc = await EscalationRepository(session).create(user_id=user.id, reason="invoice_request", summary=summary)
    notification = await send_admin_notification(bot=admin_bot, message_text=summary)
    if notification.admin_chat_id:
        esc.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id:
        esc.admin_message_id = notification.admin_message_id
    await session.flush()
    return (
        f"Заявка на счёт создана. Иван ({SHEVTSOV_TG}, {SHEVTSOV_PHONE}) свяжется с {owner_name} "
        f"в течение часа для отправки реквизитов на оплату {amount}."
    )


# ──────────────────── Helpers ────────────────────


async def _schedule_reminders(session: AsyncSession, appointment: Any) -> None:
    reminders = ReminderRepository(session)
    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    day_before = appointment.start_at - timedelta(hours=24)
    two_hours_before = appointment.start_at - timedelta(hours=2)
    if day_before > now:
        await reminders.create(appointment_id=appointment.id, reminder_type="day_before", send_at=day_before)
    if two_hours_before > now:
        await reminders.create(appointment_id=appointment.id, reminder_type="two_hours_before", send_at=two_hours_before)


ALL_TOOLS = [
    search_knowledge_base,
    check_calendar_slots,
    create_appointment,
    update_appointment,
    cancel_appointment,
    view_appointments,
    order_taxi,
    order_pharmacy,
    escalate_to_admin,
    notify_sales,
    handoff_to_amir,
    schedule_followup,
    send_invoice,
]
