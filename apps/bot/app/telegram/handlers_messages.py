from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.telegram.keyboards import language_keyboard
from app.telegram.persistence import save_outgoing_message
from app.telegram.texts import normalize_language, text

router = Router(name="messages")


@router.message(F.text)
async def fallback_text_handler(
    message: Message,
    db_session: AsyncSession,
    db_user: User,
    db_conversation: Conversation,
    trace_id: str,
) -> None:
    if db_user.preferred_language is None:
        response_text = text("language_required")
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
    response_text = text("fallback", language)
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
