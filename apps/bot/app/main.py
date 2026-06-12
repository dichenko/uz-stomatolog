import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.sessions import SessionMiddleware
from starlette.status import HTTP_302_FOUND

from app.admin import admin_router
from app.admin.auth import SESSION_KEY_TG_ID, is_admin
from app.config import get_settings
from app.db.session import async_session_factory
from app.llm.repository import ensure_llm_provider_defaults
from app.logging import configure_logging
from app.services.clinic_knowledge import load_clinic_knowledge_if_empty
from app.telegram.webhook import (
    register_telegram_webhook_route,
    setup_telegram,
    shutdown_telegram,
)
from app.tracing import configure_tracing
from app.workers.calendar_sync_worker import calendar_sync_worker_loop
from app.workers.reminder_worker import reminder_worker_loop

configure_logging()

settings = get_settings()
configure_tracing(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "bot_http_app_started",
        extra={
            "app_env": settings.app_env,
            "app_timezone": settings.app_timezone,
            "telegram_webhook_path": settings.telegram_webhook_path,
        },
    )
    try:
        async with async_session_factory() as session:
            await ensure_llm_provider_defaults(session, settings=settings)
            loaded_knowledge_count = await load_clinic_knowledge_if_empty(session)
            await session.commit()
            if loaded_knowledge_count:
                logger.info(
                    "clinic_knowledge_loaded",
                    extra={"loaded_knowledge_count": loaded_knowledge_count},
                )
    except SQLAlchemyError:
        logger.exception("clinic_knowledge_load_skipped")
    await setup_telegram(fastapi_app)
    bot = fastapi_app.state.telegram_bot
    stop_event = asyncio.Event()
    reminder_task = asyncio.create_task(
        reminder_worker_loop(
            session_factory=async_session_factory,
            bot=bot,
            stop_event=stop_event,
        )
    )
    sync_task = asyncio.create_task(
        calendar_sync_worker_loop(
            session_factory=async_session_factory,
            stop_event=stop_event,
        )
    )
    try:
        yield
    finally:
        stop_event.set()
        await asyncio.gather(reminder_task, sync_task)
        await shutdown_telegram(fastapi_app)


app = FastAPI(
    title="Dental Clinic Telegram Assistant",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret or "change-me-in-production",
    session_cookie=settings.session_cookie_name,
    max_age=settings.session_cookie_max_age_days * 86400,
    https_only=settings.app_env == "prod",
    same_site="lax",
)
app.include_router(admin_router)
register_telegram_webhook_route(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "OK"}


@app.get("/")
async def root(request: Request):
    tg_id = request.session.get(SESSION_KEY_TG_ID)
    if tg_id and is_admin(str(tg_id)):
        return RedirectResponse("/admin/", status_code=HTTP_302_FOUND)
    return RedirectResponse("/admin/login", status_code=HTTP_302_FOUND)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
    )
