import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db.session import async_session_factory
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
register_telegram_webhook_route(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "OK"}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_config=None,
    )
