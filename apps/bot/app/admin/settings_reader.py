import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_repository import get_setting

logger = logging.getLogger(__name__)


async def get_system_prompt(session: AsyncSession) -> str:
    val = await get_setting(session, "llm.system_prompt")
    return str(val.get("text", ""))


async def get_welcome_message(session: AsyncSession, language: str) -> str:
    val = await get_setting(session, "bot.welcome_messages")
    return str(val.get(language, ""))


async def get_tts_prompt(session: AsyncSession, language: str) -> str:
    val = await get_setting(session, "tts.prompts")
    return str(val.get(language, ""))


async def get_clinic_info(session: AsyncSession) -> str:
    val = await get_setting(session, "clinic.info")
    return str(val.get("text", ""))
