from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.db.repositories import AppointmentRepository
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
    if db_user.preferred_language is None:
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
            raw_payload={"reply_markup": "language_keyboard"},
        )
        return

    language = normalize_language(db_user.preferred_language)
    response_text = text("welcome", language)
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
            lines.append(f"- {start}: {appointment.service_type}, {appointment.doctor_type}")
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
