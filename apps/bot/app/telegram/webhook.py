import logging
from uuid import uuid4

from aiogram import Bot, Dispatcher
from aiogram.types import Update
from fastapi import FastAPI, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.telegram.router import create_dispatcher

logger = logging.getLogger(__name__)


async def setup_telegram(app: FastAPI) -> None:
    settings = get_settings()
    app.state.telegram_bot = None
    app.state.telegram_dispatcher = None

    token = (
        settings.telegram_bot_token.get_secret_value()
        if settings.telegram_bot_token is not None
        else None
    )
    if not token:
        logger.info("telegram_bot_not_configured")
        return

    bot = Bot(token=token)
    dispatcher = create_dispatcher(async_session_factory)
    app.state.telegram_bot = bot
    app.state.telegram_dispatcher = dispatcher

    webhook_url = f"{settings.app_base_url.rstrip('/')}{settings.telegram_webhook_path}"
    if settings.app_env == "prod":
        secret_token = (
            settings.telegram_webhook_secret.get_secret_value()
            if settings.telegram_webhook_secret is not None
            else None
        )
        await bot.set_webhook(webhook_url, secret_token=secret_token)
        logger.info("telegram_webhook_registered", extra={"webhook_url": webhook_url})
    else:
        logger.info(
            "telegram_webhook_registration_skipped",
            extra={"app_env": settings.app_env, "webhook_url": webhook_url},
        )


async def shutdown_telegram(app: FastAPI) -> None:
    bot: Bot | None = app.state.telegram_bot
    if bot is not None:
        await bot.session.close()


def register_telegram_webhook_route(app: FastAPI) -> None:
    settings = get_settings()

    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _validate_secret(settings, x_telegram_bot_api_secret_token)

        bot: Bot | None = request.app.state.telegram_bot
        dispatcher: Dispatcher | None = request.app.state.telegram_dispatcher
        if bot is None or dispatcher is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Telegram bot is not configured",
            )

        payload = await request.json()
        trace_id = uuid4().hex
        update = Update.model_validate(payload, context={"bot": bot})
        await dispatcher.feed_update(bot, update, trace_id=trace_id)
        logger.info(
            "telegram_update_processed",
            extra={"trace_id": trace_id, "update_id": update.update_id},
        )
        return {"ok": True}

    app.add_api_route(
        settings.telegram_webhook_path,
        telegram_webhook,
        methods=["POST"],
        name="telegram_webhook",
    )


def _validate_secret(settings: Settings, provided_secret: str | None) -> None:
    expected_secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret is not None
        else None
    )
    if expected_secret and provided_secret != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Telegram webhook secret token",
        )
