from typing import Literal, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.llm.manager import complete_text_with_fallback

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: ChatRole
    content: str


async def complete_text(
    *,
    messages: list[ChatMessage],
    temperature: float = 0,
    response_format: Literal["json_object"] | None = None,
    settings: Settings | None = None,
    session: AsyncSession | None = None,
    request_id: str | None = None,
    telegram_user_id: int | None = None,
) -> str | None:
    return await complete_text_with_fallback(
        messages=[dict(message) for message in messages],
        temperature=temperature,
        response_format=response_format,
        settings=settings,
        session=session,
        request_id=request_id,
        telegram_user_id=telegram_user_id,
    )
