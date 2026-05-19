from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_reader import get_welcome_message
from app.db.models import Conversation, User
from app.db.repositories import UserRepository
from app.services.booking import BookingFlowError, confirm_booking_slot
from app.services.cancellation import CancellationError, confirm_cancellation
from app.services.rescheduling import (
    ReschedulingError,
    ReschedulingSlotConflictError,
    confirm_reschedule_slot,
    propose_reschedule_slots,
)
from app.telegram.keyboards import reschedule_slots_keyboard
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

    welcome = ""
    try:
        welcome = await get_welcome_message(db_session, language)
    except Exception:
        pass

    response_text = (
        welcome.strip() if welcome.strip()
        else text("language_saved", language)
    )
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


@router.callback_query(F.data.startswith("cancel_appointment:"))
async def cancel_appointment_callback_handler(
    callback: CallbackQuery,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    try:
        appointment_id = int(callback.data.split(":", 1)[1] if callback.data else "-1")
        result = await confirm_cancellation(
            session=db_session,
            user=db_user,
            appointment_id=appointment_id,
            language=language,
            admin_bot=callback.bot,
        )
    except (ValueError, CancellationError):
        await callback.answer(
            _cancellation_error_text(language),
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
                "cancellation_confirmed": True,
                "appointment_id": result.appointment.id,
                "calendar_cancelled": result.calendar_cancelled,
                "admin_notification_sent": result.admin_notification_sent,
            },
        )


def _cancellation_error_text(language: str) -> str:
    return {
        "ru": "Не удалось отменить запись. Возможно, она уже отменена.",
        "uz": "Yozuvni bekor qilib bo'lmadi. Ehtimol u allaqachon bekor qilingan.",
        "en": "Could not cancel the appointment. It may already be cancelled.",
    }[normalize_language(language)]


@router.callback_query(F.data.startswith("reschedule_select:"))
async def reschedule_select_callback_handler(
    callback: CallbackQuery,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    try:
        appointment_id = int(callback.data.split(":", 1)[1] if callback.data else "-1")
        result = await propose_reschedule_slots(
            session=db_session,
            conversation=db_conversation,
            appointment_id=appointment_id,
            language=language,
            calendar_service=None,
        )
    except (ValueError, ReschedulingError):
        await callback.answer(
            _reschedule_error_text(language),
            show_alert=True,
        )
        return

    await callback.answer()
    if isinstance(callback.message, Message):
        if result.proposed_slots:
            await callback.message.edit_text(
                result.text,
                reply_markup=reschedule_slots_keyboard(result.proposed_slots),
            )
        else:
            await callback.message.edit_text(result.text)


@router.callback_query(F.data.startswith("reschedule_slot:"))
async def reschedule_slot_callback_handler(
    callback: CallbackQuery,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    language = normalize_language(db_user.preferred_language)
    try:
        slot_index = int(callback.data.split(":", 1)[1] if callback.data else "-1")
        result = await confirm_reschedule_slot(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            slot_index=slot_index,
            language=language,
            admin_bot=callback.bot,
        )
    except (ValueError, ReschedulingSlotConflictError):
        await callback.answer(
            _reschedule_slot_error_text(language),
            show_alert=True,
        )
        return
    except ReschedulingError:
        await callback.answer(
            _reschedule_error_text(language),
            show_alert=True,
        )
        return

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(result.text)
        await save_outgoing_message(
            session=db_session,
            user=db_user,
            conversation=db_conversation,
            telegram_message_id=callback.message.message_id,
            text=result.text,
            language=language,
            trace_id=trace_id,
            raw_payload={
                "reschedule_confirmed": True,
                "appointment_id": result.appointment.id,
                "calendar_event_id": result.calendar_event_id,
                "calendar_event_updated": result.calendar_event_updated,
                "admin_notification_sent": result.admin_notification_sent,
            },
        )


def _reschedule_error_text(language: str) -> str:
    return {
        "ru": "Не удалось перенести запись. Возможно, она уже недоступна.",
        "uz": "Yozuvni ko'chirib bo'lmadi. Ehtimol u endi mavjud emas.",
        "en": "Could not reschedule the appointment. It may no longer be available.",
    }[normalize_language(language)]


def _reschedule_slot_error_text(language: str) -> str:
    return {
        "ru": "Этот слот уже недоступен. Выберите другое время или начните заново.",
        "uz": "Bu vaqt endi mavjud emas. Boshqa vaqt tanlang yoki qaytadan boshlang.",
        "en": "This slot is no longer available. Choose another time or start over.",
    }[normalize_language(language)]


def _booking_slot_error_text(language: str) -> str:
    return {
        "ru": "Этот слот уже недоступен. Напишите, пожалуйста, желаемое время ещё раз.",
        "uz": "Bu vaqt endi mavjud emas. Iltimos, qulay vaqtni qayta yozing.",
        "en": (
            "This slot is no longer available. "
            "Please send your preferred time again."
        ),
    }[normalize_language(language)]
