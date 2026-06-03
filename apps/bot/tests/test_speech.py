from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.speech import MockSpeechProvider, create_speech_providers
from app.speech.aisha_provider import AishaTtsProvider
from app.speech.azure_provider import AzureSpeechProvider, AzureSpeechStatusError
from app.speech.base import SpeechProviderError
from app.speech.openai_provider import OpenAISpeechProvider
from app.speech.temp_files import (
    cleanup_temp_file,
    create_temp_audio_path,
    validate_file_size,
)
from app.speech.yandex_provider import (
    YandexSpeechKitProvider,
    YandexSpeechKitStatusError,
)


def test_speech_factory_routes_languages_to_expected_providers():
    providers = create_speech_providers(Settings())

    assert providers.stt_for_language("uz") is providers.muxlisa
    assert providers.tts_for_language("uz") is providers.aisha
    assert providers.stt_for_language("ru") is providers.openai
    assert providers.tts_for_language("ru") is providers.yandex
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


async def test_aisha_tts_posts_form_and_downloads_audio(monkeypatch):
    test_dir = _make_test_dir()
    calls = []

    class FakeResponse:
        def __init__(
            self,
            *,
            status_code,
            headers=None,
            content=b"",
            text="",
            json_payload=None,
        ):
            self.status_code = status_code
            self.headers = headers or {}
            self.content = content
            self.text = text
            self._json_payload = json_payload

        def json(self):
            if self._json_payload is None:
                raise ValueError("no json")
            return self._json_payload

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, files):
            calls.append(
                {
                    "method": "POST",
                    "url": url,
                    "headers": headers,
                    "files": files,
                    "timeout": self.timeout,
                }
            )
            return FakeResponse(
                status_code=201,
                headers={"content-type": "application/json"},
                json_payload={"audio_path": "/media/tts_audios/test.wav"},
            )

        async def get(self, url, *, headers):
            calls.append({"method": "GET", "url": url, "headers": headers})
            return FakeResponse(
                status_code=200,
                headers={"content-type": "audio/wav"},
                content=b"wav-bytes",
            )

    monkeypatch.setattr(
        "app.speech.aisha_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = AishaTtsProvider(
        Settings(
            speech_temp_dir=str(test_dir),
            aisha_api_key="test-key",
            aisha_tts_timeout_ms=12000,
            aisha_tts_model="Gulnoza",
            aisha_tts_mood="Neutral",
            aisha_tts_speed=1.2,
        )
    )
    result = await provider.synthesize("**Salom** - test", "uz")

    assert Path(result.file_path).read_bytes() == b"wav-bytes"
    assert result.provider == "aisha"
    assert result.mime_type == "audio/wav"
    assert result.format == "wav"
    assert result.voice == "Gulnoza"
    assert calls == [
        {
            "method": "POST",
            "url": "https://back.aisha.group/api/v1/tts/post/",
            "headers": {"X-Api-Key": "test-key", "Accept-Language": "uz"},
            "files": {
                "transcript": (None, "Salom test"),
                "language": (None, "uz"),
                "model": (None, "Gulnoza"),
                "mood": (None, "Neutral"),
                "speed": (None, "1.2"),
            },
            "timeout": 12,
        },
        {
            "method": "GET",
            "url": "https://back.aisha.group/media/tts_audios/test.wav",
            "headers": {"X-Api-Key": "test-key", "Accept-Language": "uz"},
        },
    ]

    await cleanup_temp_file(result.file_path, reason="test_cleanup")
    test_dir.rmdir()


async def test_aisha_tts_requires_api_key():
    provider = AishaTtsProvider(Settings())

    with pytest.raises(SpeechProviderError, match="AISHA_API_KEY"):
        await provider.synthesize("Salom", "uz")


async def test_aisha_tts_logs_error_settings_without_api_key(monkeypatch, caplog):
    class FakeResponse:
        status_code = 402
        headers = {"content-type": "application/json"}
        text = '{"detail":"payment required"}'

        def json(self):
            return {"detail": "payment required"}

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, files):
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.aisha_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = AishaTtsProvider(
        Settings(
            aisha_api_key="secret-key",
            aisha_tts_model="Gulnoza",
            aisha_tts_mood="Neutral",
            aisha_tts_speed=1.0,
        )
    )

    with caplog.at_level("ERROR", logger="app.speech.aisha_provider"):
        with pytest.raises(SpeechProviderError):
            await provider.synthesize("Salom", "uz")

    record = next(item for item in caplog.records if item.message == "aisha_tts_failed")
    assert record.status_code == 402
    assert record.body == "{'detail': 'payment required'}"
    assert record.language == "uz"
    assert record.model == "Gulnoza"
    assert record.mood == "Neutral"
    assert record.speed == 1.0
    assert "secret-key" not in caplog.text


async def test_azure_tts_posts_ssml_to_speech_service(monkeypatch):
    test_dir = _make_test_dir()
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "audio/ogg"}
        content = b"ogg-opus-bytes"

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, content):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "content": content,
                    "timeout": self.timeout,
                }
            )
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.azure_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    providers = create_speech_providers(
        Settings(
            speech_temp_dir=str(test_dir),
            azure_speech_key="test-key",
            azure_speech_endpoint=(
                "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1"
            ),
            azure_tts_voice="ru-RU-SvetlanaNeural",
            azure_tts_output_format="ogg-24khz-16bit-mono-opus",
            azure_tts_rate="20%",
            azure_tts_pitch="+2st",
            azure_tts_range="+10%",
            azure_tts_timeout_ms=12000,
        )
    )
    result = await providers.azure.synthesize("**Привет** - тест", "ru")

    assert Path(result.file_path).read_bytes() == b"ogg-opus-bytes"
    assert result.provider == "azure"
    assert result.mime_type == "audio/ogg"
    assert result.format == "opus"
    assert calls == [
        {
            "url": "https://westeurope.tts.speech.microsoft.com/cognitiveservices/v1",
            "headers": {
                "Ocp-Apim-Subscription-Key": "test-key",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "ogg-24khz-16bit-mono-opus",
                "User-Agent": "uz-stomatolog-bot",
            },
            "content": (
                '<speak version="1.0" '
                'xmlns="http://www.w3.org/2001/10/synthesis" '
                'xml:lang="ru-RU"><voice name="ru-RU-SvetlanaNeural">'
                '<prosody rate="20%" pitch="+2st" range="+10%">Привет тест'
                "</prosody></voice></speak>"
            ),
            "timeout": 12,
        }
    ]

    await cleanup_temp_file(result.file_path, reason="test_cleanup")
    test_dir.rmdir()


async def test_azure_tts_logs_error_settings_without_api_key(monkeypatch, caplog):
    class FakeResponse:
        status_code = 400
        headers = {"content-type": "text/plain"}
        text = "bad voice"

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, content):
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.azure_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = AzureSpeechProvider(
        Settings(
            azure_speech_key="secret-key",
            azure_tts_voice="ru-RU-SvetlanaNeural",
            azure_tts_output_format="ogg-24khz-16bit-mono-opus",
            azure_tts_rate="20%",
            azure_tts_pitch="+2st",
            azure_tts_range="+10%",
        )
    )

    with caplog.at_level("ERROR", logger="app.speech.azure_provider"):
        with pytest.raises(AzureSpeechStatusError):
            await provider.synthesize("Hello", "ru")

    record = next(item for item in caplog.records if item.message == "azure_tts_failed")
    assert record.status_code == 400
    assert record.body == "bad voice"
    assert record.voice == "ru-RU-SvetlanaNeural"
    assert record.output_format == "ogg-24khz-16bit-mono-opus"
    assert record.rate == "20%"
    assert record.pitch == "+2st"
    assert record.range == "+10%"
    assert "secret-key" not in caplog.text


async def test_yandex_tts_posts_text_to_speechkit(monkeypatch):
    test_dir = _make_test_dir()
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "audio/ogg"}
        content = b"ogg-opus-bytes"

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, data):
            calls.append(
                {
                    "url": url,
                    "headers": headers,
                    "data": data,
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
            speech_temp_dir=str(test_dir),
            yandex_speechkit_api_key="test-key",
            yandex_tts_voice="marina",
            yandex_tts_emotion="friendly",
            yandex_tts_speed="1.4",
            yandex_tts_timeout_ms=12000,
        )
    )
    result = await providers.yandex.synthesize("**Hello** - test", "ru")

    assert Path(result.file_path).read_bytes() == b"ogg-opus-bytes"
    assert result.provider == "yandex"
    assert result.mime_type == "audio/ogg"
    assert result.format == "opus"
    assert calls == [
        {
            "url": "https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
            "headers": {"Authorization": "Api-Key test-key"},
            "data": {
                "text": "Hello test",
                "lang": "ru-RU",
                "voice": "marina",
                "emotion": "friendly",
                "speed": "1.4",
                "format": "oggopus",
            },
            "timeout": 12,
        }
    ]

    await cleanup_temp_file(result.file_path, reason="test_cleanup")
    test_dir.rmdir()


async def test_yandex_tts_uses_custom_voice_settings(monkeypatch):
    test_dir = _make_test_dir()
    calls = []

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "audio/ogg"}
        content = b"voice"

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, data):
            calls.append(data)
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.yandex_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = YandexSpeechKitProvider(
        Settings(
            speech_temp_dir=str(test_dir),
            yandex_speechkit_api_key="test-key",
            yandex_tts_voice="ermil",
            yandex_tts_emotion="good",
            yandex_tts_speed="1.2",
            yandex_tts_format="oggopus",
        )
    )
    result = await provider.synthesize("Hello", "ru")

    assert calls[0]["voice"] == "ermil"
    assert calls[0]["emotion"] == "good"
    assert calls[0]["speed"] == "1.2"
    assert calls[0]["format"] == "oggopus"

    await cleanup_temp_file(result.file_path, reason="test_cleanup")
    test_dir.rmdir()


async def test_yandex_tts_logs_error_settings_without_api_key(monkeypatch, caplog):
    class FakeResponse:
        status_code = 400
        headers = {"content-type": "text/plain"}
        text = "bad voice"

    class FakeAsyncClient:
        def __init__(self, *, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, data):
            return FakeResponse()

    monkeypatch.setattr(
        "app.speech.yandex_provider.httpx.AsyncClient",
        FakeAsyncClient,
    )

    provider = YandexSpeechKitProvider(
        Settings(
            yandex_speechkit_api_key="secret-key",
            yandex_tts_voice="alena",
            yandex_tts_emotion="good",
            yandex_tts_speed="1.15",
        )
    )

    with caplog.at_level("ERROR", logger="app.speech.yandex_provider"):
        with pytest.raises(YandexSpeechKitStatusError):
            await provider.synthesize("Hello", "ru")

    record = next(
        item for item in caplog.records if item.message == "yandex_tts_failed"
    )
    assert record.status_code == 400
    assert record.body == "bad voice"
    assert record.voice == "alena"
    assert record.emotion == "good"
    assert record.speed == "1.15"
    assert "secret-key" not in caplog.text
