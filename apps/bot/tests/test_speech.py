from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.speech import MockSpeechProvider, create_speech_providers
from app.speech.base import SpeechProviderError
from app.speech.openai_provider import OpenAISpeechProvider
from app.speech.temp_files import (
    cleanup_temp_file,
    create_temp_audio_path,
    validate_file_size,
)


def test_speech_factory_routes_languages_to_expected_providers():
    providers = create_speech_providers(Settings())

    assert providers.stt_for_language("uz") is providers.muxlisa
    assert providers.tts_for_language("uz") is providers.muxlisa
    assert providers.stt_for_language("ru") is providers.yandex
    assert providers.tts_for_language("ru") is providers.openai
    assert providers.stt_for_language("en") is providers.openai
    assert providers.tts_for_language("en") is providers.openai


async def test_mock_speech_provider_supports_voice_pipeline(monkeypatch):
    test_dir = _make_test_dir()
    monkeypatch.setattr(
        "app.speech.temp_files.get_settings",
        lambda: SimpleNamespace(speech_temp_dir=str(test_dir)),
    )

    provider = MockSpeechProvider(transcription="How much does cleaning cost?")
    input_path = test_dir / "voice.ogg"
    input_path.write_bytes(b"voice")

    stt_result = await provider.transcribe(str(input_path), "en")
    tts_result = await provider.synthesize("Cleaning costs 350,000 UZS.", "en")

    assert stt_result.text == "How much does cleaning cost?"
    assert stt_result.provider == "mock"
    assert tts_result.provider == "mock"
    assert tts_result.mime_type == "audio/mpeg"

    await cleanup_temp_file(tts_result.file_path, reason="test_cleanup")
    assert not Path(tts_result.file_path).exists()
    input_path.unlink(missing_ok=True)
    test_dir.rmdir()


async def test_temp_audio_file_cleanup_removes_file(monkeypatch):
    test_dir = _make_test_dir()
    monkeypatch.setattr(
        "app.speech.temp_files.get_settings",
        lambda: SimpleNamespace(speech_temp_dir=str(test_dir)),
    )

    path = create_temp_audio_path(suffix=".ogg")
    path.write_bytes(b"voice")

    assert path.exists()
    validate_file_size(path, max_size_mb=1)
    await cleanup_temp_file(path, reason="test_cleanup")

    assert not path.exists()
    test_dir.rmdir()


def _make_test_dir() -> Path:
    path = Path(__file__).parent / "_speech_tmp" / uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    return path


async def test_openai_tts_rejects_too_long_text_before_api_call():
    provider = OpenAISpeechProvider(Settings(openai_tts_max_chars=5))

    with pytest.raises(SpeechProviderError):
        await provider.synthesize("too long", "en")


async def test_yandex_stt_posts_audio_to_speechkit(monkeypatch):
    test_dir = _make_test_dir()
    input_path = test_dir / "voice.ogg"
    input_path.write_bytes(b"voice-bytes")
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"result": "Здравствуйте"}

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, params, headers, content):
            calls.append(
                {
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "content": content,
                    "timeout": self.timeout,
                }
            )
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.yandex_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    providers = create_speech_providers(
        Settings(
            yandex_speechkit_api_key="test-key",
            yandex_stt_timeout_ms=12000,
        )
    )
    result = await providers.yandex.transcribe(str(input_path), "ru")

    assert result.text == "Здравствуйте"
    assert result.provider == "yandex"
    assert calls == [
        {
            "url": "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize",
            "params": {
                "lang": "ru-RU",
                "format": "oggopus",
                "topic": "general",
            },
            "headers": {"Authorization": "Api-Key test-key"},
            "content": b"voice-bytes",
            "timeout": 12,
        }
    ]

    input_path.unlink()
    test_dir.rmdir()
