from pydantic import SecretStr

from app.config import Settings
from app.services.text_llm import complete_text


async def test_complete_text_uses_claude_messages_api(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "content": [
                    {"type": "text", "text": '{"intent": "admin_faq"}'},
                ],
            }

    class FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url: str, *, headers: dict, json: dict):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr("app.services.text_llm.httpx.AsyncClient", FakeAsyncClient)

    result = await complete_text(
        settings=Settings(
            text_llm_provider="claude",
            claude_api_key=SecretStr("test-key"),
            claude_text_model="claude-sonnet-4-5-20250929",
            claude_timeout_ms=30000,
            claude_max_tokens=512,
        ),
        response_format="json_object",
        messages=[
            {"role": "system", "content": "Route intents."},
            {"role": "user", "content": "How much is cleaning?"},
        ],
    )

    assert result == '{"intent": "admin_faq"}'
    assert captured["timeout"] == 30
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["json"]["model"] == "claude-sonnet-4-5-20250929"
    assert captured["json"]["max_tokens"] == 512
    assert "Route intents." in captured["json"]["system"]
    assert "valid JSON object" in captured["json"]["system"]
    assert captured["json"]["messages"] == [
        {"role": "user", "content": "How much is cleaning?"},
    ]


async def test_complete_text_returns_none_without_claude_key():
    result = await complete_text(
        settings=Settings(text_llm_provider="claude", claude_api_key=None),
        messages=[{"role": "user", "content": "Hello"}],
    )

    assert result is None
