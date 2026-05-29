from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.config import Settings
from app.db.models import Escalation, ExecutionRun
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    MessageRepository,
    UserRepository,
)
from app.graph import run_bot_graph
from app.graph.intents import classify_intent, classify_intent_text
from app.services.clinic_knowledge import load_clinic_knowledge_if_empty


class FakeAdminBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send_message(self, *, chat_id: str, text: str):
        self.messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=777)


def test_intent_classifier_detects_core_flows():
    assert classify_intent_text("How much does cleaning cost?") == "admin_faq"
    assert classify_intent_text("I want to book an appointment") == "book_appointment"
    assert classify_intent_text("I own a clinic and want a demo") == "owner_sales"
    assert classify_intent_text("какие у меня есть записи?") == "view_appointments"
    assert classify_intent_text("Cancel my appointment") == "cancel_appointment"
    assert classify_intent_text("What medicine should I take?") == "medical_question"


async def test_hybrid_intent_classifier_uses_llm_router(monkeypatch):
    async def fake_llm_router(**kwargs):
        assert kwargs["text"] == "I was already scheduled, can you check it?"
        assert kwargs["language"] == "en"
        assert kwargs["current_flow"] == "booking"
        return "view_appointments"

    monkeypatch.setattr(
        "app.graph.intents._try_llm_classify_intent",
        fake_llm_router,
    )

    intent = await classify_intent(
        "I was already scheduled, can you check it?",
        language="en",
        current_flow="booking",
        current_state="collecting_patient",
    )

    assert intent == "view_appointments"


async def test_hybrid_intent_classifier_falls_back_to_keywords(monkeypatch):
    async def no_llm_router(**_kwargs):
        return None

    monkeypatch.setattr(
        "app.graph.intents._try_llm_classify_intent",
        no_llm_router,
    )

    intent = await classify_intent("I want to book an appointment", language="en")

    assert intent == "book_appointment"


async def test_graph_answers_faq_and_persists_execution_run(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    await load_clinic_knowledge_if_empty(session)
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1001,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1001,
    )
    message = await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=1,
        direction="in",
        message_type="text",
        language="en",
        text="How much does cleaning cost?",
        trace_id="graph-trace-1",
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-1",
        telegram_chat_id=1001,
        input_text="How much does cleaning cost?",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
        input_message_id=message.id,
    )

    execution_run = (
        await session.execute(
            select(ExecutionRun).where(ExecutionRun.trace_id == "graph-trace-1")
        )
    ).scalar_one()

    assert result.intent == "admin_faq"
    assert result.safety_status == "safe"
    assert "350,000 UZS" in result.final_response_text
    assert execution_run.status == "success"
    assert execution_run.input_message_id == message.id
    assert execution_run.intent == "admin_faq"


async def test_graph_starts_booking_controlled_flow(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1002,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1002,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-2",
        telegram_chat_id=1002,
        input_text="I want to book a cleaning",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
    )

    assert result.intent == "book_appointment"
    assert result.metadata["service_type"] == "cleaning"
    assert result.metadata["doctor_type"] == "therapist"
    assert result.metadata["missing_fields"] == ["patient_name", "phone"]
    assert "patient" in result.final_response_text.casefold()


async def test_graph_owner_sales_warm_lead_notifies_admin(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: Settings(admin_telegram_chat_id="-100123"),
    )
    admin_bot = FakeAdminBot()
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1020,
        telegram_username="owner",
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1020,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-owner-warm",
        telegram_chat_id=1020,
        input_text="I own Beverly Dental and want to see how you work",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
        admin_bot=admin_bot,
    )

    assert result.intent == "owner_sales"
    assert result.metadata["owner_clinic_name"] == "Beverly Dental"
    assert result.metadata["owner_sales_stage"] == "demo_intro"
    assert conversation.current_flow == "owner_sales"
    assert len(admin_bot.messages) >= 1
    assert "Sales lead" in admin_bot.messages[0]["text"]
    admin_text = "\n".join(message["text"] for message in admin_bot.messages)
    assert "Beverly Dental" in admin_text
    assert any(
        call["tool"] == "notify_sales" and call["stage"] == "warm"
        for call in result.metadata["tool_calls"]
    )


async def test_graph_owner_sales_hot_lead_persists_and_notifies(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: Settings(admin_telegram_chat_id="-100123"),
    )
    admin_bot = FakeAdminBot()
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1021,
        telegram_username="alisher",
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1021,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-owner-hot",
        telegram_chat_id=1021,
        input_text=(
            "I want to connect you. My name is Alisher, Beverly Dental, "
            "2 locations, phone +998 90 555 12 34"
        ),
        input_type="text",
        preferred_language="en",
        telegram_profile={},
        admin_bot=admin_bot,
    )

    escalations = (await session.execute(select(Escalation))).scalars().all()
    reasons = {item.reason for item in escalations}

    assert result.intent == "owner_sales"
    assert result.metadata["owner_sales_stage"] == "hot"
    assert result.metadata["owner_name"] == "Alisher"
    assert result.metadata["owner_clinic_name"] == "Beverly Dental"
    assert result.metadata["owner_locations"] == 2
    assert result.metadata["owner_phone"] == "+998905551234"
    assert result.metadata["admin_notification_sent"] is True
    assert "sales_hot" in reasons
    assert "Ivan" in result.final_response_text
    assert any(
        call["tool"] == "notify_sales" and call["stage"] == "hot"
        for call in result.metadata["tool_calls"]
    )


async def test_graph_lists_user_appointments_by_telegram_user(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1010,
        preferred_language="ru",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1010,
    )
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow="booking",
        current_state="collecting_patient",
        summary='{"service_type": "cleaning"}',
    )
    start_at = datetime.now(UTC) + timedelta(days=2)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="cleaning",
        doctor_type="therapist",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=60),
        patient_name="Михаил",
        primary_phone="+998901234567",
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-view-appointments",
        telegram_chat_id=1010,
        input_text="посмотрите какие у меня есть записи, я же записывался уже",
        input_type="text",
        preferred_language="ru",
        telegram_profile={},
    )

    assert result.intent == "view_appointments"
    assert result.metadata["active_appointments"][0]["id"] == appointment.id
    assert "Ваши активные записи" in result.final_response_text
    assert conversation.current_flow is None


async def test_graph_refuses_medical_advice(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)

    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1003,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1003,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-3",
        telegram_chat_id=1003,
        input_text="My tooth hurts, what medicine should I take?",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
    )

    assert result.intent == "medical_question"
    assert result.safety_status == "medical_advice"
    assert "cannot provide medical advice" in result.final_response_text


async def test_graph_creates_escalation_and_notifies_admin_for_emergency(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: Settings(admin_telegram_chat_id="-100123"),
    )
    admin_bot = FakeAdminBot()
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1004,
        telegram_username="ali",
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1004,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-4",
        telegram_chat_id=1004,
        input_text="Emergency, bleeding after extraction, phone +998 90 123 45 67",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
        admin_bot=admin_bot,
    )
    escalation = (
        await session.execute(
            select(Escalation).where(Escalation.id == result.metadata["escalation_id"])
        )
    ).scalar_one()

    assert result.intent == "emergency"
    assert result.should_escalate is True
    assert result.metadata["admin_notification_sent"] is True
    assert result.metadata["admin_message_id"] == 777
    assert result.metadata["missing_fields"] == []
    assert escalation.reason == "emergency"
    assert escalation.phone == "+998901234567"
    assert escalation.admin_chat_id == "-100123"
    assert escalation.admin_message_id == 777
    assert admin_bot.messages[0]["chat_id"] == "-100123"
    assert "Escalation required" in admin_bot.messages[0]["text"]


async def test_graph_escalates_unknown_faq_without_admin_bot(session, monkeypatch):
    async def no_openai_answer(**_kwargs):
        return None

    async def no_llm_router(**_kwargs):
        return None

    monkeypatch.setattr("app.services.faq._try_openai_answer", no_openai_answer)
    monkeypatch.setattr("app.graph.intents._try_llm_classify_intent", no_llm_router)

    await load_clinic_knowledge_if_empty(session)
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=1005,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=1005,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="graph-trace-5",
        telegram_chat_id=1005,
        input_text="Do you sell toothbrush subscriptions?",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
    )
    escalation = (
        await session.execute(
            select(Escalation).where(Escalation.id == result.metadata["escalation_id"])
        )
    ).scalar_one()

    assert result.intent == "admin_faq"
    assert result.should_escalate is True
    assert result.metadata["escalation_reason"] == "unknown"
    assert result.metadata["admin_notification_sent"] is False
    assert result.metadata["missing_fields"] == ["phone"]
    assert escalation.reason == "unknown"
