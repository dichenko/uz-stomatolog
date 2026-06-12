import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm.crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.llm.manager import LlmProviderError, complete_text_with_fallback
from app.llm.repository import (
    LlmProviderConfigError,
    ensure_llm_provider_defaults,
    get_provider_config,
    update_provider_configs,
)


def test_api_key_masking_and_encryption_roundtrip():
    settings = Settings(llm_config_encryption_key=SecretStr("unit-test-key"))
    encrypted = encrypt_api_key("sk-ant-test-secret", settings)

    assert encrypted != "sk-ant-test-secret"
    assert decrypt_api_key(encrypted, settings) == "sk-ant-test-secret"
    assert mask_api_key("sk-ant-test-secret") == "sk-an...-secret"
    assert mask_api_key("short") == "******"


async def test_provider_defaults_import_anthropic_key_once(session):
    settings = Settings(
        llm_config_encryption_key=SecretStr("unit-test-key"),
        anthropic_api_key=SecretStr("sk-ant-imported"),
    )

    await ensure_llm_provider_defaults(session, settings=settings)
    config = await get_provider_config(session, "anthropic")

    assert config is not None
    assert config.enabled is True
    assert config.priority == 1
    assert config.api_key_masked == "sk-an...mported"
    assert config.api_key_encrypted is not None
    assert decrypt_api_key(config.api_key_encrypted, settings) == "sk-ant-imported"


async def test_duplicate_priority_validation(session):
    settings = Settings(
        llm_config_encryption_key=SecretStr("unit-test-key"),
        anthropic_api_key=SecretStr("sk-ant-imported"),
    )
    await ensure_llm_provider_defaults(session, settings=settings)

    with pytest.raises(LlmProviderConfigError):
        await update_provider_configs(
            session,
            [
                {
                    "providerCode": "anthropic",
                    "enabled": True,
                    "priority": 1,
                    "selectedModelId": "claude-sonnet-4-6",
                },
                {
                    "providerCode": "openai",
                    "enabled": True,
                    "priority": 1,
                    "selectedModelId": "gpt-5.4-mini",
                    "apiKey": "sk-openai-test",
                },
                {
                    "providerCode": "mistral",
                    "enabled": False,
                    "priority": 3,
                    "selectedModelId": "mistral-medium-3-5",
                },
            ],
            admin_tg_id="1",
            settings=settings,
        )


async def test_empty_api_key_does_not_overwrite_existing_key(session):
    settings = Settings(
        llm_config_encryption_key=SecretStr("unit-test-key"),
        anthropic_api_key=SecretStr("sk-ant-original"),
    )
    await ensure_llm_provider_defaults(session, settings=settings)
    before = await get_provider_config(session, "anthropic")
    assert before is not None
    encrypted_before = before.api_key_encrypted

    await update_provider_configs(
        session,
        [
            {
                "providerCode": "anthropic",
                "enabled": True,
                "priority": 1,
                "selectedModelId": "claude-haiku-4-5",
                "apiKey": "",
            },
            {
                "providerCode": "openai",
                "enabled": False,
                "priority": 2,
                "selectedModelId": "gpt-5.4-mini",
            },
            {
                "providerCode": "mistral",
                "enabled": False,
                "priority": 3,
                "selectedModelId": "mistral-medium-3-5",
            },
        ],
        admin_tg_id="1",
        settings=settings,
    )
    after = await get_provider_config(session, "anthropic")

    assert after is not None
    assert after.api_key_encrypted == encrypted_before
    assert after.selected_model_id == "claude-haiku-4-5"


async def test_complete_text_falls_back_for_retryable_provider_error(
    session,
    monkeypatch,
):
    settings = Settings(
        llm_config_encryption_key=SecretStr("unit-test-key"),
        anthropic_api_key=SecretStr("sk-ant-original"),
    )
    await ensure_llm_provider_defaults(session, settings=settings)
    await update_provider_configs(
        session,
        [
            {
                "providerCode": "anthropic",
                "enabled": True,
                "priority": 1,
                "selectedModelId": "claude-sonnet-4-6",
                "apiKey": "",
            },
            {
                "providerCode": "openai",
                "enabled": True,
                "priority": 2,
                "selectedModelId": "gpt-5.4-mini",
                "apiKey": "sk-openai-test",
            },
            {
                "providerCode": "mistral",
                "enabled": False,
                "priority": 3,
                "selectedModelId": "mistral-medium-3-5",
            },
        ],
        admin_tg_id="1",
        settings=settings,
    )
    calls = []

    async def fake_call_provider(config, **_kwargs):
        calls.append(config.provider_code)
        if config.provider_code == "anthropic":
            raise LlmProviderError(
                "rate limited",
                code="http_429",
                fallback_eligible=True,
            )
        return type(
            "Response",
            (),
            {
                "text": "ok",
                "provider_code": config.provider_code,
                "model_id": config.model_id,
                "usage": None,
            },
        )()

    monkeypatch.setattr("app.llm.manager._call_provider", fake_call_provider)

    result = await complete_text_with_fallback(
        session=session,
        settings=settings,
        messages=[{"role": "user", "content": "hello"}],
    )

    assert result == "ok"
    assert calls == ["anthropic", "openai"]


async def test_complete_text_does_not_fallback_for_internal_request_error(
    session,
    monkeypatch,
):
    settings = Settings(
        llm_config_encryption_key=SecretStr("unit-test-key"),
        anthropic_api_key=SecretStr("sk-ant-original"),
    )
    await ensure_llm_provider_defaults(session, settings=settings)
    calls = []

    async def fake_call_provider(config, **_kwargs):
        calls.append(config.provider_code)
        raise LlmProviderError(
            "bad schema",
            code="invalid_request",
            fallback_eligible=False,
        )

    monkeypatch.setattr("app.llm.manager._call_provider", fake_call_provider)

    with pytest.raises(LlmProviderError):
        await complete_text_with_fallback(
            session=session,
            settings=settings,
            messages=[],
        )

    assert calls == ["anthropic"]
