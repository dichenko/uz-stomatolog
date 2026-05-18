from typing import Any
from uuid import uuid4

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation, User
from app.db.repositories import (
    ConversationRepository,
    MessageRepository,
    UserRepository,
)


class PersistenceMiddleware(BaseMiddleware):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        trace_id = data.get("trace_id") or uuid4().hex
        data["trace_id"] = trace_id

        async with self.session_factory() as session:
            data["db_session"] = session
            try:
                await self._persist_incoming(event, data, session, trace_id)
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise

    async def _persist_incoming(
        self,
        event: TelegramObject,
        data: dict[str, Any],
        session: AsyncSession,
        trace_id: str,
    ) -> None:
        if isinstance(event, Message):
            telegram_user = event.from_user
            if telegram_user is None:
                return

            user = await UserRepository(session).upsert_from_telegram(
                telegram_user_id=telegram_user.id,
                telegram_username=telegram_user.username,
                telegram_first_name=telegram_user.first_name,
                telegram_last_name=telegram_user.last_name,
            )
            conversation = await ConversationRepository(session).get_or_create(
                user_id=user.id,
                telegram_chat_id=event.chat.id,
            )
            message_text = event.text or event.caption
            if event.voice is not None:
                message_type = "voice"
            elif event.text is not None:
                message_type = "text"
            else:
                message_type = "system"
            incoming_message = await MessageRepository(session).save_message(
                user_id=user.id,
                conversation_id=conversation.id,
                telegram_message_id=event.message_id,
                direction="in",
                message_type=message_type,
                language=user.preferred_language,
                text=message_text,
                raw_payload=event.model_dump(mode="json", exclude_none=True),
                trace_id=trace_id,
            )
            data["db_user"] = user
            data["db_conversation"] = conversation
            data["db_incoming_message"] = incoming_message
            return

        if isinstance(event, CallbackQuery):
            telegram_user = event.from_user
            chat_id = (
                event.message.chat.id
                if event.message is not None and hasattr(event.message, "chat")
                else telegram_user.id
            )
            user = await UserRepository(session).upsert_from_telegram(
                telegram_user_id=telegram_user.id,
                telegram_username=telegram_user.username,
                telegram_first_name=telegram_user.first_name,
                telegram_last_name=telegram_user.last_name,
            )
            conversation = await ConversationRepository(session).get_or_create(
                user_id=user.id,
                telegram_chat_id=chat_id,
            )
            telegram_message_id = (
                event.message.message_id
                if event.message is not None and hasattr(event.message, "message_id")
                else None
            )
            incoming_message = await MessageRepository(session).save_message(
                user_id=user.id,
                conversation_id=conversation.id,
                telegram_message_id=telegram_message_id,
                direction="in",
                message_type="callback",
                language=user.preferred_language,
                text=event.data,
                raw_payload=event.model_dump(mode="json", exclude_none=True),
                trace_id=trace_id,
            )
            data["db_user"] = user
            data["db_conversation"] = conversation
            data["db_incoming_message"] = incoming_message


async def save_outgoing_message(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    telegram_message_id: int | None,
    text: str,
    language: str | None,
    trace_id: str,
    message_type: str = "text",
    raw_payload: dict[str, Any] | None = None,
) -> None:
    await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=telegram_message_id,
        direction="out",
        message_type=message_type,
        language=language,
        text=text,
        raw_payload=raw_payload,
        trace_id=trace_id,
    )
