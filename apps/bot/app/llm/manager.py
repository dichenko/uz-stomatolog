import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from langsmith.wrappers import wrap_openai
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.session import async_session_factory
from app.llm.repository import (
    RuntimeProviderConfig,
    get_runtime_provider_configs,
    record_provider_call,
)

logger = logging.getLogger(__name__)

ChatRole = Literal["system", "user", "assistant"]
ChatMessage = dict[str, str]

SAFE_LLM_ERROR_MESSAGE = "Service is temporarily unavailable. Please try again later."


class LlmProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        fallback_eligible: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.fallback_eligible = fallback_eligible


class LlmProvidersUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmTextResponse:
    text: str
    provider_code: str
    model_id: str
    usage: dict[str, int | None] | None = None


def supports_temperature(model: str) -> bool:
    return not model.lower().startswith("gpt-5")


async def complete_text_with_fallback(
    *,
    messages: list[ChatMessage],
    temperature: float = 0,
    response_format: Literal["json_object"] | None = None,
    session: AsyncSession | None = None,
    settings: Settings | None = None,
    request_id: str | None = None,
    telegram_user_id: int | None = None,
) -> str | None:
    response = await generate_text_with_fallback(
        messages=messages,
        temperature=temperature,
        response_format=response_format,
        session=session,
        settings=settings,
        request_id=request_id,
        telegram_user_id=telegram_user_id,
    )
    return response.text if response else None


async def generate_text_with_fallback(
    *,
    messages: list[ChatMessage],
    temperature: float = 0,
    response_format: Literal["json_object"] | None = None,
    session: AsyncSession | None = None,
    settings: Settings | None = None,
    request_id: str | None = None,
    telegram_user_id: int | None = None,
) -> LlmTextResponse | None:
    resolved = settings or get_settings()
    if session is not None:
        return await _generate_text_with_session(
            session=session,
            settings=resolved,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            request_id=request_id,
            telegram_user_id=telegram_user_id,
        )

    async with async_session_factory() as own_session:
        response = await _generate_text_with_session(
            session=own_session,
            settings=resolved,
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            request_id=request_id,
            telegram_user_id=telegram_user_id,
        )
        await own_session.commit()
        return response


async def run_agent_with_fallback(
    *,
    invoke: Callable[[RuntimeProviderConfig], Awaitable[str]],
    session: AsyncSession | None,
    settings: Settings | None = None,
    request_id: str | None = None,
    telegram_user_id: int | None = None,
    side_effects_tracker: dict[str, Any] | None = None,
) -> str:
    resolved = settings or get_settings()
    if session is not None:
        return await _run_agent_with_session(
            session=session,
            settings=resolved,
            invoke=invoke,
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            side_effects_tracker=side_effects_tracker,
        )

    async with async_session_factory() as own_session:
        result = await _run_agent_with_session(
            session=own_session,
            settings=resolved,
            invoke=invoke,
            request_id=request_id,
            telegram_user_id=telegram_user_id,
            side_effects_tracker=side_effects_tracker,
        )
        await own_session.commit()
        return result


def build_chat_model(
    config: RuntimeProviderConfig,
    settings: Settings | None = None,
):
    resolved = settings or get_settings()
    if config.provider_code == "anthropic":
        return ChatAnthropic(
            model=config.model_id,
            api_key=config.api_key,
            base_url=resolved.claude_base_url.rstrip("/"),
            temperature=0,
            max_tokens=resolved.claude_max_tokens,
            timeout=resolved.claude_timeout_ms / 1000,
            max_retries=0,
        )
    if config.provider_code == "openai":
        kwargs: dict[str, Any] = {
            "model": config.model_id,
            "api_key": config.api_key,
            "base_url": resolved.openai_base_url or None,
            "max_retries": 0,
        }
        if supports_temperature(config.model_id):
            kwargs["temperature"] = 0
        return ChatOpenAI(**kwargs)
    if config.provider_code == "mistral":
        from langchain_mistralai import ChatMistralAI

        return ChatMistralAI(
            model=config.model_id,
            api_key=config.api_key,
            temperature=0,
            timeout=resolved.mistral_timeout_ms / 1000,
            max_retries=0,
        )
    raise LlmProviderError(
        f"Unsupported provider: {config.provider_code}",
        code="unsupported_provider",
        fallback_eligible=False,
    )


async def test_provider(
    config: RuntimeProviderConfig,
    *,
    settings: Settings | None = None,
) -> LlmTextResponse:
    return await _call_provider(
        config,
        messages=[{"role": "user", "content": "Reply with: ok"}],
        temperature=0,
        response_format=None,
        settings=settings or get_settings(),
    )


async def _generate_text_with_session(
    *,
    session: AsyncSession,
    settings: Settings,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    request_id: str | None,
    telegram_user_id: int | None,
) -> LlmTextResponse | None:
    configs = await get_runtime_provider_configs(session, settings=settings)
    if not configs:
        logger.error("llm_no_enabled_provider")
        return None

    last_error: Exception | None = None
    for index, config in enumerate(configs, start=1):
        start = time.perf_counter()
        try:
            response = await _call_provider(
                config,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                settings=settings,
            )
            await record_provider_call(
                session,
                request_id=request_id,
                telegram_user_id=telegram_user_id,
                provider_code=config.provider_code,
                model_id=config.model_id,
                priority=config.priority,
                status="success",
                latency_ms=_elapsed_ms(start),
                fallback_attempt_number=index,
                was_fallback=index > 1,
            )
            return response
        except LlmProviderError as exc:
            last_error = exc
            await record_provider_call(
                session,
                request_id=request_id,
                telegram_user_id=telegram_user_id,
                provider_code=config.provider_code,
                model_id=config.model_id,
                priority=config.priority,
                status="failed",
                error_type=exc.code,
                error_message_sanitized=str(exc),
                latency_ms=_elapsed_ms(start),
                fallback_attempt_number=index,
                was_fallback=index > 1,
            )
            logger.warning(
                "llm_provider_failed",
                extra={
                    "provider_code": config.provider_code,
                    "model_id": config.model_id,
                    "priority": config.priority,
                    "error_code": exc.code,
                    "fallback_eligible": exc.fallback_eligible,
                },
            )
            if not exc.fallback_eligible:
                raise

    logger.error("llm_all_providers_failed", extra={"error": str(last_error)})
    return None


async def _run_agent_with_session(
    *,
    session: AsyncSession,
    settings: Settings,
    invoke: Callable[[RuntimeProviderConfig], Awaitable[str]],
    request_id: str | None,
    telegram_user_id: int | None,
    side_effects_tracker: dict[str, Any] | None,
) -> str:
    configs = await get_runtime_provider_configs(session, settings=settings)
    if not configs:
        raise LlmProvidersUnavailable(SAFE_LLM_ERROR_MESSAGE)

    last_error: Exception | None = None
    for index, config in enumerate(configs, start=1):
        start = time.perf_counter()
        try:
            text = await invoke(config)
            await record_provider_call(
                session,
                request_id=request_id,
                telegram_user_id=telegram_user_id,
                provider_code=config.provider_code,
                model_id=config.model_id,
                priority=config.priority,
                status="success",
                latency_ms=_elapsed_ms(start),
                fallback_attempt_number=index,
                was_fallback=index > 1,
                tool_executed_before_failure=_side_effect_seen(side_effects_tracker),
            )
            return text
        except Exception as exc:
            provider_error = _normalize_exception(exc)
            last_error = provider_error
            side_effect_seen = _side_effect_seen(side_effects_tracker)
            await record_provider_call(
                session,
                request_id=request_id,
                telegram_user_id=telegram_user_id,
                provider_code=config.provider_code,
                model_id=config.model_id,
                priority=config.priority,
                status="failed",
                error_type=provider_error.code,
                error_message_sanitized=str(provider_error),
                latency_ms=_elapsed_ms(start),
                fallback_attempt_number=index,
                was_fallback=index > 1,
                tool_executed_before_failure=side_effect_seen,
            )
            logger.warning(
                "llm_agent_provider_failed",
                extra={
                    "provider_code": config.provider_code,
                    "model_id": config.model_id,
                    "priority": config.priority,
                    "error_code": provider_error.code,
                    "fallback_eligible": provider_error.fallback_eligible,
                    "tool_executed_before_failure": side_effect_seen,
                },
            )
            if side_effect_seen or not provider_error.fallback_eligible:
                raise LlmProvidersUnavailable(SAFE_LLM_ERROR_MESSAGE) from exc

    logger.error("llm_agent_all_providers_failed", extra={"error": str(last_error)})
    raise LlmProvidersUnavailable(SAFE_LLM_ERROR_MESSAGE)


async def _call_provider(
    config: RuntimeProviderConfig,
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> LlmTextResponse:
    try:
        if config.provider_code == "anthropic":
            return await _complete_with_anthropic(
                config,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                settings=settings,
            )
        if config.provider_code == "openai":
            return await _complete_with_openai(
                config,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                settings=settings,
            )
        if config.provider_code == "mistral":
            return await _complete_with_mistral(
                config,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                settings=settings,
            )
    except Exception as exc:
        raise _normalize_exception(exc) from exc

    raise LlmProviderError(
        f"Unsupported provider: {config.provider_code}",
        code="unsupported_provider",
        fallback_eligible=False,
    )


async def _complete_with_anthropic(
    config: RuntimeProviderConfig,
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> LlmTextResponse:
    system_messages = [
        message["content"].strip()
        for message in messages
        if message.get("role") == "system" and message.get("content", "").strip()
    ]
    anthropic_messages = [
        {"role": message["role"], "content": message["content"]}
        for message in messages
        if message.get("role") != "system" and message.get("content", "").strip()
    ]
    if not anthropic_messages:
        raise LlmProviderError(
            "No non-system messages to send",
            code="invalid_request",
            fallback_eligible=False,
        )

    if response_format == "json_object":
        system_messages.append(
            "Return only a valid JSON object. Do not include markdown."
        )

    payload: dict[str, Any] = {
        "model": config.model_id,
        "max_tokens": settings.claude_max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system_messages:
        payload["system"] = "\n\n".join(system_messages)

    async with httpx.AsyncClient(timeout=settings.claude_timeout_ms / 1000) as client:
        response = await client.post(
            f"{settings.claude_base_url.rstrip('/')}/v1/messages",
            headers={
                "x-api-key": config.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
        )
        _raise_for_status(response)

    data = response.json()
    content = data.get("content")
    if not isinstance(content, list):
        raise LlmProviderError(
            "Anthropic response has no content list",
            code="invalid_provider_response",
            fallback_eligible=True,
        )
    text_parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    text = "".join(text_parts).strip()
    if not text:
        raise LlmProviderError(
            "Anthropic response was empty",
            code="empty_response",
            fallback_eligible=True,
        )
    return LlmTextResponse(
        text=text,
        provider_code=config.provider_code,
        model_id=config.model_id,
        usage=_anthropic_usage(data),
    )


async def _complete_with_openai(
    config: RuntimeProviderConfig,
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> LlmTextResponse:
    kwargs: dict[str, Any] = {
        "model": config.model_id,
        "messages": messages,
    }
    if supports_temperature(config.model_id):
        kwargs["temperature"] = temperature
    if response_format == "json_object":
        kwargs["response_format"] = {"type": "json_object"}

    client = wrap_openai(
        AsyncOpenAI(
            api_key=config.api_key,
            base_url=settings.openai_base_url or None,
        )
    )
    response = await client.chat.completions.create(**kwargs)
    answer = response.choices[0].message.content
    if not answer:
        raise LlmProviderError(
            "OpenAI response was empty",
            code="empty_response",
            fallback_eligible=True,
        )
    usage = getattr(response, "usage", None)
    return LlmTextResponse(
        text=answer.strip(),
        provider_code=config.provider_code,
        model_id=config.model_id,
        usage={
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        if usage
        else None,
    )


async def _complete_with_mistral(
    config: RuntimeProviderConfig,
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> LlmTextResponse:
    payload: dict[str, Any] = {
        "model": config.model_id,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=settings.mistral_timeout_ms / 1000) as client:
        response = await client.post(
            f"{settings.mistral_base_url.rstrip('/')}/v1/chat/completions",
            headers={
                "authorization": f"Bearer {config.api_key}",
                "content-type": "application/json",
            },
            json=payload,
        )
        _raise_for_status(response)
    data = response.json()
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmProviderError(
            "Mistral response has no choices",
            code="invalid_provider_response",
            fallback_eligible=True,
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    answer = message.get("content") if isinstance(message, dict) else None
    if not answer:
        raise LlmProviderError(
            "Mistral response was empty",
            code="empty_response",
            fallback_eligible=True,
        )
    usage = data.get("usage") if isinstance(data, dict) else None
    return LlmTextResponse(
        text=str(answer).strip(),
        provider_code=config.provider_code,
        model_id=config.model_id,
        usage={
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        if isinstance(usage, dict)
        else None,
    )


def _normalize_exception(exc: Exception) -> LlmProviderError:
    if isinstance(exc, LlmProviderError):
        return exc
    if isinstance(
        exc,
        (
            APITimeoutError,
            APIConnectionError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ),
    ):
        return LlmProviderError(
            str(exc),
            code="network_or_timeout",
            fallback_eligible=True,
        )
    if isinstance(exc, (TypeError, ValueError, KeyError)):
        return LlmProviderError(
            str(exc),
            code=exc.__class__.__name__,
            fallback_eligible=False,
        )
    if isinstance(exc, APIStatusError):
        return _status_error(exc.status_code, str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        return _status_error(exc.response.status_code, exc.response.text)
    return LlmProviderError(
        str(exc),
        code=exc.__class__.__name__,
        fallback_eligible=True,
    )


def _status_error(status_code: int, message: str) -> LlmProviderError:
    fallback = status_code in {401, 403, 404, 408, 409, 429, 500, 502, 503, 504}
    if 400 <= status_code < 500 and status_code not in {401, 403, 404, 408, 409, 429}:
        fallback = False
    return LlmProviderError(
        message,
        code=f"http_{status_code}",
        fallback_eligible=fallback,
    )


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _status_error(exc.response.status_code, exc.response.text) from exc


def _anthropic_usage(data: dict[str, Any]) -> dict[str, int | None] | None:
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    total_tokens = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _side_effect_seen(tracker: dict[str, Any] | None) -> bool:
    return bool(tracker and tracker.get("executed"))


def flatten_message_content(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") if isinstance(c, dict) else str(c)
            for c in content
            if c
        )
    if isinstance(content, BaseMessage):
        return flatten_message_content(content.content)
    return str(content)
