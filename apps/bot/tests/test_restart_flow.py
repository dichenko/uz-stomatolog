from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Appointment, ExecutionRun, Message
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    ExecutionRunRepository,
    MessageRepository,
    UserRepository,
)
from app.telegram.handlers_start import reset_user_dialog_history


async def test_restart_clears_dialog_history_and_keeps_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=9101,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=9101,
    )
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow="booking",
        current_state="collecting_patient",
        summary='{"patient_name": "Ali"}',
    )
    message = await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=1,
        direction="in",
        message_type="text",
        language="en",
        text="Old message",
        trace_id="restart-trace",
    )
    await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=2,
        direction="out",
        message_type="text",
        language="en",
        text="Old answer",
        trace_id="restart-trace",
    )
    await ExecutionRunRepository(session).start(
        trace_id="restart-trace",
        user_id=user.id,
        conversation_id=conversation.id,
        input_message_id=message.id,
        graph_input={"input_text": "Old message"},
    )
    start_at = datetime.now(UTC) + timedelta(days=3)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        patient_name="Ali",
        primary_phone="+998901234567",
    )

    await reset_user_dialog_history(
        session=session,
        user=user,
        conversation=conversation,
    )
    await session.flush()

    messages = (await session.execute(select(Message))).scalars().all()
    runs = (await session.execute(select(ExecutionRun))).scalars().all()
    appointments = (await session.execute(select(Appointment))).scalars().all()

    assert messages == []
    assert runs == []
    assert user.preferred_language is None
    assert conversation.current_flow is None
    assert conversation.current_state is None
    assert conversation.summary is None
    assert appointments == [appointment]
