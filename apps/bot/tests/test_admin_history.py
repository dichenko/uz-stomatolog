from datetime import UTC, datetime, time, timedelta

from app.admin.history_repository import (
    HistoryFilters,
    HistoryQuery,
    MessageHistoryRepository,
)
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    ExecutionRunRepository,
    MessageRepository,
    UserRepository,
)


async def test_history_page_returns_all_chat_messages(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=9001,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=9001,
    )
    messages = MessageRepository(session)
    created_at = datetime(2026, 5, 20, 10, 30, tzinfo=UTC)

    incoming = await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=1,
        direction="in",
        message_type="text",
        language=None,
        text="How much is cleaning?",
        trace_id="history-trace-1",
    )
    incoming.created_at = created_at
    outgoing = await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=2,
        direction="out",
        message_type="text",
        language="en",
        text="Cleaning costs 350,000 UZS.",
        raw_payload={"admin_notification_sent": False},
        trace_id="history-trace-1",
    )
    outgoing.created_at = created_at + timedelta(seconds=1)
    await ExecutionRunRepository(session).start(
        trace_id="history-trace-1",
        user_id=user.id,
        conversation_id=conversation.id,
        input_message_id=incoming.id,
        graph_input={"input_text": "How much is cleaning?"},
    )
    await ExecutionRunRepository(session).finish(
        trace_id="history-trace-1",
        status="success",
        intent="admin_faq",
        graph_output={},
        tool_calls=[],
    )

    page = await MessageHistoryRepository(session).fetch_page(
        HistoryQuery(sort_field="date", sort_dir="asc")
    )

    assert page.total == 2
    assert page.filtered == 2
    assert page.rows[0].date == "2026-05-20"
    assert page.rows[0].time == "10:30:00"
    assert page.rows[0].direction == "in"
    assert page.rows[0].tg_id == 9001
    assert page.rows[0].language == "en"
    assert page.rows[0].user_text == "How much is cleaning?"
    assert page.rows[0].llm_answer_text == ""
    assert page.rows[1].direction == "out"
    assert page.rows[1].user_text == ""
    assert page.rows[1].llm_answer_text == "Cleaning costs 350,000 UZS."

    time_filtered = await MessageHistoryRepository(session).fetch_page(
        HistoryQuery(
            sort_field="time",
            filters=HistoryFilters(
                time_from=time(10, 0),
                time_to=time(11, 0),
            ),
        )
    )
    assert time_filtered.filtered == 2


async def test_history_filters_by_message_type_and_agent_action(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=9002,
        preferred_language="uz",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=9002,
    )
    messages = MessageRepository(session)
    incoming = await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=10,
        direction="in",
        message_type="voice",
        language="uz",
        text="Qabulga yozing",
        trace_id="history-trace-2",
    )
    await messages.save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=11,
        direction="out",
        message_type="text",
        language="uz",
        text="Qabul tasdiqlandi.",
        raw_payload={
            "booking_confirmed": True,
            "calendar_event_id": "calendar-1",
            "admin_notification_sent": True,
        },
        trace_id="history-trace-2",
    )
    start_at = datetime.now(UTC) + timedelta(days=3)
    await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=30),
        patient_name="Ali",
        primary_phone="+998901234567",
        created_trace_id="history-trace-2",
    )
    await ExecutionRunRepository(session).start(
        trace_id="history-trace-2",
        user_id=user.id,
        conversation_id=conversation.id,
        input_message_id=incoming.id,
        graph_input={"input_text": "Qabulga yozing"},
    )
    await ExecutionRunRepository(session).finish(
        trace_id="history-trace-2",
        status="success",
        intent="book_appointment",
        graph_output={"admin_notification_sent": True},
        tool_calls=[],
    )

    page = await MessageHistoryRepository(session).fetch_page(
        HistoryQuery(
            filters=HistoryFilters(
                message_type="voice",
                agent_action="Создана запись",
            )
        )
    )

    assert page.total == 2
    assert page.filtered == 1
    assert page.rows[0].message_type == "voice"
    assert page.rows[0].direction == "in"
    assert "Создана запись" in page.rows[0].agent_action
    assert "Оповещение группы админов" in page.rows[0].agent_action

    outgoing_page = await MessageHistoryRepository(session).fetch_page(
        HistoryQuery(
            filters=HistoryFilters(
                direction="out",
                agent_action="Создана запись",
            )
        )
    )

    assert outgoing_page.total == 2
    assert outgoing_page.filtered == 1
    assert outgoing_page.rows[0].direction == "out"
    assert "Создана запись в календаре" in outgoing_page.rows[0].agent_action
