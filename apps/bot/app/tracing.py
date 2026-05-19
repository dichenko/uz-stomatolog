import logging
import os

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def configure_tracing(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    _configure_langsmith(resolved)
    _configure_opentelemetry(resolved)


def _configure_langsmith(settings: Settings) -> None:
    if not settings.langsmith_tracing:
        logger.info("langsmith_tracing_disabled")
        return

    api_key = (
        settings.langsmith_api_key.get_secret_value()
        if settings.langsmith_api_key is not None
        else None
    )
    if not api_key:
        logger.warning("langsmith_api_key_missing")
        return

    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    os.environ["LANGSMITH_ENDPOINT"] = (
        "https://api.smith.langchain.com"
    )
    logger.info(
        "langsmith_tracing_configured",
        extra={
            "project": settings.langsmith_project,
        },
    )


def _configure_opentelemetry(settings: Settings) -> None:
    if not settings.otel_enabled:
        logger.info("opentelemetry_disabled")
        return

    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        logger.warning("opentelemetry_endpoint_missing")
        return

    os.environ.setdefault("OTEL_SERVICE_NAME", settings.otel_service_name)
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", endpoint)
    os.environ.setdefault("OTEL_TRACES_EXPORTER", "otlp")
    logger.info(
        "opentelemetry_configured",
        extra={
            "endpoint": endpoint,
            "service_name": settings.otel_service_name,
        },
    )
