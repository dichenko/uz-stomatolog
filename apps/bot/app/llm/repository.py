import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.models import LlmModelCatalog, LlmProviderCallLog, LlmProviderConfig
from app.llm.crypto import (
    decrypt_api_key,
    encrypt_api_key,
    fingerprint_api_key,
    mask_api_key,
)

logger = logging.getLogger(__name__)

ProviderCode = Literal["openai", "anthropic", "mistral"]

MODEL_CATALOG: list[dict[str, Any]] = [
    {
        "provider_code": "openai",
        "model_id": "gpt-5.5",
        "display_name": "GPT-5.5",
        "description": (
            "Highest-quality default for complex reasoning, coding, "
            "and agentic work"
        ),
        "availability_note": None,
        "sort_order": 10,
    },
    {
        "provider_code": "openai",
        "model_id": "gpt-5.4",
        "display_name": "GPT-5.4",
        "description": "Strong model, cheaper than GPT-5.5",
        "availability_note": None,
        "sort_order": 20,
    },
    {
        "provider_code": "openai",
        "model_id": "gpt-5.4-mini",
        "display_name": "GPT-5.4 mini",
        "description": "Strong lower-latency model for production workloads",
        "availability_note": None,
        "sort_order": 30,
    },
    {
        "provider_code": "openai",
        "model_id": "gpt-5.4-nano",
        "display_name": "GPT-5.4 nano",
        "description": "Cheap, fast, high-volume tasks and sub-agents",
        "availability_note": None,
        "sort_order": 40,
    },
    {
        "provider_code": "openai",
        "model_id": "gpt-5-nano",
        "display_name": "GPT-5 nano",
        "description": "Very cheap fallback option for simple tasks",
        "availability_note": None,
        "sort_order": 50,
    },
    {
        "provider_code": "anthropic",
        "model_id": "claude-fable-5",
        "display_name": "Claude Fable 5",
        "description": "Strongest widely released Anthropic model",
        "availability_note": None,
        "sort_order": 10,
    },
    {
        "provider_code": "anthropic",
        "model_id": "claude-opus-4-8",
        "display_name": "Claude Opus 4.8",
        "description": (
            "Complex reasoning, long-horizon agentic coding, "
            "high-autonomy work"
        ),
        "availability_note": None,
        "sort_order": 20,
    },
    {
        "provider_code": "anthropic",
        "model_id": "claude-sonnet-4-6",
        "display_name": "Claude Sonnet 4.6",
        "description": "Strong balance of intelligence, speed, and cost",
        "availability_note": None,
        "sort_order": 30,
    },
    {
        "provider_code": "anthropic",
        "model_id": "claude-haiku-4-5",
        "display_name": "Claude Haiku 4.5",
        "description": "Fast and cheaper model with strong reasoning",
        "availability_note": None,
        "sort_order": 40,
    },
    {
        "provider_code": "anthropic",
        "model_id": "claude-mythos-5",
        "display_name": "Claude Mythos 5",
        "description": "Limited-availability Anthropic model",
        "availability_note": "Limited access",
        "sort_order": 50,
    },
    {
        "provider_code": "mistral",
        "model_id": "mistral-medium-3-5",
        "display_name": "Mistral Medium 3.5",
        "description": (
            "Frontier-class multimodal model optimized for agentic "
            "and coding use cases"
        ),
        "availability_note": None,
        "sort_order": 10,
    },
    {
        "provider_code": "mistral",
        "model_id": "mistral-small-2603",
        "display_name": "Mistral Small 4",
        "description": (
            "Hybrid instruct/reasoning/coding model with good "
            "cost/performance"
        ),
        "availability_note": None,
        "sort_order": 20,
    },
    {
        "provider_code": "mistral",
        "model_id": "mistral-large-2512",
        "display_name": "Mistral Large 3",
        "description": "Strong general-purpose multimodal model",
        "availability_note": None,
        "sort_order": 30,
    },
    {
        "provider_code": "mistral",
        "model_id": "mistral-medium-2508",
        "display_name": "Mistral Medium 3.1",
        "description": "Strong general multimodal model",
        "availability_note": None,
        "sort_order": 40,
    },
    {
        "provider_code": "mistral",
        "model_id": "magistral-medium-2509",
        "display_name": "Magistral Medium 1.2",
        "description": "Mistral reasoning model",
        "availability_note": None,
        "sort_order": 50,
    },
]

PROVIDER_DEFAULTS = [
    {
        "provider_code": "anthropic",
        "display_name": "Anthropic",
        "enabled": True,
        "priority": 1,
        "selected_model_id": "claude-sonnet-4-6",
    },
    {
        "provider_code": "openai",
        "display_name": "OpenAI",
        "enabled": False,
        "priority": 2,
        "selected_model_id": "gpt-5.4-mini",
    },
    {
        "provider_code": "mistral",
        "display_name": "Mistral",
        "enabled": False,
        "priority": 3,
        "selected_model_id": "mistral-medium-3-5",
    },
]


@dataclass(frozen=True)
class RuntimeProviderConfig:
    provider_code: ProviderCode
    display_name: str
    priority: int
    model_id: str
    api_key: str


class LlmProviderConfigError(ValueError):
    pass


async def ensure_llm_provider_defaults(
    session: AsyncSession,
    settings: Settings | None = None,
) -> None:
    await seed_model_catalog(session)
    await seed_provider_configs(session)
    await import_anthropic_key_once(session, settings=settings)


async def seed_model_catalog(session: AsyncSession) -> None:
    for row in MODEL_CATALOG:
        existing = await _get_model(session, row["provider_code"], row["model_id"])
        if existing is not None:
            continue
        session.add(LlmModelCatalog(**row, is_active=True))


async def seed_provider_configs(session: AsyncSession) -> None:
    for row in PROVIDER_DEFAULTS:
        existing = await get_provider_config(session, row["provider_code"])
        if existing is not None:
            continue
        session.add(LlmProviderConfig(**row))


async def import_anthropic_key_once(
    session: AsyncSession,
    settings: Settings | None = None,
) -> bool:
    resolved = settings or get_settings()
    config = await get_provider_config(session, "anthropic")
    if config is None or config.api_key_encrypted:
        return False

    secret = resolved.anthropic_api_key or resolved.claude_api_key
    if secret is None:
        return False

    api_key = secret.get_secret_value().strip()
    if not api_key:
        return False

    config.enabled = True
    config.priority = 1
    config.api_key_encrypted = encrypt_api_key(api_key, resolved)
    config.api_key_masked = mask_api_key(api_key)
    config.api_key_fingerprint = fingerprint_api_key(api_key)
    config.last_status = "unknown"
    logger.info(
        "llm_anthropic_key_imported",
        extra={"provider_code": "anthropic", "api_key_masked": config.api_key_masked},
    )
    return True


async def get_model_catalog(session: AsyncSession) -> list[LlmModelCatalog]:
    result = await session.execute(
        select(LlmModelCatalog)
        .where(LlmModelCatalog.is_active.is_(True))
        .order_by(LlmModelCatalog.provider_code, LlmModelCatalog.sort_order)
    )
    return list(result.scalars().all())


async def get_provider_config(
    session: AsyncSession,
    provider_code: str,
) -> LlmProviderConfig | None:
    result = await session.execute(
        select(LlmProviderConfig).where(
            LlmProviderConfig.provider_code == provider_code
        )
    )
    return result.scalar_one_or_none()


async def get_provider_configs(session: AsyncSession) -> list[LlmProviderConfig]:
    result = await session.execute(
        select(LlmProviderConfig).order_by(LlmProviderConfig.provider_code)
    )
    return list(result.scalars().all())


async def get_runtime_provider_configs(
    session: AsyncSession,
    settings: Settings | None = None,
) -> list[RuntimeProviderConfig]:
    await ensure_llm_provider_defaults(session, settings=settings)
    result = await session.execute(
        select(LlmProviderConfig)
        .where(LlmProviderConfig.enabled.is_(True))
        .order_by(LlmProviderConfig.priority)
    )
    configs = list(result.scalars().all())
    runtime: list[RuntimeProviderConfig] = []
    for config in configs:
        if (
            config.priority is None
            or not config.selected_model_id
            or not config.api_key_encrypted
        ):
            continue
        runtime.append(
            RuntimeProviderConfig(
                provider_code=config.provider_code,  # type: ignore[arg-type]
                display_name=config.display_name,
                priority=config.priority,
                model_id=config.selected_model_id,
                api_key=decrypt_api_key(config.api_key_encrypted, settings),
            )
        )
    return runtime


async def get_runtime_provider_config(
    session: AsyncSession,
    provider_code: str,
    settings: Settings | None = None,
) -> RuntimeProviderConfig:
    await ensure_llm_provider_defaults(session, settings=settings)
    config = await get_provider_config(session, provider_code)
    if config is None:
        raise LlmProviderConfigError(f"Unknown provider: {provider_code}")
    if not config.selected_model_id:
        raise LlmProviderConfigError("Provider has no selected model")
    if not config.api_key_encrypted:
        raise LlmProviderConfigError("Provider has no saved API key")
    return RuntimeProviderConfig(
        provider_code=config.provider_code,  # type: ignore[arg-type]
        display_name=config.display_name,
        priority=config.priority or 0,
        model_id=config.selected_model_id,
        api_key=decrypt_api_key(config.api_key_encrypted, settings),
    )


async def update_provider_configs(
    session: AsyncSession,
    updates: list[dict[str, Any]],
    *,
    admin_tg_id: str,
    settings: Settings | None = None,
) -> list[LlmProviderConfig]:
    existing = {
        config.provider_code: config
        for config in await get_provider_configs(session)
    }
    catalog = await get_model_catalog(session)
    active_models = {
        (model.provider_code, model.model_id)
        for model in catalog
        if model.is_active
    }

    for update in updates:
        provider_code = str(
            update.get("providerCode") or update.get("provider_code") or ""
        )
        config = existing.get(provider_code)
        if config is None:
            raise LlmProviderConfigError(f"Unknown provider: {provider_code}")

        enabled = bool(update.get("enabled"))
        priority = _coerce_priority(update.get("priority"))
        model_id = str(
            update.get("selectedModelId")
            or update.get("selected_model_id")
            or ""
        ).strip()
        new_api_key = str(update.get("apiKey") or update.get("api_key") or "").strip()

        if model_id and (provider_code, model_id) not in active_models:
            raise LlmProviderConfigError(f"Invalid model for {provider_code}")

        config.enabled = enabled
        config.priority = priority
        config.selected_model_id = model_id or None
        config.updated_by_admin_id = admin_tg_id

        if new_api_key:
            config.api_key_encrypted = encrypt_api_key(new_api_key, settings)
            config.api_key_masked = mask_api_key(new_api_key)
            config.api_key_fingerprint = fingerprint_api_key(new_api_key)
            config.last_status = "unknown"
            config.last_error_code = None
            config.last_error_message = None

    _validate_provider_configs(list(existing.values()))
    return await get_provider_configs(session)


def serialize_provider_configs(
    configs: list[LlmProviderConfig],
    catalog: list[LlmModelCatalog],
) -> dict[str, Any]:
    models_by_provider: dict[str, list[dict[str, Any]]] = {}
    for model in catalog:
        models_by_provider.setdefault(model.provider_code, []).append(
            {
                "providerCode": model.provider_code,
                "modelId": model.model_id,
                "displayName": model.display_name,
                "description": model.description,
                "availabilityNote": model.availability_note,
                "isActive": model.is_active,
                "sortOrder": model.sort_order,
            }
        )

    return {
        "providers": [
            {
                "providerCode": config.provider_code,
                "displayName": config.display_name,
                "enabled": config.enabled,
                "priority": config.priority,
                "selectedModelId": config.selected_model_id,
                "apiKeyMasked": config.api_key_masked,
                "hasApiKey": bool(config.api_key_encrypted),
                "lastStatus": config.last_status or "unknown",
                "lastTestedAt": _iso(config.last_tested_at),
                "lastSuccessAt": _iso(config.last_success_at),
                "lastFailureAt": _iso(config.last_failure_at),
                "lastErrorCode": config.last_error_code,
                "lastErrorMessage": config.last_error_message,
                "models": models_by_provider.get(config.provider_code, []),
            }
            for config in sorted(configs, key=lambda c: c.provider_code)
        ]
    }


async def record_provider_call(
    session: AsyncSession | None,
    *,
    request_id: str | None,
    telegram_user_id: int | None,
    provider_code: str,
    model_id: str | None,
    priority: int | None,
    status: str,
    error_type: str | None = None,
    error_message_sanitized: str | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    fallback_attempt_number: int | None = None,
    was_fallback: bool = False,
    tool_executed_before_failure: bool = False,
) -> None:
    if session is None:
        return
    session.add(
        LlmProviderCallLog(
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            provider_code=provider_code,
            model_id=model_id,
            priority=priority,
            status=status,
            error_type=error_type,
            error_message_sanitized=_sanitize_error(error_message_sanitized),
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            fallback_attempt_number=fallback_attempt_number,
            was_fallback=was_fallback,
            tool_executed_before_failure=tool_executed_before_failure,
        )
    )


async def update_provider_test_status(
    session: AsyncSession,
    provider_code: str,
    *,
    ok: bool,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    config = await get_provider_config(session, provider_code)
    if config is None:
        return
    now = datetime.now(UTC)
    config.last_status = "ok" if ok else "failed"
    config.last_tested_at = now
    if ok:
        config.last_success_at = now
        config.last_error_code = None
        config.last_error_message = None
    else:
        config.last_failure_at = now
        config.last_error_code = error_code
        config.last_error_message = _sanitize_error(error_message)


def _validate_provider_configs(configs: list[LlmProviderConfig]) -> None:
    priorities: dict[int, str] = {}
    for config in configs:
        if not config.enabled:
            continue
        if config.priority not in (1, 2, 3):
            raise LlmProviderConfigError(
                "Enabled providers require priority 1, 2, or 3"
            )
        if not config.selected_model_id:
            raise LlmProviderConfigError("Enabled providers require a selected model")
        if not config.api_key_encrypted:
            raise LlmProviderConfigError("Enabled providers require an API key")
        if config.priority in priorities:
            raise LlmProviderConfigError(
                f"Duplicate priority {config.priority}: "
                f"{priorities[config.priority]} and {config.provider_code}"
            )
        priorities[config.priority] = config.provider_code


def _coerce_priority(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise LlmProviderConfigError("Priority must be 1, 2, or 3") from exc


async def _get_model(
    session: AsyncSession,
    provider_code: str,
    model_id: str,
) -> LlmModelCatalog | None:
    result = await session.execute(
        select(LlmModelCatalog).where(
            LlmModelCatalog.provider_code == provider_code,
            LlmModelCatalog.model_id == model_id,
        )
    )
    return result.scalar_one_or_none()


def _sanitize_error(message: str | None) -> str | None:
    if not message:
        return None
    sanitized = str(message).replace("\n", " ").replace("\r", " ").strip()
    return sanitized[:1000]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
