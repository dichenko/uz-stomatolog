from dataclasses import dataclass
from typing import Any, Literal, Protocol

SpeechProviderName = Literal["openai", "aisha", "yandex", "azure", "mock"]
AudioFormat = Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]


class SpeechProviderError(RuntimeError):
    """Raised when an STT/TTS provider cannot complete an operation safely."""


@dataclass(frozen=True)
class SpeechToTextResult:
    text: str
    provider: SpeechProviderName
    model: str
    language: str | None = None
    raw: Any | None = None


@dataclass(frozen=True)
class TextToSpeechResult:
    file_path: str
    mime_type: str
    format: AudioFormat
    provider: SpeechProviderName
    model: str
    voice: str | None = None


class SpeechToTextProvider(Protocol):
    async def transcribe(self, file_path: str, language: str) -> SpeechToTextResult:
        ...


class TextToSpeechProvider(Protocol):
    async def synthesize(
        self, text: str, language: str, instructions: str | None = None
    ) -> TextToSpeechResult:
        ...
