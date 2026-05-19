from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.db.repositories import UserRepository
from app.services.booking import BookingFlowError, confirm_booking_slot
from app.telegram.persistence import save_outgoing_message
from app.telegram.texts import SUPPORTED_LANGUAGES, normalize_language, text

router = Router(name="callbacks")


@router.callback_query(F.data.startswith("language:"))
async def language_callback_handler(
    callback: CallbackQuery,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = callback.data.split(":", 1)[1] if callback.data else ""
    if language not in SUPPORTED_LANGUAGES:
        await callback.answer("Unsupported language", show_alert=True)
        return

    db_user = await UserRepository(db_session).set_language(
        db_user.telegram_user_id,
        language,
    )
    await callback.answer()

    response_text = text("language_saved", language)
    if isinstance(callback.message, Message):
        sent = await callback.message.answer(response_text)
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=response_text,
            language=normalize_language(language),
            trace_id=trace_id,
        )


@router.callback_query(F.data.startswith("booking_slot:"))
async def booking_slot_callback_handler(
    callback: CallbackQuery,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    try:
        slot_index = int(callback.data.split(":", 1)[1] if callback.data else "-1")
        result = await confirm_booking_slot(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            slot_index=slot_index,
            language=language,
            admin_bot=callback.bot,
        )
    except (ValueError, BookingFlowError):
        await callback.answer(
            _booking_slot_error_text(language),
            show_alert=True,
        )
        return

    await callback.answer()
    if isinstance(callback.message, Message):
        sent = await callback.message.answer(result.text)
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=sent.message_id,
            text=result.text,
            language=language,
            trace_id=trace_id,
            raw_payload={
                "booking_confirmed": True,
                "appointment_id": result.appointment.id,
                "calendar_event_id": result.calendar_event_id,
                "admin_notification_sent": result.admin_notification_sent,
            },
        )


def _booking_slot_error_text(language: str) -> str:
    return {
        "ru": "Этот слот уже недоступен. Напишите, пожалуйста, желаемое время ещё раз.",
        "uz": "Bu vaqt endi mavjud emas. Iltimos, qulay vaqtni qayta yozing.",
        "en": (
            "This slot is no longer available. "
            "Please send your preferred time again."
        ),
    }[normalize_language(language)]
