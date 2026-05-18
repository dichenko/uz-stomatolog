from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["dev", "test", "prod"] = "dev"
    app_base_url: str = "http://localhost:8000"
    app_timezone: str = "Asia/Tashkent"

    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_path: str = "/telegram/webhook"
    admin_telegram_chat_id: str | None = None

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "dental_bot"
    postgres_user: str = "dental_bot"
    postgres_password: SecretStr | None = None
    database_url: SecretStr = Field(
        default=SecretStr(
            "postgresql+asyncpg://dental_bot:password@postgres:5432/dental_bot"
        )
    )

    openai_api_key: SecretStr | None = None
    openai_text_model: str = "gpt-4.1-mini"
    openai_stt_model: str = "gpt-4o-mini-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"

    muxlisa_api_key: SecretStr | None = None
    muxlisa_base_url: str | None = None

    google_calendar_id: str | None = None
    google_service_account_json_path: str = "/run/secrets/google_service_account.json"

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "dental-telegram-mvp"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "dental-telegram-bot"


@lru_cache
def get_settings() -> Settings:
    return Settings()
