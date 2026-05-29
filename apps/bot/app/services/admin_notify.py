import logging
import traceback as traceback_module
from dataclasses import dataclass
from typing import Any

from aiogram.exceptions import TelegramAPIError

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdminNotificationResult:
    sent: bool
    admin_chat_id: str | None = None
    admin_message_id: int | None = None


async def send_admin_notification(
    *,
    bot: Any | None,
    message_text: str,
    settings: Settings | None = None,
) -> AdminNotificationResult:
    resolved_settings = settings or get_settings()
    admin_chat_id = resolved_settings.admin_telegram_chat_id
    if bot is None or not admin_chat_id:
        logger.info(
            "admin_notification_skipped",
            extra={
                "has_bot": bot is not None,
                "has_admin_chat_id": bool(admin_chat_id),
            },
        )
        return AdminNotificationResult(sent=False, admin_chat_id=admin_chat_id)

    try:
        sent_message = await bot.send_message(chat_id=admin_chat_id, text=message_text)
    except TelegramAPIError as exc:
        logger.exception(
            "admin_notification_failed",
            extra={
                "admin_chat_id": admin_chat_id,
                "telegram_error": str(exc),
            },
        )
        return AdminNotificationResult(sent=False, admin_chat_id=admin_chat_id)

    message_id = getattr(sent_message, "message_id", None)
    logger.info(
        "admin_notification_sent",
        extra={"admin_chat_id": admin_chat_id, "admin_message_id": message_id},
    )
    return AdminNotificationResult(
        sent=True,
        admin_chat_id=admin_chat_id,
        admin_message_id=message_id,
    )


async def notify_dev_admin(
    *,
    bot: Any | None,
    error: str,
    trace_id: str | None = None,
    user_info: str = "",
    settings: Settings | None = None,
) -> None:
    """Send error notification to DEV_ADMIN_TG_ID."""
    resolved_settings = settings or get_settings()
    dev_chat_id = resolved_settings.dev_admin_tg_id
    if bot is None or not dev_chat_id:
        return

    import datetime

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = "⚠️ BOT ERROR"
    if trace_id:
        header += f" | trace={trace_id}"
    if user_info:
        header += f" | {user_info}"

    full_traceback = traceback_module.format_exc()
    if full_traceback and full_traceback != "NoneType: None\n":
        tb_snippet = full_traceback[-3500:]
        error_msg = f"{header}\n\n{error}\n\n```\n{tb_snippet}\n```\n\n_{now}_"
    else:
        error_msg = f"{header}\n\n{error}\n\n_{now}_"

    try:
        await bot.send_message(
            chat_id=dev_chat_id, text=error_msg[:4000], parse_mode="Markdown"
        )
    except TelegramAPIError as exc:
        logger.exception(
            "dev_admin_notification_failed",
            extra={"dev_chat_id": dev_chat_id, "error": str(exc)},
        )
