import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.config import get_settings
from app.logging import configure_logging

configure_logging()

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logger.info(
        "bot_http_app_started",
        extra={
            "app_env": settings.app_env,
            "app_timezone": settings.app_timezone,
            "telegram_webhook_path": settings.telegram_webhook_path,
        },
    )
    yield


app = FastAPI(
    title="Dental Clinic Telegram Assistant",
    version="0.1.0",
    lifespan=lifespan,
)


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
