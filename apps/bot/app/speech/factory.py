from dataclasses import dataclass

from app.config import Settings, get_settings
from app.speech.base import SpeechToTextProvider, TextToSpeechProvider
from app.speech.muxlisa_provider import MuxlisaSpeechProvider
from app.speech.openai_provider import OpenAISpeechProvider
from app.telegram.texts import normalize_language


@dataclass(frozen=True)
class SpeechProviders:
    openai: OpenAISpeechProvider
    muxlisa: MuxlisaSpeechProvider

    def stt_for_language(self, language: str) -> SpeechToTextProvider:
        return self.muxlisa if normalize_language(language) == "uz" else self.openai

    def tts_for_language(self, language: str) -> TextToSpeechProvider:
        return self.muxlisa if normalize_language(language) == "uz" else self.openai


def create_speech_providers(settings: Settings | None = None) -> SpeechProviders:
    resolved_settings = settings or get_settings()
    return SpeechProviders(
        openai=OpenAISpeechProvider(resolved_settings),
        muxlisa=MuxlisaSpeechProvider(resolved_settings),
    )
