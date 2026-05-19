import inspect
import logging
import time
from pathlib import Path
from typing import Any

from langsmith.wrappers import wrap_openai
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from app.config import Settings
from app.speech.base import (
    AudioFormat,
    SpeechProviderError,
    SpeechToTextResult,
    TextToSpeechResult,
)
from app.speech.temp_files import create_temp_audio_path, validate_file_size

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenAISpeechProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: AsyncOpenAI | None = None

    async def transcribe(self, file_path: str, language: str) -> SpeechToTextResult:
        validate_file_size(
            file_path,
            max_size_mb=self.settings.openai_stt_max_audio_size_mb,
        )
        started_at = time.perf_counter()
        result = await self._retry(
            operation="stt",
            call=lambda: self._transcribe_once(file_path, language),
        )
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "openai",
                "operation": "stt",
                "model": self.settings.openai_stt_model,
                "language": language,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": Path(file_path).stat().st_size,
            },
        )
        return result

    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        if len(text) > self.settings.openai_tts_max_chars:
            raise SpeechProviderError(
                "OpenAI TTS input is too long: "
                f"{len(text)}/{self.settings.openai_tts_max_chars}"
            )

        started_at = time.perf_counter()
        result = await self._retry(
            operation="tts",
            call=lambda: self._synthesize_once(
                text, language, instructions
            ),
        )
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "openai",
                "operation": "tts",
                "model": self.settings.openai_tts_model,
                "voice": self.settings.openai_tts_voice,
                "response_format": self.settings.openai_tts_response_format,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
        )
        return result

    async def _transcribe_once(
        self,
        file_path: str,
        language: str,
    ) -> SpeechToTextResult:
        with Path(file_path).open("rb") as audio_file:
            result = await self._client_or_raise().audio.transcriptions.create(
                file=audio_file,
                model=self.settings.openai_stt_model,
                language=language or self.settings.openai_stt_language,
                prompt=self.settings.openai_stt_prompt or None,
                response_format=self.settings.openai_stt_response_format,
            )

        text = getattr(result, "text", None)
        if not text and isinstance(result, dict):
            text = result.get("text")
        return SpeechToTextResult(
            text=(text or "").strip(),
            provider="openai",
            model=self.settings.openai_stt_model,
            language=language,
            raw=_safe_model_dump(result),
        )

    async def _synthesize_once(
        self, text: str, _language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        response_format = self.settings.openai_tts_response_format
        resolved_instructions = instructions or self.settings.openai_tts_instructions
        response = await self._client_or_raise().audio.speech.create(
            model=self.settings.openai_tts_model,
            voice=self.settings.openai_tts_voice,
            input=text,
            instructions=resolved_instructions or "",
            response_format=response_format,
            speed=self.settings.openai_tts_speed,
        )
        audio_bytes = await _read_openai_binary_response(response)
        output_path = create_temp_audio_path(suffix=f".{response_format}")
        output_path.write_bytes(audio_bytes)
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type=_mime_type(response_format),
            format=response_format,
            provider="openai",
            model=self.settings.openai_tts_model,
            voice=self.settings.openai_tts_voice,
        )

    async def _retry(self, *, operation: str, call: Any) -> Any:
        delays = (0, 2, 5)
        last_error: Exception | None = None
        for attempt, delay_seconds in enumerate(delays, start=1):
            if delay_seconds:
                import asyncio

                await asyncio.sleep(delay_seconds)
            try:
                return await call()
            except (APIConnectionError, APITimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_error",
                    extra={
                        "provider": "openai",
                        "operation": operation,
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                    },
                )
            except APIStatusError as exc:
                status_code = exc.status_code
                if status_code not in RETRYABLE_STATUS_CODES:
                    raise SpeechProviderError(
                        f"OpenAI {operation} failed with status {status_code}"
                    ) from exc
                last_error = exc
                logger.warning(
                    "speech_provider_retryable_status",
                    extra={
                        "provider": "openai",
                        "operation": operation,
                        "attempt": attempt,
                        "status_code": status_code,
                    },
                )

        raise SpeechProviderError(
            f"OpenAI {operation} failed after retries"
        ) from last_error

    def _client_or_raise(self) -> AsyncOpenAI:
        if self._client is not None:
            return self._client

        if self.settings.openai_api_key is None:
            raise SpeechProviderError("OPENAI_API_KEY is required for OpenAI speech")
        api_key = self.settings.openai_api_key.get_secret_value().strip()
        if not api_key:
            raise SpeechProviderError("OPENAI_API_KEY is required for OpenAI speech")

        self._client = wrap_openai(AsyncOpenAI(
            api_key=api_key,
            base_url=self.settings.openai_base_url or None,
            timeout=self.settings.openai_stt_timeout_ms / 1000,
            max_retries=0,
        ))
        return self._client


async def _read_openai_binary_response(response: Any) -> bytes:
    if hasattr(response, "aread"):
        result = response.aread()
        return await result if inspect.isawaitable(result) else result
    if hasattr(response, "read"):
        result = response.read()
        return await result if inspect.isawaitable(result) else result
    if hasattr(response, "content"):
        return bytes(response.content)
    raise SpeechProviderError("OpenAI TTS response did not contain readable audio")


def _safe_model_dump(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _mime_type(format_name: AudioFormat) -> str:
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/pcm",
    }.get(format_name, "application/octet-stream")
