from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import is_admin
from app.config import get_settings
from app.db.models import Conversation, User
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    ExecutionRunRepository,
    MessageRepository,
    UserRepository,
)
from app.telegram.keyboards import language_keyboard
from app.telegram.persistence import save_outgoing_message
from app.telegram.texts import normalize_language, text

router = Router(name="start")


@router.message(CommandStart())
async def start_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    await reset_user_dialog_history(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
    )
    language = normalize_language(db_user.preferred_language)
    response_text = text("choose_language", language)
    sent = await message.answer(response_text, reply_markup=language_keyboard())
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
        raw_payload={"reply_markup": "language_keyboard"},
    )


@router.message(Command("language"))
async def language_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    response_text = text("choose_language", language)
    sent = await message.answer(response_text, reply_markup=language_keyboard())
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
        raw_payload={"reply_markup": "language_keyboard"},
    )


@router.message(Command("admin"))
async def admin_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    settings = get_settings()
    language = normalize_language(db_user.preferred_language)
    if not is_admin(str(db_user.telegram_user_id), settings):
        response_text = _admin_access_denied_text(language)
    else:
        response_text = _admin_link_text(settings.app_base_url, language)

    sent = await message.answer(response_text)
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
        raw_payload={"command": "admin"},
    )


@router.message(Command("restart"))
async def restart_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    await reset_user_dialog_history(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
    )
    response_text = text("choose_language")
    sent = await message.answer(response_text, reply_markup=language_keyboard())
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=None,
        trace_id=trace_id,
        raw_payload={"reply_markup": "language_keyboard", "restart": True},
    )


@router.message(Command("help"))
async def help_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    response_text = text("help", language)
    sent = await message.answer(response_text)
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
    )


def _admin_link_text(app_base_url: str, language: str) -> str:
    url = f"{app_base_url.rstrip('/')}/admin/login"
    if language == "uz":
        return f"Admin panel: {url}"
    if language == "en":
        return f"Admin panel: {url}"
    return f"Админка: {url}"


def _admin_access_denied_text(language: str) -> str:
    if language == "uz":
        return "Bu buyruq faqat administratorlar uchun."
    if language == "en":
        return "This command is available only to administrators."
    return "Эта команда доступна только администраторам."


async def reset_user_dialog_history(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
) -> None:
    await ExecutionRunRepository(session).delete_for_conversation(
        user_id=user.id,
        conversation_id=conversation.id,
    )
    await MessageRepository(session).delete_for_conversation(
        user_id=user.id,
        conversation_id=conversation.id,
    )
    await UserRepository(session).clear_language(user.telegram_user_id)
    await ConversationRepository(session).reset_state(conversation_id=conversation.id)
    user.preferred_language = None
    conversation.current_flow = None
    conversation.current_state = None
    conversation.summary = None


@router.message(Command("my_appointments"))
async def my_appointments_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    appointments = await AppointmentRepository(db_session).get_active_future_by_user(
        user_id=db_user.id
    )
    if not appointments:
        response_text = text("appointments_empty", language)
    else:
        lines = [text("appointments_header", language)]
        for appointment in appointments:
            start = appointment.start_at.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"- {start}: {appointment.service_type}, {appointment.doctor_type}"
            )
        response_text = "\n".join(lines)

    sent = await message.answer(response_text)
    await save_outgoing_message(
        session=db_session,
        user=db_user,
        conversation=db_conversation,
        telegram_message_id=sent.message_id,
        text=response_text,
        language=language,
        trace_id=trace_id,
    )
