from dataclasses import dataclass

from app.config import Settings, get_settings
from app.speech.aisha_provider import AishaSpeechProvider
from app.speech.azure_provider import AzureSpeechProvider
from app.speech.base import SpeechToTextProvider, TextToSpeechProvider
from app.speech.openai_provider import OpenAISpeechProvider
from app.speech.yandex_provider import YandexSpeechKitProvider
from app.telegram.texts import normalize_language


@dataclass(frozen=True)
class SpeechProviders:
    openai: OpenAISpeechProvider
    aisha: AishaSpeechProvider
    azure: AzureSpeechProvider
    yandex: YandexSpeechKitProvider

    def stt_for_language(self, language: str) -> SpeechToTextProvider:
        normalized_language = normalize_language(language)
        if normalized_language == "uz":
            return self.aisha
        return self.openai

    def tts_for_language(self, language: str) -> TextToSpeechProvider:
        normalized_language = normalize_language(language)
        if normalized_language == "uz":
            return self.aisha
        if normalized_language == "ru":
            return self.yandex
        return self.openai


def create_speech_providers(settings: Settings | None = None) -> SpeechProviders:
    resolved_settings = settings or get_settings()
    return SpeechProviders(
        openai=OpenAISpeechProvider(resolved_settings),
        aisha=AishaSpeechProvider(resolved_settings),
        azure=AzureSpeechProvider(resolved_settings),
        yandex=YandexSpeechKitProvider(resolved_settings),
    )
