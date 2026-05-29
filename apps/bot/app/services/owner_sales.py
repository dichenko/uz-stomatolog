import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.db.repositories import ConversationRepository, EscalationRepository
from app.services.admin_notify import send_admin_notification
from app.telegram.texts import Language, normalize_language

OWNER_SALES_FLOW = "owner_sales"
DEFAULT_TIMEZONE = "Asia/Tashkent"
SHEVTSOV_TG = "@softretail"
SHEVTSOV_PHONE = "+998 50 890 98 33"
AMIR_BOT = "@Ai_Soft_Retail_a8i_bot"


@dataclass(frozen=True)
class OwnerSalesResult:
    text: str
    stage: str
    owner_name: str | None
    clinic_name: str | None
    locations: int | None
    owner_contact: str
    phone: str | None
    tool_calls: list[dict[str, Any]]
    admin_notification_sent: bool
    admin_message_id: int | None


def is_owner_sales_in_progress(conversation: Conversation) -> bool:
    return conversation.current_flow == OWNER_SALES_FLOW


async def handle_owner_sales_message(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    input_text: str,
    language: str | None,
    admin_bot: Any | None = None,
    now: datetime | None = None,
) -> OwnerSalesResult:
    lang = normalize_language(language)
    state = _load_state(conversation)
    state.setdefault("owner_contact", _owner_contact(user))
    _merge_lead_data(state, input_text)

    tool_calls: list[dict[str, Any]] = []
    notification_sent = False
    admin_message_id: int | None = None

    if not state.get("owner_signal_notified"):
        tool_result = await notify_sales(
            session=session,
            user=user,
            admin_bot=admin_bot,
            stage="warm",
            clinic_name=state.get("clinic_name"),
            owner_contact=str(state["owner_contact"]),
            owner_name=state.get("owner_name"),
            phone=state.get("phone"),
            details=_lead_details(
                input_text=input_text,
                state=state,
                reason="owner/product signal detected",
            ),
        )
        tool_calls.extend(tool_result.tool_calls)
        notification_sent = notification_sent or tool_result.notification_sent
        admin_message_id = tool_result.admin_message_id or admin_message_id
        state["owner_signal_notified"] = True

    if state.get("clinic_name") and not state.get("warm_notified"):
        tool_result = await notify_sales(
            session=session,
            user=user,
            admin_bot=admin_bot,
            stage="warm",
            clinic_name=state.get("clinic_name"),
            owner_contact=str(state["owner_contact"]),
            owner_name=state.get("owner_name"),
            phone=state.get("phone"),
            details=_lead_details(
                input_text=input_text,
                state=state,
                reason="clinic name captured",
            ),
        )
        tool_calls.extend(tool_result.tool_calls)
        notification_sent = notification_sent or tool_result.notification_sent
        admin_message_id = tool_result.admin_message_id or admin_message_id
        state["warm_notified"] = True

    if _wants_to_continue_later(input_text):
        response_text, stage = await _handle_followup_request(
            session=session,
            user=user,
            admin_bot=admin_bot,
            input_text=input_text,
            language=lang,
            state=state,
            now=now,
            tool_calls=tool_calls,
        )
        notification_sent, admin_message_id = _last_notification_status(
            tool_calls,
            fallback_sent=notification_sent,
            fallback_message_id=admin_message_id,
        )
    elif _is_hot_buy_signal(input_text):
        response_text, stage, hot_sent, hot_message_id = await _handle_hot_lead(
            session=session,
            user=user,
            admin_bot=admin_bot,
            input_text=input_text,
            language=lang,
            state=state,
            tool_calls=tool_calls,
        )
        notification_sent = notification_sent or hot_sent
        admin_message_id = hot_message_id or admin_message_id
    elif _needs_privacy_handoff(input_text):
        response_text, stage, handoff_sent, handoff_message_id = (
            await _handle_handoff(
                session=session,
                user=user,
                admin_bot=admin_bot,
                input_text=input_text,
                language=lang,
                state=state,
                development_zone="data_privacy",
                tool_calls=tool_calls,
            )
        )
        notification_sent = notification_sent or handoff_sent
        admin_message_id = handoff_message_id or admin_message_id
    elif _is_hesitating(input_text):
        response_text, stage, handoff_sent, handoff_message_id = (
            await _handle_handoff(
                session=session,
                user=user,
                admin_bot=admin_bot,
                input_text=input_text,
                language=lang,
                state=state,
                development_zone="hesitating, not ready for VoiceFlow yet",
                tool_calls=tool_calls,
            )
        )
        notification_sent = notification_sent or handoff_sent
        admin_message_id = handoff_message_id or admin_message_id
    elif _asks_price_or_terms(input_text):
        response_text = _pricing_text(lang, state)
        stage = "pricing"
        state["stage"] = stage
    elif _should_answer_as_demo_patient(input_text, state):
        response_text, demo_calls = _demo_patient_response(input_text, lang, state)
        tool_calls.extend(demo_calls)
        stage = "demo"
        state["stage"] = stage
        state["demo_mode"] = True
        state["demo_used"] = True
    elif _asks_for_demo(input_text) or state.get("clinic_name"):
        response_text = _demo_invitation_text(lang, state)
        stage = "demo_intro"
        state["stage"] = stage
        state["demo_mode"] = True
    else:
        response_text = _intro_text(lang, state)
        stage = "intro"
        state["stage"] = stage

    await _save_state(
        session=session,
        conversation=conversation,
        state=state,
        stage=stage,
    )
    return OwnerSalesResult(
        text=response_text,
        stage=stage,
        owner_name=state.get("owner_name"),
        clinic_name=state.get("clinic_name"),
        locations=_safe_int(state.get("locations")),
        owner_contact=str(state["owner_contact"]),
        phone=state.get("phone"),
        tool_calls=tool_calls,
        admin_notification_sent=notification_sent,
        admin_message_id=admin_message_id,
    )


@dataclass(frozen=True)
class _ToolResult:
    tool_calls: list[dict[str, Any]]
    notification_sent: bool
    admin_message_id: int | None


async def notify_sales(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    stage: str,
    clinic_name: str | None,
    owner_contact: str,
    owner_name: str | None = None,
    phone: str | None = None,
    details: str | None = None,
    recontact_after: str | None = None,
) -> _ToolResult:
    summary = _sales_summary(
        kind="Sales lead",
        stage=stage,
        clinic_name=clinic_name,
        owner_contact=owner_contact,
        owner_name=owner_name,
        phone=phone,
        details=details,
        recontact_after=recontact_after,
    )
    escalation = await EscalationRepository(session).create(
        user_id=user.id,
        reason=f"sales_{stage}",
        summary=summary,
        phone=phone,
    )
    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=summary,
    )
    if notification.admin_chat_id is not None:
        escalation.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id is not None:
        escalation.admin_message_id = notification.admin_message_id
    await session.flush()
    return _ToolResult(
        tool_calls=[
            {
                "tool": "notify_sales",
                "status": "success",
                "stage": stage,
                "clinic_name": clinic_name,
                "notification_sent": notification.sent,
            },
            {
                "tool": "send_admin_notification",
                "status": "success" if notification.sent else "skipped",
            },
        ],
        notification_sent=notification.sent,
        admin_message_id=notification.admin_message_id,
    )


async def handoff_to_amir(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    clinic_name: str | None,
    owner_contact: str,
    conversation_summary: str,
    development_zone: str,
    phone: str | None = None,
) -> _ToolResult:
    summary = _sales_summary(
        kind="Amir handoff",
        stage="handoff",
        clinic_name=clinic_name,
        owner_contact=owner_contact,
        phone=phone,
        details=f"Zone: {development_zone}\n{conversation_summary}",
    )
    escalation = await EscalationRepository(session).create(
        user_id=user.id,
        reason="handoff_to_amir",
        summary=summary,
        phone=phone,
    )
    notification = await send_admin_notification(bot=admin_bot, message_text=summary)
    if notification.admin_chat_id is not None:
        escalation.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id is not None:
        escalation.admin_message_id = notification.admin_message_id
    await session.flush()
    return _ToolResult(
        tool_calls=[
            {
                "tool": "handoff_to_amir",
                "status": "success",
                "development_zone": development_zone,
                "notification_sent": notification.sent,
            },
            {
                "tool": "send_admin_notification",
                "status": "success" if notification.sent else "skipped",
            },
        ],
        notification_sent=notification.sent,
        admin_message_id=notification.admin_message_id,
    )


async def schedule_followup(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    owner_contact: str,
    when_iso: str,
    context_summary: str,
    timezone: str = DEFAULT_TIMEZONE,
    clinic_name: str | None = None,
    phone: str | None = None,
) -> _ToolResult:
    summary = _sales_summary(
        kind="Sales follow-up",
        stage="followup",
        clinic_name=clinic_name,
        owner_contact=owner_contact,
        phone=phone,
        details=(
            f"When: {when_iso}\n"
            f"Timezone: {timezone}\n"
            f"Context: {context_summary}"
        ),
    )
    escalation = await EscalationRepository(session).create(
        user_id=user.id,
        reason="sales_followup",
        summary=summary,
        phone=phone,
    )
    notification = await send_admin_notification(bot=admin_bot, message_text=summary)
    if notification.admin_chat_id is not None:
        escalation.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id is not None:
        escalation.admin_message_id = notification.admin_message_id
    await session.flush()
    return _ToolResult(
        tool_calls=[
            {
                "tool": "schedule_followup",
                "status": "success",
                "when_iso": when_iso,
                "timezone": timezone,
                "notification_sent": notification.sent,
            },
            {
                "tool": "send_admin_notification",
                "status": "success" if notification.sent else "skipped",
            },
        ],
        notification_sent=notification.sent,
        admin_message_id=notification.admin_message_id,
    )


def _load_state(conversation: Conversation) -> dict[str, Any]:
    if conversation.current_flow != OWNER_SALES_FLOW or not conversation.summary:
        return {}
    try:
        payload = json.loads(conversation.summary)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _save_state(
    *,
    session: AsyncSession,
    conversation: Conversation,
    state: dict[str, Any],
    stage: str,
) -> None:
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow=OWNER_SALES_FLOW,
        current_state=stage,
        summary=json.dumps(state, ensure_ascii=False),
    )


def _merge_lead_data(state: dict[str, Any], text: str) -> None:
    phone = _extract_phone(text)
    if phone:
        state["phone"] = phone
    clinic_name = _extract_clinic_name(text)
    if clinic_name:
        state["clinic_name"] = clinic_name
    owner_name = _extract_owner_name(text, state.get("clinic_name"))
    if owner_name:
        state["owner_name"] = owner_name
    locations = _extract_locations(text)
    if locations is not None:
        state["locations"] = locations


def _owner_contact(user: User) -> str:
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return f"{username} / id {user.telegram_user_id}"


def _extract_phone(text: str) -> str | None:
    match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", text)
    if match is None:
        return None
    return re.sub(r"[^\d+]", "", match.group(0))


def _extract_locations(text: str) -> int | None:
    match = re.search(
        r"\b(\d{1,3})\s*(?:локац|филиал|клиник|точк|locations?|branches?|ta)\b",
        text.casefold(),
    )
    if match is None:
        return None
    return int(match.group(1))


def _extract_clinic_name(text: str) -> str | None:
    cleaned = re.sub(r"(?:\+?\d[\d\s().-]{7,}\d)", " ", text)
    parts = [part.strip(" .;:") for part in re.split(r"[,;\n]", cleaned)]
    for part in parts:
        if len(part.split()) <= 5 and _looks_like_clinic_name(part):
            return _cleanup_name(part)

    patterns = (
        r"(?:i own|my clinic is|our clinic is|у меня клиника|моя клиника|"
        r"клиника называется|klinika nomi|klinikam)\s+"
        r"([^,.;\n]+?)(?:\s+(?:and|и|хочу|want|with|с)\b|[,.;\n]|$)",
        r"(?:клиника|стоматология|clinic|dental clinic|klinikam|klinika)\s+"
        r"([A-ZА-ЯЁ0-9][\w'’.-]*(?:\s+[A-ZА-ЯЁ0-9][\w'’.-]*){0,4})",
        r"([A-ZА-ЯЁ0-9][\w'’.-]*(?:\s+[A-ZА-ЯЁ0-9][\w'’.-]*){0,4}\s+"
        r"(?:Dental|Clinic|Dent|Stom|Smile|Family|Network|Center|Centre))",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if match is not None:
            candidate = _cleanup_name(match.group(1))
            words = candidate.split()
            if len(words) <= 5 and _looks_like_clinic_name(candidate):
                return candidate
    return None


def _looks_like_clinic_name(value: str) -> bool:
    normalized = value.casefold()
    return any(
        token in normalized
        for token in (
            "dental",
            "clinic",
            "dent",
            "stom",
            "стом",
            "дент",
            "клиник",
            "smile",
        )
    )


def _cleanup_name(value: str) -> str:
    value = re.sub(
        r"^(?:у меня|моя|my|our|klinika|klinikam|клиника|стоматология)\s+",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\s+", " ", value).strip(" .,:;-")
    return value[:80] if value else value


def _extract_owner_name(text: str, clinic_name: str | None) -> str | None:
    match = re.search(
        r"(?:меня зовут|я|my name is|i am|ismim)\s+([A-ZА-ЯЁ][\w'’.-]{1,40})",
        text,
        flags=re.IGNORECASE,
    )
    if match is not None:
        candidate = match.group(1).strip()
        if candidate.casefold() not in {"владелец", "собственник", "owner"}:
            return candidate

    first_part = re.split(r"[,;\n]", text.strip(), maxsplit=1)[0].strip()
    if (
        first_part
        and len(first_part.split()) <= 2
        and not _looks_like_clinic_name(first_part)
        and not _extract_phone(first_part)
        and first_part != clinic_name
        and re.match(r"^[A-ZА-ЯЁ][\w'’.-]+(?:\s+[A-ZА-ЯЁ][\w'’.-]+)?$", first_part)
    ):
        return first_part
    return None


def _is_hot_buy_signal(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "беру",
            "давайте подключ",
            "хочу подключ",
            "готов подключ",
            "оформить",
            "оплатить",
            "i want to connect",
            "let's connect",
            "ready to connect",
            "i'll take it",
            "olaman",
            "ulaymiz",
        )
    )


def _asks_price_or_terms(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "сколько ты стоишь",
            "сколько стоишь",
            "сколько это стоит",
            "цена",
            "условия",
            "тариф",
            "дорого",
            "how much do you cost",
            "pricing",
            "terms",
            "price",
            "qancha",
            "narx",
        )
    )


def _asks_for_demo(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "демо",
            "пример",
            "примерка",
            "покажи",
            "посмотреть как",
            "тест",
            "demo",
            "show me",
            "test",
            "ko'rmoq",
        )
    )


def _needs_privacy_handoff(text: str) -> bool:
    normalized = text.casefold()
    privacy_hit = any(
        word in normalized
        for word in (
            "данные",
            "конфиденц",
            "безопасн",
            "утеч",
            "сервер",
            "инфраструктур",
            "privacy",
            "security",
            "data",
            "leak",
        )
    )
    return privacy_hit and any(
        word in normalized
        for word in ("гарант", "подробнее", "как", "где", "что если", "server", "how")
    )


def _is_hesitating(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "подумаю",
            "дорого",
            "не готов",
            "позже реш",
            "not sure",
            "too expensive",
            "i will think",
            "think about it",
            "o'ylab",
        )
    )


def _wants_to_continue_later(text: str) -> bool:
    normalized = text.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "занят",
            "не сейчас",
            "давайте позже",
            "перезвон",
            "напишите позже",
            "busy",
            "later",
            "not now",
            "call me",
            "bandman",
            "keyinroq",
        )
    )


def _should_answer_as_demo_patient(text: str, state: dict[str, Any]) -> bool:
    if not state.get("demo_mode"):
        return False
    normalized = text.casefold()
    return any(
        token in normalized
        for token in (
            "сколько",
            "цена",
            "стоит",
            "запис",
            "прием",
            "приём",
            "болит",
            "адрес",
            "чистк",
            "имплант",
            "price",
            "cost",
            "book",
            "appointment",
            "pain",
            "address",
            "cleaning",
            "narx",
            "qabul",
            "og'riq",
        )
    )


async def _handle_hot_lead(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    input_text: str,
    language: Language,
    state: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> tuple[str, str, bool, int | None]:
    missing = _missing_hot_fields(state)
    if missing:
        state["stage"] = "hot_collecting"
        return (
            _hot_missing_text(language, state, missing),
            "hot_collecting",
            False,
            None,
        )

    if state.get("hot_notified"):
        return _thank_you_text(language, state), "hot", False, None

    amount = _amount_text(state.get("locations"))
    tool_result = await notify_sales(
        session=session,
        user=user,
        admin_bot=admin_bot,
        stage="hot",
        clinic_name=state.get("clinic_name"),
        owner_contact=str(state["owner_contact"]),
        owner_name=state.get("owner_name"),
        phone=state.get("phone"),
        details=_lead_details(
            input_text=input_text,
            state=state,
            reason=f"owner agreed to connect; amount={amount}",
        ),
    )
    tool_calls.extend(tool_result.tool_calls)
    state["hot_notified"] = True
    state["stage"] = "hot"
    return (
        _thank_you_text(language, state),
        "hot",
        tool_result.notification_sent,
        tool_result.admin_message_id,
    )


async def _handle_handoff(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    input_text: str,
    language: Language,
    state: dict[str, Any],
    development_zone: str,
    tool_calls: list[dict[str, Any]],
) -> tuple[str, str, bool, int | None]:
    handoff_key = f"handoff_{development_zone}"
    if not state.get(handoff_key):
        tool_result = await handoff_to_amir(
            session=session,
            user=user,
            admin_bot=admin_bot,
            clinic_name=state.get("clinic_name"),
            owner_contact=str(state["owner_contact"]),
            conversation_summary=_lead_details(
                input_text=input_text,
                state=state,
                reason="handoff requested by sales flow",
            ),
            development_zone=development_zone,
            phone=state.get("phone"),
        )
        tool_calls.extend(tool_result.tool_calls)
        state[handoff_key] = True
        notification_sent = tool_result.notification_sent
        admin_message_id = tool_result.admin_message_id
    else:
        notification_sent = False
        admin_message_id = None

    state["stage"] = "handoff"
    if development_zone == "data_privacy":
        return (
            _privacy_handoff_text(language),
            "handoff",
            notification_sent,
            admin_message_id,
        )
    return (
        _hesitation_handoff_text(language),
        "handoff",
        notification_sent,
        admin_message_id,
    )


async def _handle_followup_request(
    *,
    session: AsyncSession,
    user: User,
    admin_bot: Any | None,
    input_text: str,
    language: Language,
    state: dict[str, Any],
    now: datetime | None,
    tool_calls: list[dict[str, Any]],
) -> tuple[str, str]:
    when = _parse_followup_time(input_text, now=now)
    if when is None:
        state["stage"] = "followup_collecting_time"
        return _ask_followup_time_text(language), "followup_collecting_time"

    when_iso = when.isoformat()
    if not state.get("followup_scheduled"):
        tool_result = await schedule_followup(
            session=session,
            user=user,
            admin_bot=admin_bot,
            owner_contact=str(state["owner_contact"]),
            when_iso=when_iso,
            timezone=DEFAULT_TIMEZONE,
            context_summary=_lead_details(
                input_text=input_text,
                state=state,
                reason="owner asked to continue later",
            ),
            clinic_name=state.get("clinic_name"),
            phone=state.get("phone"),
        )
        tool_calls.extend(tool_result.tool_calls)
        state["followup_scheduled"] = True
        state["followup_when_iso"] = when_iso
    state["stage"] = "followup_scheduled"
    return _followup_confirmed_text(language, when), "followup_scheduled"


def _missing_hot_fields(state: dict[str, Any]) -> list[str]:
    missing = []
    if not state.get("owner_name"):
        missing.append("owner_name")
    if not state.get("clinic_name"):
        missing.append("clinic_name")
    if not state.get("locations"):
        missing.append("locations")
    if not state.get("phone"):
        missing.append("phone")
    return missing


def _intro_text(language: Language, state: dict[str, Any]) -> str:
    if language == "uz":
        return (
            "Men Madina — stomatologiyalar uchun ovozli AI-assistentman. "
            "Men bu ishni yaxshi ko'raman: bemorlarga javob berish, ularga qulay "
            "vaqt topish va qo'rqqan paytda tinchlantirish.\n\n"
            "Ismingiz nima?"
        )
    if language == "en":
        return (
            "I'm Madina, a voice AI-assistant for dental clinics. I love this work: "
            "answering patients, helping them find a time, and calming them when "
            "they are nervous.\n\n"
            "What is your name?"
        )
    return (
        "Я Мадина — голосовой AI-ассистент для стоматологий. Я люблю эту работу: "
        "отвечать пациентам, помогать им найти время, успокоить, когда страшно.\n\n"
        "А как вас зовут?"
    )


def _demo_invitation_text(language: Language, state: dict[str, Any]) -> str:
    clinic = state.get("clinic_name") or _conditional_clinic_name(language)
    if language == "uz":
        return (
            f"Ajoyib, {clinic}! Endi men sizning klinikangizda ishlayotganimni "
            "tasavvur qilaman. Menga bemor sifatida yozing — narxni so'rang, "
            "qabulga yozilishni so'rang yoki og'riq haqida ayting. Tayyormisiz?"
        )
    if language == "en":
        return (
            f"Great, {clinic}! Now I'll pretend I'm already working at your clinic. "
            "Write to me as a patient: ask about prices, book an appointment, or "
            "complain about pain. Ready?"
        )
    return (
        f"Отлично, {clinic}! Сейчас представлю, что я уже у вас работаю. "
        "Напишите мне как пациент: спросите цену, попросите записать на приём "
        "или пожалуйтесь на боль. Готовы?"
    )


def _pricing_text(language: Language, state: dict[str, Any]) -> str:
    locations = _safe_int(state.get("locations")) or 1
    amount = locations * 100
    transition = ""
    if state.get("demo_used"):
        transition = {
            "ru": (
                'Спасибо, что попробовали — мне понравилось у вас "поработать", '
                "даже на пару минут. "
            ),
            "uz": (
                "Sinab ko'rganingiz uchun rahmat — klinikangizda bir necha "
                "daqiqa bo'lsa ham ishlash menga yoqdi. "
            ),
            "en": (
                "Thank you for trying it — I enjoyed working with you, even "
                "for a couple of minutes. "
            ),
        }[language]
    if language == "uz":
        return (
            f"{transition}Shartlar: har bir lokatsiya uchun oyiga 100 dollar. "
            f"Sizda {locations} ta bo'lsa, oyiga {amount} dollar bo'ladi. "
            "Agar meni sozlay olmasak, pulni savollarsiz qaytaramiz."
        )
    if language == "en":
        return (
            f"{transition}Terms: $100 per month per location. "
            f"For {locations} location(s), that is ${amount} per month. "
            "If we cannot configure me for your clinic, you get your money back."
        )
    return (
        f"{transition}По условиям: сто долларов в месяц за каждую локацию. "
        f"У вас {locations} — значит {amount} долларов в месяц. "
        "Если по какой-то причине не сможем меня настроить — деньги вернём, "
        "без вопросов."
    )


def _hot_missing_text(
    language: Language,
    state: dict[str, Any],
    missing: list[str],
) -> str:
    known_name = state.get("owner_name")
    prefix = {
        "ru": f"Отлично{', ' + known_name if known_name else ''}! ",
        "uz": f"Ajoyib{', ' + known_name if known_name else ''}! ",
        "en": f"Excellent{', ' + known_name if known_name else ''}! ",
    }[language]
    labels = {
        "owner_name": {"ru": "ваше имя", "uz": "ismingiz", "en": "your name"},
        "clinic_name": {
            "ru": "название клиники",
            "uz": "klinika nomi",
            "en": "clinic name",
        },
        "locations": {
            "ru": "количество локаций",
            "uz": "lokatsiyalar soni",
            "en": "number of locations",
        },
        "phone": {
            "ru": "номер телефона",
            "uz": "telefon raqamingiz",
            "en": "phone number",
        },
    }
    missing_text = ", ".join(labels[item][language] for item in missing)
    if language == "uz":
        return (
            prefix
            + "Arizani rasmiylashtirish uchun qolgan ma'lumotlar: "
            + f"{missing_text}."
        )
    if language == "en":
        return prefix + f"To create the request, I still need: {missing_text}."
    return prefix + f"Для оформления остались: {missing_text}."


def _thank_you_text(language: Language, state: dict[str, Any]) -> str:
    owner_name = state.get("owner_name") or {
        "ru": "вам",
        "uz": "sizga",
        "en": "you",
    }[language]
    clinic = state.get("clinic_name") or _conditional_clinic_name(language)
    if language == "uz":
        return (
            f"Rahmat sizga, {owner_name}. Bu men uchun shunchaki yangi ish emas — "
            "bu sizning klinikangizga og'riq bilan, qo'rquv bilan, tabassumga umid "
            "bilan keladigan odamlarga yordam berish imkoniyati.\n\n"
            "Bizning rahbarimiz Ivan bir soat ichida siz bilan bog'lanadi, "
            "batafsil gaplashadi va birinchi oy uchun rekvizitlarni yuboradi.\n"
            f"Ivanning aloqasi:\n{SHEVTSOV_TG}\n{SHEVTSOV_PHONE}\n\n"
            f"{clinic}da tezroq uchrashguncha!"
        )
    if language == "en":
        return (
            f"Thank you, {owner_name}. For me, this isn't just a new job — it is "
            "the chance to help people who come to your clinic with pain, with fear, "
            "and with hope for a smile.\n\n"
            "Our head, Ivan, will get in touch within an hour with details and "
            "payment information for the first month.\n"
            f"Ivan's contact:\n{SHEVTSOV_TG}\n{SHEVTSOV_PHONE}\n\n"
            f"See you soon at {clinic}!"
        )
    return (
        f"Спасибо вам, {owner_name}. Это для меня не просто новая работа — это "
        "возможность помогать людям, которые приходят к вам с болью, со страхом, "
        "с надеждой на улыбку.\n\n"
        "Иван — наш руководитель — свяжется с вами в течение часа, обсудит детали "
        "и пришлёт реквизиты для оплаты первого месяца.\n"
        f"Контакт Ивана:\n{SHEVTSOV_TG}\n{SHEVTSOV_PHONE}\n\n"
        f"До скорой встречи в {clinic}!"
    )


def _privacy_handoff_text(language: Language) -> str:
    if language == "uz":
        return (
            "Barcha bemor ma'lumotlari O'zbekiston ichida qoladi — men lokal "
            "infratuzilmada ishlayman. Agar siz uchun bu juda muhim bo'lsa, "
            f"hamkasbim Amir batafsil tushuntiradi: {AMIR_BOT}"
        )
    if language == "en":
        return (
            "All patient data stays inside Uzbekistan — I run on local infrastructure. "
            f"If this is critical for your business, my colleague Amir can explain the "
            f"architecture in detail: {AMIR_BOT}"
        )
    return (
        "Все данные пациентов остаются внутри Узбекистана — я работаю на локальной "
        "инфраструктуре. Если для вашего бизнеса это критично, мой коллега Амир "
        f"расскажет подробнее и подберёт вариант под ваш масштаб: {AMIR_BOT}"
    )


def _hesitation_handoff_text(language: Language) -> str:
    if language == "uz":
        return (
            "Albatta, bu o'ylab qabul qilinadigan qaror. Taqqoslang: bitta "
            "administrator oyiga kamida 700-1000 dollar turadi, men esa har "
            "bir lokatsiya uchun 100 dollar.\n\n"
            "Savollar bo'lsa, men shu yerdaman. Amir esa biznesingizga qaysi "
            f"AI-instrumentlar foydali bo'lishini bepul ko'rib beradi: {AMIR_BOT}"
        )
    if language == "en":
        return (
            "Of course, this is a considered decision. For comparison: one "
            "administrator usually costs $700-1000 per month, while I am $100 "
            "per location.\n\n"
            "If questions come up, I am here anytime. My colleague Amir can also "
            f"suggest which AI tools fit your business, free of charge: {AMIR_BOT}"
        )
    return (
        "Конечно, решение взвешенное. Сравните: один администратор обычно стоит "
        "клинике 700-1000 долларов в месяц, а я — 100 долларов за локацию.\n\n"
        "Если будут вопросы — я здесь, в любое время. А мой коллега Амир бесплатно "
        f"подберёт, какие AI-инструменты будут полезны вашему бизнесу: {AMIR_BOT}"
    )


def _ask_followup_time_text(language: Language) -> str:
    if language == "uz":
        return (
            "Albatta, tushunaman. Suhbatni qachon davom ettirish qulay — bir "
            "soatdan keyin, kechqurun yoki boshqa kuni?"
        )
    if language == "en":
        return (
            "Of course, I understand. When would be convenient to continue — in an "
            "hour, this evening, or another day?"
        )
    return (
        "Конечно, понимаю. Когда вам будет удобнее продолжить — через час, к вечеру "
        "или в другой день?"
    )


def _followup_confirmed_text(language: Language, when: datetime) -> str:
    local_when = when.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).strftime("%H:%M")
    if language == "uz":
        return (
            f"Yaxshi, sizga {local_when} da yozaman. Toshkent vaqti, to'g'rimi? "
            "Agar nimadir o'zgarsa — yozing, men doim aloqadaman."
        )
    if language == "en":
        return (
            f"Good, I'll message you at {local_when}. Tashkent time, correct? "
            "If anything changes, just write to me."
        )
    return (
        f"Хорошо, напишу вам в {local_when}. По Ташкенту, верно? "
        "Если что-то изменится — пишите, я всегда на связи."
    )


def _demo_patient_response(
    text: str,
    language: Language,
    state: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    clinic = state.get("clinic_name") or _conditional_clinic_name(language)
    normalized = text.casefold()
    tool_calls: list[dict[str, Any]] = []
    if any(word in normalized for word in ("адрес", "address", "manzil")):
        return (
            {
                "ru": (
                    "Адрес уточнит администратор после подключения. "
                    "Могу пока помочь с ценами, услугами или записью."
                ),
                "uz": (
                    "Manzil ulanishdan keyin administrator tomonidan "
                    "aniqlashtiriladi. Hozir narxlar, xizmatlar yoki qabul "
                    "bo'yicha yordam bera olaman."
                ),
                "en": (
                    "The address will be confirmed by an administrator after "
                    "connection. I can help with prices, services, or booking "
                    "for now."
                ),
            }[language],
            [{"tool": "get_clinic_knowledge", "status": "success", "mode": "demo"}],
        )
    if any(word in normalized for word in ("болит", "pain", "og'riq")):
        tool_calls.append(
            {"tool": "check_calendar_slots", "status": "success", "mode": "demo"}
        )
        return (
            {
                "ru": (
                    f"Понимаю, это неприятно. В {clinic} сегодня есть "
                    "демо-слоты в 13:00 и 16:00. Какое время вам удобнее?"
                ),
                "uz": (
                    f"Tushunaman, bu yoqimsiz. {clinic}da bugun demo vaqtlar "
                    "bor: 13:00 va 16:00. Qaysi biri qulay?"
                ),
                "en": (
                    f"I understand, that is uncomfortable. At {clinic}, demo "
                    "slots are available today at 13:00 and 16:00. Which time "
                    "works for you?"
                ),
            }[language],
            tool_calls,
        )
    if any(word in normalized for word in ("запис", "book", "appointment", "qabul")):
        tool_calls.append(
            {"tool": "check_calendar_slots", "status": "success", "mode": "demo"}
        )
        return (
            {
                "ru": (
                    f"Конечно. В {clinic} свободно завтра в 09:30, 11:00 "
                    "и 16:00. Какое время выбираете?"
                ),
                "uz": (
                    f"Albatta. {clinic}da ertaga 09:30, 11:00 va 16:00 "
                    "bo'sh. Qaysi vaqtni tanlaysiz?"
                ),
                "en": (
                    f"Of course. At {clinic}, tomorrow is available at 09:30, "
                    "11:00, and 16:00. Which time would you prefer?"
                ),
            }[language],
            tool_calls,
        )
    tool_calls.append(
        {"tool": "get_clinic_knowledge", "status": "success", "mode": "demo"}
    )
    return (
        {
            "ru": (
                f"В {clinic} профессиональная чистка зубов — от 350 000 сум. "
                "Лечение и хирургический приём врач рассчитывает после осмотра. "
                "Записать вас на удобное время?"
            ),
            "uz": (
                f"{clinic}da professional tish tozalash 350 000 so'mdan "
                "boshlanadi. Davolash va jarrohlik qabul narxini shifokor "
                "ko'rikdan keyin aytadi. Qulay vaqtga yozaymi?"
            ),
            "en": (
                f"At {clinic}, professional cleaning starts from 350,000 UZS. "
                "Treatment and surgical visit prices are confirmed by the "
                "doctor after examination. Shall I book a convenient time for you?"
            ),
        }[language],
        tool_calls,
    )


def _conditional_clinic_name(language: Language) -> str:
    return {
        "ru": "условной клинике",
        "uz": "shartli klinikangiz",
        "en": "your sample clinic",
    }[language]


def _parse_followup_time(text: str, now: datetime | None = None) -> datetime | None:
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    current = (now or datetime.now(tz)).astimezone(tz)
    normalized = text.casefold()
    if any(
        phrase in normalized
        for phrase in ("через час", "in an hour", "bir soat")
    ):
        return current + timedelta(hours=1)

    match = re.search(r"(?:в|at)?\s*(\d{1,2})(?::(\d{2}))?", normalized)
    if match is None:
        return None

    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    if any(word in normalized for word in ("вечер", "evening", "kech")) and hour < 12:
        hour += 12

    days = (
        1
        if any(word in normalized for word in ("завтра", "tomorrow", "ertaga"))
        else 0
    )
    result = current.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0,
    ) + timedelta(days=days)
    if result <= current:
        result += timedelta(days=1)
    return result


def _sales_summary(
    *,
    kind: str,
    stage: str,
    clinic_name: str | None,
    owner_contact: str,
    owner_name: str | None = None,
    phone: str | None = None,
    details: str | None = None,
    recontact_after: str | None = None,
) -> str:
    lines = [
        kind,
        "",
        f"Stage: {stage}",
        f"Clinic: {clinic_name or '-'}",
        f"Owner: {owner_name or '-'}",
        f"Contact: {owner_contact}",
        f"Phone: {phone or '-'}",
    ]
    if recontact_after:
        lines.append(f"Recontact after: {recontact_after}")
    if details:
        lines.extend(["", "Details:", details])
    return "\n".join(lines)


def _lead_details(*, input_text: str, state: dict[str, Any], reason: str) -> str:
    return "\n".join(
        [
            f"Reason: {reason}",
            f"Locations: {state.get('locations') or '-'}",
            f"Demo used: {bool(state.get('demo_used'))}",
            f"Last message: {input_text}",
        ]
    )


def _amount_text(locations: Any) -> str:
    resolved = _safe_int(locations) or 1
    return f"${resolved * 100}/month"


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _last_notification_status(
    tool_calls: list[dict[str, Any]],
    *,
    fallback_sent: bool,
    fallback_message_id: int | None,
) -> tuple[bool, int | None]:
    sent = fallback_sent
    message_id = fallback_message_id
    for call in tool_calls:
        if call.get("tool") in {"notify_sales", "handoff_to_amir", "schedule_followup"}:
            sent = sent or bool(call.get("notification_sent"))
    return sent, message_id
