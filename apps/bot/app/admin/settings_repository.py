import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AdminSetting

logger = logging.getLogger(__name__)

DEFAULT_VALUES: dict[str, dict[str, Any]] = {
    "llm.system_prompt": {"text": ""},
    "bot.welcome_messages": {"ru": "", "uz": "", "en": ""},
    "tts.prompts": {"ru": "", "uz": "", "en": ""},
    "clinic.info": {"text": ""},
}


async def get_setting(
    session: AsyncSession, key: str
) -> dict[str, Any]:
    result = await session.get(AdminSetting, key)
    if result is not None:
        try:
            return result.value if isinstance(result.value, dict) else {}
        except Exception:
            logger.exception("admin_settings_invalid_json", extra={"key": key})
    return DEFAULT_VALUES.get(key, {})


async def set_setting(
    session: AsyncSession,
    key: str,
    value: dict[str, Any],
    tg_id: str,
) -> dict[str, Any]:
    setting = await session.get(AdminSetting, key)
    if setting is None:
        setting = AdminSetting(key=key, value=value)
        session.add(setting)
    else:
        setting.value = value
    setting.updated_by_tg_id = tg_id
    await session.flush()
    return setting.value


async def get_all_settings(
    session: AsyncSession,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "systemPrompt": DEFAULT_VALUES["llm.system_prompt"],
        "welcomeMessages": DEFAULT_VALUES["bot.welcome_messages"],
        "ttsPrompts": DEFAULT_VALUES["tts.prompts"],
        "clinicInfo": DEFAULT_VALUES["clinic.info"],
    }
    try:
        for key, output_key in _KEY_MAPPING.items():
            val = await get_setting(session, key)
            if val:
                result[output_key] = val
    except Exception:
        logger.exception("admin_get_all_settings_failed")
    return result


_KEY_MAPPING = {
    "llm.system_prompt": "systemPrompt",
    "bot.welcome_messages": "welcomeMessages",
    "tts.prompts": "ttsPrompts",
    "clinic.info": "clinicInfo",
}
