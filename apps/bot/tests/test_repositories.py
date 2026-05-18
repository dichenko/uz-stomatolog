from datetime import UTC, datetime, timedelta

from app.db.repositories import (
    AppointmentRepository,
    ClinicKnowledgeRepository,
    ConversationRepository,
    EscalationRepository,
    ExecutionRunRepository,
    MessageRepository,
    ReminderRepository,
    UserRepository,
)


async def test_user_can_be_created_updated_and_get_phone(session):
    users = UserRepository(session)

    user = await users.upsert_from_telegram(
        telegram_user_id=123,
        telegram_username="ali",
        telegram_first_name="Ali",
        telegram_last_name="Karimov",
        preferred_language="ru",
    )
    await session.commit()

    same_user = await users.set_language(123, "uz")
    phone = await users.add_phone(
        user_id=same_user.id,
        phone="+998901234567",
        is_primary=True,
        source="telegram_contact",
    )
    await session.commit()

    loaded = await users.get_by_telegram_id(123)
    assert loaded is not None
    assert loaded.id == user.id
    assert loaded.preferred_language == "uz"
    assert phone.is_primary is True


async def test_messages_and_execution_runs_are_saved(session):
    users = UserRepository(session)
    conversations = ConversationRepository(session)
    messages = MessageRepository(session)
    runs = ExecutionRunRepository(session)

    user = await users.upsert_from_telegram(telegram_user_id=456)
    conversation = await conversations.get_or_create(
        user_id=user.id,
        telegram_chat_id=456,
    )
    message = await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=10,
        direction="in",
        message_type="text",
        language="en",
        text="Hello",
        raw_payload={"message_id": 10},
        trace_id="trace-1",
    )
    run = await runs.start(
        trace_id="trace-1",
        user_id=user.id,
        conversation_id=conversation.id,
        input_message_id=message.id,
        graph_input={"input_text": "Hello"},
    )
    await runs.finish(
        trace_id=run.trace_id,
        status="success",
        intent="admin_faq",
        graph_output={"final_response_text": "Hi"},
        tool_calls=[{"name": "get_clinic_knowledge"}],
    )
    await session.commit()

    assert message.id is not None
    assert run.status == "success"
    assert run.duration_ms is not None
    assert run.graph_output == {"final_response_text": "Hi"}


async def test_appointment_can_be_created_listed_and_cancelled(session):
    users = UserRepository(session)
    appointments = AppointmentRepository(session)
    reminders = ReminderRepository(session)

    user = await users.upsert_from_telegram(telegram_user_id=789)
    start_at = datetime.now(UTC) + timedelta(days=1)
    end_at = start_at + timedelta(minutes=30)
    appointment = await appointments.create(
        user_id=user.id,
        calendar_event_id="calendar-event-1",
        service_type="consultation",
        doctor_type="therapist",
        start_at=start_at,
        end_at=end_at,
        patient_name="Ali Karimov",
        primary_phone="+998901234567",
        created_trace_id="trace-2",
    )
    reminder = await reminders.create(
        appointment_id=appointment.id,
        reminder_type="day_before",
        send_at=start_at - timedelta(days=1),
    )
    active = await appointments.get_active_future_by_user(user_id=user.id)
    cancelled = await appointments.cancel(appointment_id=appointment.id, actor="user")
    cancelled_count = await reminders.cancel_for_appointment(appointment.id)
    await session.commit()

    assert active == [appointment]
    assert reminder.status == "cancelled"
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_at is not None
    assert cancelled_count == 1


async def test_clinic_knowledge_and_escalation_repositories(session):
    users = UserRepository(session)
    knowledge = ClinicKnowledgeRepository(session)
    escalations = EscalationRepository(session)

    user = await users.upsert_from_telegram(telegram_user_id=321)
    await knowledge.create(language="ru", content="График: 09:00-21:00")
    active_knowledge = await knowledge.get_active_by_language("ru")
    escalation = await escalations.create(
        user_id=user.id,
        reason="medical_question",
        summary="User asked for medicine recommendation",
        phone="+998901234567",
    )
    await session.commit()

    assert active_knowledge is not None
    assert active_knowledge.content.startswith("График")
    assert escalation.status == "new"
