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


def test_speech_factory_routes_uzbek_to_muxlisa_and_other_languages_to_openai():
    providers = create_speech_providers(Settings())

    assert providers.stt_for_language("uz") is providers.muxlisa
    assert providers.tts_for_language("uz") is providers.muxlisa
    assert providers.stt_for_language("ru") is providers.openai
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
