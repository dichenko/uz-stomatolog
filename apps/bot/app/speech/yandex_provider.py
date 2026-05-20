import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.speech.base import SpeechProviderError, SpeechToTextResult
from app.speech.temp_files import validate_file_size

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class YandexSpeechKitProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, file_path: str, language: str) -> SpeechToTextResult:
        validate_file_size(
            file_path,
            max_size_mb=self.settings.yandex_stt_max_audio_size_mb,
        )
        started_at = time.perf_counter()
        result = await self._retry(
            operation="stt",
            call=lambda: self._transcribe_once(Path(file_path)),
        )
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "yandex",
                "operation": "stt",
                "model": self.settings.yandex_stt_model,
                "language": language,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": Path(file_path).stat().st_size,
            },
        )
        return SpeechToTextResult(
            text=(result.get("result") or "").strip(),
            provider="yandex",
            model=self.settings.yandex_stt_model,
            language=language,
            raw=result,
        )

    async def _transcribe_once(self, file_path: Path) -> dict[str, Any]:
        params = {
            "lang": self.settings.yandex_stt_language,
            "format": self.settings.yandex_stt_format,
            "topic": self.settings.yandex_stt_topic,
        }
        headers = {
            "Authorization": f"Api-Key {self._api_key_or_raise()}",
        }
        timeout = self.settings.yandex_stt_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url()}/speech/v1/stt:recognize",
                params=params,
                headers=headers,
                content=file_path.read_bytes(),
            )
        if response.status_code >= 400:
            raise _yandex_error(response)
        body = response.json()
        if not isinstance(body, dict):
            raise SpeechProviderError("Yandex STT returned non-object JSON")
        return body

    async def _retry(self, *, operation: str, call: Any) -> Any:
        delays = (0, 2, 5)
        last_error: Exception | None = None
        for attempt, delay_seconds in enumerate(delays, start=1):
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            try:
                return await call()
            except YandexSpeechKitStatusError as exc:
                if exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise SpeechProviderError(
                        f"Yandex SpeechKit {operation} failed with status "
                        f"{exc.status_code}"
                    ) from exc
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_status",
                    extra={
                        "provider": "yandex",
                        "operation": operation,
                        "attempt": attempt,
                        "status_code": exc.status_code,
                    },
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_error",
                    extra={
                        "provider": "yandex",
                        "operation": operation,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    },
                )

        raise SpeechProviderError(
            f"Yandex SpeechKit {operation} failed after retries"
        ) from last_error

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
        return self.settings.yandex_stt_base_url.rstrip("/")


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
        f"Yandex SpeechKit STT failed: status={response.status_code}, body={body}",
        response.status_code,
    )
