import logging
import re
import time
from typing import Any

import httpx

from app.config import Settings
from app.speech.base import SpeechProviderError, TextToSpeechResult
from app.speech.temp_files import create_temp_audio_path

logger = logging.getLogger(__name__)


class YandexSpeechKitProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        prepared_text = _prepare_text_for_tts(text)
        if not prepared_text:
            raise SpeechProviderError("Yandex SpeechKit TTS input is empty")
        if len(prepared_text) > self.settings.yandex_tts_max_chars:
            max_chars = self.settings.yandex_tts_max_chars
            truncated_text = prepared_text[:max_chars]
            prepared_text = truncated_text.rsplit(" ", 1)[0] or truncated_text

        started_at = time.perf_counter()
        audio_bytes = await self._synthesize_once(prepared_text)
        output_path = create_temp_audio_path(suffix=".ogg")
        output_path.write_bytes(audio_bytes)
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "yandex",
                "operation": "tts",
                "model": self.settings.yandex_tts_model,
                "voice": self.settings.yandex_tts_voice,
                "emotion": self.settings.yandex_tts_emotion,
                "speed": self.settings.yandex_tts_speed,
                "language": language,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": output_path.stat().st_size,
            },
        )
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type="audio/ogg",
            format="opus",
            provider="yandex",
            model=self.settings.yandex_tts_model,
            voice=self.settings.yandex_tts_voice,
        )

    async def _synthesize_once(self, text: str) -> bytes:
        headers = {
            "Authorization": f"Api-Key {self._api_key_or_raise()}",
        }
        payload = {
            "text": text,
            "lang": self.settings.yandex_tts_language,
            "voice": self.settings.yandex_tts_voice,
            "emotion": self.settings.yandex_tts_emotion,
            "speed": self.settings.yandex_tts_speed,
            "format": self.settings.yandex_tts_format,
        }
        timeout = self.settings.yandex_tts_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url()}/speech/v1/tts:synthesize",
                headers=headers,
                data=payload,
            )
        if response.status_code >= 400:
            raise _yandex_error(response)
        return response.content

    def _api_key_or_raise(self) -> str:
        if self.settings.yandex_speechkit_api_key is None:
            raise SpeechProviderError(
                "YANDEX_SPEECHKIT_API_KEY is required for Yandex SpeechKit"
            )
        api_key = self.settings.yandex_speechkit_api_key.get_secret_value().strip()
        if not api_key:
            raise SpeechProviderError(
                "YANDEX_SPEECHKIT_API_KEY is required for Yandex SpeechKit"
            )
        return api_key

    def _base_url(self) -> str:
        return self.settings.yandex_tts_base_url.rstrip("/")


class YandexSpeechKitStatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _yandex_error(response: httpx.Response) -> YandexSpeechKitStatusError:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
    else:
        body = response.text
    return YandexSpeechKitStatusError(
        f"Yandex SpeechKit TTS failed: status={response.status_code}, body={body}",
        response.status_code,
    )


def _prepare_text_for_tts(text: str) -> str:
    prepared = text.strip()
    prepared = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", prepared)
    prepared = re.sub(r"https?://\S+", "", prepared)
    prepared = prepared.replace("**", "")
    prepared = prepared.replace("__", "")
    prepared = prepared.replace("`", "")
    prepared = prepared.replace("•", ". ")
    prepared = prepared.replace("-", " ")
    prepared = prepared.replace("₽", " рублей")
    prepared = prepared.replace("$", " долларов")
    prepared = prepared.replace("%", " процентов")
    prepared = re.sub(r"\s+", " ", prepared)
    return prepared.strip()
