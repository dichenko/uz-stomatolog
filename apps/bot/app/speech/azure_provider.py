import logging
import re
import time
from html import escape
from typing import Any

import httpx

from app.config import Settings
from app.speech.base import AudioFormat, SpeechProviderError, TextToSpeechResult
from app.speech.temp_files import create_temp_audio_path

logger = logging.getLogger(__name__)

AZURE_TTS_MODEL = "azure-speech-tts-rest"


class AzureSpeechProvider:
    _config_logged = False

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        self._log_config_once()
        prepared_text = _prepare_text_for_tts(text)
        if not prepared_text:
            raise SpeechProviderError("Azure Speech TTS input is empty")
        if len(prepared_text) > self.settings.azure_tts_max_chars:
            max_chars = self.settings.azure_tts_max_chars
            truncated_text = prepared_text[:max_chars]
            prepared_text = truncated_text.rsplit(" ", 1)[0] or truncated_text

        started_at = time.perf_counter()
        audio_bytes = await self._synthesize_once(prepared_text)
        output_format = self.settings.azure_tts_output_format
        output_path = create_temp_audio_path(suffix=_file_suffix(output_format))
        output_path.write_bytes(audio_bytes)
        logger.info(
            "speech_provider_call_succeeded",
            extra={
                "provider": "azure",
                "operation": "tts",
                "model": AZURE_TTS_MODEL,
                "voice": self.settings.azure_tts_voice,
                "language": language,
                "output_format": output_format,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "file_size_bytes": output_path.stat().st_size,
            },
        )
        return TextToSpeechResult(
            file_path=str(output_path),
            mime_type=_mime_type(output_format),
            format=_audio_format(output_format),
            provider="azure",
            model=AZURE_TTS_MODEL,
            voice=self.settings.azure_tts_voice,
        )

    async def _synthesize_once(self, text: str) -> bytes:
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key_or_raise(),
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": self.settings.azure_tts_output_format,
            "User-Agent": "uz-stomatolog-bot",
        }
        timeout = self.settings.azure_tts_timeout_ms / 1000
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self._endpoint(),
                headers=headers,
                content=self._ssml(text),
            )
        if response.status_code >= 400:
            raise self._azure_error(response)
        return response.content

    def _api_key_or_raise(self) -> str:
        if self.settings.azure_speech_key is None:
            raise SpeechProviderError("AZURE_SPEECH_KEY is required for Azure Speech")
        api_key = self.settings.azure_speech_key.get_secret_value().strip()
        if not api_key:
            raise SpeechProviderError("AZURE_SPEECH_KEY is required for Azure Speech")
        return api_key

    def _endpoint(self) -> str:
        endpoint = self.settings.azure_speech_endpoint.strip()
        if endpoint:
            return endpoint
        region = self.settings.azure_speech_region.strip()
        if not region:
            raise SpeechProviderError(
                "AZURE_SPEECH_ENDPOINT or AZURE_SPEECH_REGION is required "
                "for Azure Speech"
            )
        return f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    def _ssml(self, text: str) -> str:
        prosody_attributes = _prosody_attributes(
            rate=self.settings.azure_tts_rate,
            pitch=self.settings.azure_tts_pitch,
            range_=self.settings.azure_tts_range,
        )
        escaped_text = escape(text)
        body = (
            f"<prosody {prosody_attributes}>{escaped_text}</prosody>"
            if prosody_attributes
            else escaped_text
        )
        language = escape(self.settings.azure_tts_language, quote=True)
        voice = escape(self.settings.azure_tts_voice, quote=True)
        return (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xml:lang="{language}">'
            f'<voice name="{voice}">{body}</voice>'
            "</speak>"
        )

    def _log_config_once(self) -> None:
        if AzureSpeechProvider._config_logged:
            return
        AzureSpeechProvider._config_logged = True
        logger.info(
            "azure_tts_config",
            extra={
                "region": self.settings.azure_speech_region,
                "endpoint": self._endpoint(),
                "voice": self.settings.azure_tts_voice,
                "language": self.settings.azure_tts_language,
                "output_format": self.settings.azure_tts_output_format,
                "rate": self.settings.azure_tts_rate,
                "pitch": self.settings.azure_tts_pitch,
                "range": self.settings.azure_tts_range,
            },
        )

    def _azure_error(self, response: httpx.Response) -> "AzureSpeechStatusError":
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body: Any = response.json()
            except ValueError:
                body = response.text
        else:
            body = response.text
        logger.error(
            "azure_tts_failed",
            extra={
                "status_code": response.status_code,
                "body": str(body)[:1000],
                "voice": self.settings.azure_tts_voice,
                "language": self.settings.azure_tts_language,
                "output_format": self.settings.azure_tts_output_format,
                "rate": self.settings.azure_tts_rate,
                "pitch": self.settings.azure_tts_pitch,
                "range": self.settings.azure_tts_range,
            },
        )
        return AzureSpeechStatusError(
            f"Azure Speech TTS failed: status={response.status_code}, body={body}",
            response.status_code,
        )


class AzureSpeechStatusError(RuntimeError):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _prosody_attributes(*, rate: str, pitch: str, range_: str) -> str:
    attributes = []
    for name, value in (
        ("rate", rate.strip()),
        ("pitch", pitch.strip()),
        ("range", range_.strip()),
    ):
        if value:
            attributes.append(f'{name}="{escape(value, quote=True)}"')
    return " ".join(attributes)


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
    prepared = prepared.replace("\u20bd", " \u0440\u0443\u0431\u043b\u0435\u0439")
    dollars = " \u0434\u043e\u043b\u043b\u0430\u0440\u043e\u0432"
    percent = " \u043f\u0440\u043e\u0446\u0435\u043d\u0442\u043e\u0432"
    prepared = prepared.replace("$", dollars)
    prepared = prepared.replace("%", percent)
    prepared = re.sub(r"\s+", " ", prepared)
    return prepared.strip()


def _file_suffix(output_format: str) -> str:
    normalized = output_format.lower()
    if "ogg" in normalized or "opus" in normalized:
        return ".ogg"
    if "mp3" in normalized:
        return ".mp3"
    if "wav" in normalized:
        return ".wav"
    return ".audio"


def _mime_type(output_format: str) -> str:
    normalized = output_format.lower()
    if "ogg" in normalized or "opus" in normalized:
        return "audio/ogg"
    if "mp3" in normalized:
        return "audio/mpeg"
    if "wav" in normalized:
        return "audio/wav"
    return "application/octet-stream"


def _audio_format(output_format: str) -> AudioFormat:
    normalized = output_format.lower()
    if "opus" in normalized:
        return "opus"
    if "mp3" in normalized:
        return "mp3"
    if "wav" in normalized:
        return "wav"
    return "opus"
