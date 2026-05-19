import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.speech.base import SpeechProviderError, SpeechToTextResult, TextToSpeechResult
from app.speech.temp_files import (
    cleanup_temp_file,
    convert_to_wav,
    create_temp_audio_path,
    validate_file_size,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class MuxlisaSpeechProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(self, file_path: str, language: str) -> SpeechToTextResult:
        validate_file_size(
            file_path,
            max_size_mb=self.settings.muxlisa_max_audio_size_mb,
        )
        wav_path: Path | None = None
        input_path = Path(file_path)
        if input_path.suffix.casefold() != ".wav":
            wav_path = await convert_to_wav(input_path)
            validate_file_size(
                wav_path,
                max_size_mb=self.settings.muxlisa_max_audio_size_mb,
            )
            stt_path = wav_path
        else:
            stt_path = input_path

        try:
            started_at = time.perf_counter()
            result = await self._retry(
                operation="stt",
                call=lambda: self._transcribe_once(stt_path),
            )
            logger.info(
                "speech_provider_call_succeeded",
                extra={
                    "provider": "muxlisa",
                    "operation": "stt",
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                    "file_size_bytes": stt_path.stat().st_size,
                },
            )
            return SpeechToTextResult(
                text=(result.get("text") or result.get("transcription") or "").strip(),
                provider="muxlisa",
                model="muxlisa-stt",
                language=language,
                raw=result,
            )
        finally:
            await cleanup_temp_file(wav_path, reason="muxlisa_converted_wav_cleanup")

    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        if len(text) > self.settings.muxlisa_tts_max_chars:
            max_chars = self.settings.muxlisa_tts_max_chars
            text = text[:max_chars].rsplit(" ", 1)[0] or text[:max_chars]

        started_at = time.perf_counter()
        audio_bytes = await self._retry(
            operation="tts",
            call=lambda: self._synthesize_once(text),
        )
        output_path = create_temp_audio_path(suffix=".wav")
        output_path.write_bytes(audio_bytes)
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "muxlisa",
                "operation": "tts",
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": output_path.stat().st_size,
            },
        )
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type="audio/wav",
            format="wav",
            provider="muxlisa",
            model="muxlisa-tts",
            voice=str(self.settings.muxlisa_tts_speaker),
        )

    async def _transcribe_once(self, file_path: Path) -> dict[str, Any]:
        headers = {"x-api-key": self._api_key_or_raise()}
        timeout = self.settings.muxlisa_stt_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            with file_path.open("rb") as audio_file:
                response = await client.post(
                    f"{self._base_url_or_raise()}/api/v2/stt",
                    headers=headers,
                    files={"audio": ("audio.wav", audio_file, "audio/wav")},
                )
        if response.status_code >= 400:
            raise _muxlisa_error("stt", response)
        return response.json()

    async def _synthesize_once(self, text: str) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key_or_raise(),
        }
        timeout = self.settings.muxlisa_tts_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self._base_url_or_raise()}/api/v2/tts",
                headers=headers,
                json={"text": text, "speaker": self.settings.muxlisa_tts_speaker},
            )
        if response.status_code >= 400:
            raise _muxlisa_error("tts", response)
        return response.content

    async def _retry(self, *, operation: str, call: Any) -> Any:
        delays = (0, 2, 5)
        last_error: Exception | None = None
        for attempt, delay_seconds in enumerate(delays, start=1):
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            try:
                return await call()
            except MuxlisaStatusError as exc:
                if exc.status_code not in RETRYABLE_STATUS_CODES:
                    raise SpeechProviderError(
                        f"Muxlisa {operation} failed with status {exc.status_code}"
                    ) from exc
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_status",
                    extra={
                        "provider": "muxlisa",
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
                        "provider": "muxlisa",
                        "operation": operation,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    },
                )

        raise SpeechProviderError(
            f"Muxlisa {operation} failed after retries"
        ) from last_error

    def _api_key_or_raise(self) -> str:
        if self.settings.muxlisa_api_key is None:
            raise SpeechProviderError("MUXLISA_API_KEY is required for Muxlisa speech")
        api_key = self.settings.muxlisa_api_key.get_secret_value().strip()
        if not api_key:
            raise SpeechProviderError("MUXLISA_API_KEY is required for Muxlisa speech")
        return api_key

    def _base_url_or_raise(self) -> str:
        if not self.settings.muxlisa_base_url:
            raise SpeechProviderError("MUXLISA_BASE_URL is required for Muxlisa speech")
        return self.settings.muxlisa_base_url.rstrip("/")


class MuxlisaStatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _muxlisa_error(operation: str, response: httpx.Response) -> MuxlisaStatusError:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        body: Any = response.json()
    else:
        body = response.text
    return MuxlisaStatusError(
        f"Muxlisa {operation} failed: status={response.status_code}, body={body}",
        response.status_code,
    )
