from datetime import UTC, datetime, timedelta

from app.admin.settings_repository import set_setting
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    MessageRepository,
    UserRepository,
)
from app.services.faq import generate_admin_faq_answer
from app.services.llm_context import build_openai_context_messages


async def test_faq_builds_llm_context_from_db(session, monkeypatch):
    captured = {}

    async def capture_openai_answer(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", capture_openai_answer)

    await set_setting(
        session,
        "clinic.info",
        {"text": "Admin clinic info: open 09:00-21:00"},
        tg_id="admin-1",
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=7001,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=7001,
    )
    messages = MessageRepository(session)
    await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=1,
        direction="in",
        message_type="text",
        language="en",
        text="Do you work on Sunday?",
    )
    await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=2,
        direction="out",
        message_type="text",
        language="en",
        text="We work every day.",
    )
    current = await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=3,
        direction="in",
        message_type="text",
        language="en",
        text="How much is cleaning?",
    )

    start_at = datetime.now(UTC) + timedelta(days=2)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="cleaning",
        doctor_type="therapist",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        patient_name="Ali",
        primary_phone="+998901234567",
        conversation_summary="Booking: cleaning",
    )
    await AppointmentRepository(session).cancel(
        appointment_id=appointment.id,
        actor="user",
    )
    await session.flush()

    await generate_admin_faq_answer(
        question="How much is cleaning?",
        language="en",
        knowledge="Fallback knowledge",
        session=session,
        user=user,
        conversation=conversation,
        input_message_id=current.id,
    )

    context = captured["llm_context"]
    assert captured["knowledge"] == "Admin clinic info: open 09:00-21:00"
    assert context.clinic_info == "Admin clinic info: open 09:00-21:00"
    assert [message.role for message in context.recent_messages] == [
        "user",
        "assistant",
    ]
    assert "How much is cleaning?" not in context.recent_messages[-1].content
    assert "status=cancelled" in context.appointment_history
    assert "action=created" in context.appointment_history
    assert "action=cancelled" in context.appointment_history

    openai_messages = build_openai_context_messages(context)
    assert any(
        "Clinic reference from admin settings" in message["content"]
        for message in openai_messages
        if message["role"] == "system"
    )
    assert any(message["role"] == "assistant" for message in openai_messages)
