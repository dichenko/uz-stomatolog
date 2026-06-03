import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings
from app.speech.base import SpeechProviderError, TextToSpeechResult
from app.speech.temp_files import create_temp_audio_path

logger = logging.getLogger(__name__)

AISHA_TTS_MODEL = "aisha-tts"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class AishaTtsProvider:
    _config_logged = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        self._log_config_once()
        prepared_text = _prepare_text_for_tts(text)
        if not prepared_text:
            raise SpeechProviderError("Aisha TTS input is empty")
        if len(prepared_text) > self.settings.aisha_tts_max_chars:
            max_chars = self.settings.aisha_tts_max_chars
            truncated_text = prepared_text[:max_chars]
            prepared_text = truncated_text.rsplit(" ", 1)[0] or truncated_text

        started_at = time.perf_counter()
        audio_bytes = await self._retry(
            call=lambda: self._synthesize_once(prepared_text),
        )
        output_path = create_temp_audio_path(suffix=".wav")
        output_path.write_bytes(audio_bytes)
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "aisha",
                "operation": "tts",
                "model": AISHA_TTS_MODEL,
                "voice": self.settings.aisha_tts_model,
                "language": language,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": output_path.stat().st_size,
            },
        )
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type="audio/wav",
            format="wav",
            provider="aisha",
            model=AISHA_TTS_MODEL,
            voice=self.settings.aisha_tts_model,
        )

    async def _synthesize_once(self, text: str) -> bytes:
        headers = {
            "X-Api-Key": self._api_key_or_raise(),
            "Accept-Language": self.settings.aisha_tts_language,
        }
        fields = {
            "transcript": (None, text),
            "language": (None, self.settings.aisha_tts_language),
            "model": (None, self.settings.aisha_tts_model),
            "mood": (None, self.settings.aisha_tts_mood),
            "speed": (None, str(self.settings.aisha_tts_speed)),
        }
        timeout = self.settings.aisha_tts_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url()}/api/v1/tts/post/",
                headers=headers,
                files=fields,
            )
            if response.status_code >= 400:
                raise self._aisha_error(response)
            audio_path = self._audio_path_from_response(response)
            audio_response = await client.get(
                urljoin(f"{self._base_url()}/", audio_path),
                headers=headers,
            )
        if audio_response.status_code >= 400:
            raise self._aisha_error(audio_response)
        return audio_response.content

    async def _retry(self, *, call: Any) -> bytes:
        delays = (0, 2, 5)
        last_error: Exception | None = None
        for attempt, delay_seconds in enumerate(delays, start=1):
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            try:
                return await call()
            except AishaStatusError as exc:
                if exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise SpeechProviderError(
                        f"Aisha TTS failed with status {exc.status_code}"
                    ) from exc
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_status",
                    extra={
                        "provider": "aisha",
                        "operation": "tts",
                        "attempt": attempt,
                        "status_code": exc.status_code,
                    },
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_error",
                    extra={
                        "provider": "aisha",
                        "operation": "tts",
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    },
                )
        raise SpeechProviderError("Aisha TTS failed after retries") from last_error

    def _api_key_or_raise(self) -> str:
        if self.settings.aisha_api_key is None:
            raise SpeechProviderError("AISHA_API_KEY is required for Aisha TTS")
        api_key = self.settings.aisha_api_key.get_secret_value().strip()
        if not api_key:
            raise SpeechProviderError("AISHA_API_KEY is required for Aisha TTS")
        return api_key

    def _base_url(self) -> str:
        return self.settings.aisha_base_url.rstrip("/")

    def _audio_path_from_response(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpeechProviderError("Aisha TTS response is not valid JSON") from exc
        audio_path = payload.get("audio_path")
        if not isinstance(audio_path, str) or not audio_path.strip():
            raise SpeechProviderError("Aisha TTS response did not include audio_path")
        return audio_path.strip()

    def _log_config_once(self) -> None:
        if AishaTtsProvider._config_logged:
            return
        AishaTtsProvider._config_logged = True
        logger.info(
            "aisha_tts_config",
            extra={
                "base_url": self._base_url(),
                "language": self.settings.aisha_tts_language,
                "model": self.settings.aisha_tts_model,
                "mood": self.settings.aisha_tts_mood,
                "speed": self.settings.aisha_tts_speed,
            },
        )

    def _aisha_error(self, response: httpx.Response) -> "AishaStatusError":
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
        else:
            body = response.text
        logger.error(
            "aisha_tts_failed",
            extra={
                "status_code": response.status_code,
                "body": str(body)[:1000],
                "language": self.settings.aisha_tts_language,
                "model": self.settings.aisha_tts_model,
                "mood": self.settings.aisha_tts_mood,
                "speed": self.settings.aisha_tts_speed,
            },
        )
        return AishaStatusError(
            f"Aisha TTS failed: status={response.status_code}, body={body}",
            response.status_code,
        )


class AishaStatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _prepare_text_for_tts(text: str) -> str:
    prepared = text.strip()
    prepared = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", prepared)
    prepared = re.sub(r"https?://\S+", "", prepared)
    prepared = re.sub(r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]", "", prepared)
    prepared = prepared.replace("**", "")
    prepared = prepared.replace("__", "")
    prepared = prepared.replace("`", "")
    prepared = prepared.replace("\u2022", ". ")
    prepared = prepared.replace("-", " ")
    prepared = re.sub(r"\s+", " ", prepared)
    return prepared.strip()
