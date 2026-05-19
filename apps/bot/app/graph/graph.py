import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.db.repositories import ExecutionRunRepository
from app.graph.nodes import build_nodes, route_intent
from app.graph.state import BotState, GraphResult, InputType
from app.telegram.texts import Language

logger = logging.getLogger(__name__)


async def run_bot_graph(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    trace_id: str,
    telegram_chat_id: int,
    input_text: str,
    input_type: InputType,
    preferred_language: Language,
    telegram_profile: dict[str, Any],
    input_message_id: int | None = None,
    admin_bot: Any | None = None,
) -> GraphResult:
    graph_input = _initial_state(
        trace_id=trace_id,
        user=user,
        telegram_chat_id=telegram_chat_id,
        input_text=input_text,
        input_type=input_type,
        preferred_language=preferred_language,
        telegram_profile=telegram_profile,
    )
    runs = ExecutionRunRepository(session)
    await runs.start(
        trace_id=trace_id,
        user_id=user.id,
        conversation_id=conversation.id,
        input_message_id=input_message_id,
        graph_input=_serializable_state(graph_input),
    )
    try:
        graph = _compile_graph(
            session=session,
            user=user,
            conversation=conversation,
            admin_bot=admin_bot,
        )
        output: BotState = await graph.ainvoke(graph_input)
        await runs.finish(
            trace_id=trace_id,
            status="success",
            intent=output["intent"],
            graph_output=_serializable_state(output),
            tool_calls=output["tool_calls"],
        )
        return _result_from_state(output)
    except Exception as exc:
        logger.exception("bot_graph_failed", extra={"trace_id": trace_id})
        await runs.finish(trace_id=trace_id, status="failed", error=str(exc))
        raise


def _compile_graph(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    admin_bot: Any | None = None,
):
    nodes = build_nodes(
        session=session,
        user=user,
        conversation=conversation,
        admin_bot=admin_bot,
    )
    workflow = StateGraph(BotState)
    workflow.add_node("load_user_context", nodes["load_user_context"])
    workflow.add_node("classify_intent", nodes["classify_intent"])
    workflow.add_node("safety_guard", nodes["safety_guard"])
    workflow.add_node("admin_faq", nodes["admin_faq"])
    workflow.add_node("start_booking", nodes["start_booking"])
    workflow.add_node("continue_booking", nodes["continue_booking"])
    workflow.add_node("cancel_appointment", nodes["cancel_appointment"])
    workflow.add_node("reschedule_appointment", nodes["reschedule_appointment"])
    workflow.add_node("emergency_or_escalation", nodes["emergency_or_escalation"])
    workflow.add_node("fallback", nodes["fallback"])

    workflow.add_edge(START, "load_user_context")
    workflow.add_edge("load_user_context", "classify_intent")
    workflow.add_edge("classify_intent", "safety_guard")
    workflow.add_conditional_edges(
        "safety_guard",
        route_intent,
        {
            "admin_faq": "admin_faq",
            "start_booking": "start_booking",
            "cancel_appointment": "cancel_appointment",
            "reschedule_appointment": "reschedule_appointment",
            "emergency_or_escalation": "emergency_or_escalation",
            "fallback": "fallback",
        },
    )
    for terminal_node in (
        "admin_faq",
        "start_booking",
        "cancel_appointment",
        "reschedule_appointment",
        "emergency_or_escalation",
        "fallback",
    ):
        workflow.add_edge(terminal_node, END)
    return workflow.compile()


def _initial_state(
    *,
    trace_id: str,
    user: User,
    telegram_chat_id: int,
    input_text: str,
    input_type: InputType,
    preferred_language: Language,
    telegram_profile: dict[str, Any],
) -> BotState:
    return {
        "trace_id": trace_id,
        "telegram_user_id": user.telegram_user_id,
        "telegram_chat_id": telegram_chat_id,
        "input_text": input_text,
        "input_type": input_type,
        "preferred_language": preferred_language,
        "telegram_profile": telegram_profile,
        "user_profile": None,
        "conversation_summary": None,
        "intent": None,
        "safety_status": None,
        "service_type": None,
        "doctor_type": None,
        "requested_date": None,
        "requested_time_of_day": None,
        "proposed_slots": [],
        "selected_slot": None,
        "missing_fields": [],
        "final_response_text": None,
        "should_generate_voice": input_type == "voice",
        "should_escalate": False,
        "escalation_reason": None,
        "escalation_id": None,
        "escalation_phone": None,
        "admin_notification_sent": False,
        "admin_message_id": None,
        "tool_calls": [],
    }


def _result_from_state(state: BotState) -> GraphResult:
    return GraphResult(
        final_response_text=state["final_response_text"] or "",
        intent=state["intent"],
        safety_status=state["safety_status"],
        should_generate_voice=state["should_generate_voice"],
        should_escalate=state["should_escalate"],
        proposed_slots=state["proposed_slots"],
        metadata={
            "faq_answered": state.get("faq_answered"),
            "faq_source": state.get("faq_source"),
            "service_type": state["service_type"],
            "doctor_type": state["doctor_type"],
            "missing_fields": state["missing_fields"],
            "escalation_reason": state["escalation_reason"],
            "escalation_id": state["escalation_id"],
            "escalation_phone": state["escalation_phone"],
            "admin_notification_sent": state["admin_notification_sent"],
            "admin_message_id": state["admin_message_id"],
            "tool_calls": state["tool_calls"],
        },
    )


def _serializable_state(state: BotState) -> dict[str, Any]:
    return dict(state)
