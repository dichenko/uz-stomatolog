from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, computed_field
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
    speech_temp_dir: str = "/tmp/dental-bot-audio"

    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_webhook_path: str = "/telegram/webhook"
    admin_telegram_chat_id: str | None = None
    dev_admin_tg_id: str | None = None
    bot_mode: Literal["webhook", "polling"] = "polling"

    telegram_oidc_client_id: str = ""
    telegram_oidc_client_secret: str = ""
    telegram_oidc_redirect_uri: str = ""
    telegram_admin_ids: str = ""
    session_secret: str = ""
    session_cookie_name: str = "uz_stomatolog_admin_session"
    session_cookie_max_age_days: int = 30

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "dental_bot"
    postgres_user: str = "dental_bot"
    postgres_password: SecretStr | None = None

    @computed_field
    @property
    def database_url(self) -> str:
        password_value = (
            self.postgres_password.get_secret_value()
            if self.postgres_password is not None
            else "password"
        )
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password_value}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_text_model: str = "gpt-4.1-mini"
    openai_stt_model: str = "gpt-4o-transcribe"
    openai_stt_language: str = "ru"
    openai_stt_response_format: str = "json"
    openai_stt_timeout_ms: int = 60000
    openai_stt_max_audio_size_mb: int = 25
    openai_stt_prompt: str = ""
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "marin"
    openai_tts_fallback_voice: str = "cedar"
    openai_tts_response_format: Literal["mp3", "opus", "aac", "flac", "wav", "pcm"] = (
        "opus"
    )
    openai_tts_timeout_ms: int = 60000
    openai_tts_max_chars: int = 4096
    openai_tts_speed: float = 1.0
    openai_tts_instructions: str = ""

    muxlisa_api_key: SecretStr | None = None
    muxlisa_base_url: str | None = "https://service.muxlisa.uz"
    muxlisa_stt_timeout_ms: int = 60000
    muxlisa_tts_timeout_ms: int = 60000
    muxlisa_max_audio_size_mb: int = 5
    muxlisa_max_audio_duration_sec: int = 60
    muxlisa_tts_max_chars: int = 512
    muxlisa_tts_speaker: int = 0

    yandex_speechkit_api_key: SecretStr | None = None
    yandex_tts_base_url: str = "https://tts.api.cloud.yandex.net"
    yandex_tts_model: str = "yandex-speechkit-tts-v1"
    yandex_tts_language: str = "ru-RU"
    yandex_tts_voice: str = "alena"
    yandex_tts_emotion: str = "good"
    yandex_tts_speed: str = "1.15"
    yandex_tts_format: str = "oggopus"
    yandex_tts_timeout_ms: int = 60000
    yandex_tts_max_chars: int = 5000

    azure_speech_key: SecretStr | None = None
    azure_speech_region: str = "westeurope"
    azure_speech_endpoint: str = (
        "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
    )
    azure_tts_language: str = "ru-RU"
    azure_tts_voice: str = "ru-RU-SvetlanaNeural"
    azure_tts_output_format: str = "ogg-24khz-16bit-mono-opus"
    azure_tts_rate: str = "20%"
    azure_tts_pitch: str = ""
    azure_tts_range: str = ""
    azure_tts_timeout_ms: int = 60000
    azure_tts_max_chars: int = 5000

    google_calendar_id: str | None = None
    google_service_account_json_path: str = "/run/secrets/google_service_account.json"

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "dental-telegram-mvp"

    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "dental-telegram-bot"


@lru_cache
def get_settings() -> Settings:
    return Settings()
