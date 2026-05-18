from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.db.repositories import UserRepository
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
