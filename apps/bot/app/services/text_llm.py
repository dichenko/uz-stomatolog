import logging
from typing import Literal, TypedDict

import httpx
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

ChatRole = Literal["system", "user", "assistant"]


class ChatMessage(TypedDict):
    role: ChatRole
    content: str


def _supports_temperature(model: str) -> bool:
    return not model.lower().startswith("gpt-5")


async def complete_text(
    *,
    messages: list[ChatMessage],
    temperature: float = 0,
    response_format: Literal["json_object"] | None = None,
    settings: Settings | None = None,
) -> str | None:
    resolved = settings or get_settings()
    if resolved.text_llm_provider == "claude":
        return await _complete_with_claude(
            messages=messages,
            temperature=temperature,
            response_format=response_format,
            settings=resolved,
        )
    return await _complete_with_openai(
        messages=messages,
        temperature=temperature,
        response_format=response_format,
        settings=resolved,
    )


async def _complete_with_claude(
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> str | None:
    if settings.claude_api_key is None:
        return None

    api_key = settings.claude_api_key.get_secret_value().strip()
    if not api_key:
        return None

    system_messages = [
        message["content"].strip()
        for message in messages
        if message["role"] == "system" and message["content"].strip()
    ]
    claude_messages = [
        {
            "role": message["role"],
            "content": message["content"],
        }
        for message in messages
        if message["role"] != "system" and message["content"].strip()
    ]
    if not claude_messages:
        return None

    if response_format == "json_object":
        system_messages.append(
            "Return only a valid JSON object. Do not include markdown."
        )

    payload: dict[str, object] = {
        "model": settings.claude_text_model,
        "max_tokens": settings.claude_max_tokens,
        "temperature": temperature,
        "messages": claude_messages,
    }
    if system_messages:
        payload["system"] = "\n\n".join(system_messages)

    try:
        async with httpx.AsyncClient(
            timeout=settings.claude_timeout_ms / 1000,
        ) as client:
            response = await client.post(
                f"{settings.claude_base_url.rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except Exception:
        logger.exception("claude_text_generation_failed")
        return None

    data = response.json()
    content = data.get("content")
    if not isinstance(content, list):
        return None

    text_parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    answer = "".join(text_parts).strip()
    return answer or None


async def _complete_with_openai(
    *,
    messages: list[ChatMessage],
    temperature: float,
    response_format: Literal["json_object"] | None,
    settings: Settings,
) -> str | None:
    if settings.openai_api_key is None:
        return None

    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        return None

    kwargs: dict[str, object] = {
        "model": settings.openai_text_model,
        "messages": messages,
    }
    if _supports_temperature(settings.openai_text_model):
        kwargs["temperature"] = temperature
    if response_format == "json_object":
        kwargs["response_format"] = {"type": "json_object"}

    try:
        client = wrap_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url=settings.openai_base_url or None,
            )
        )
        response = await client.chat.completions.create(**kwargs)
        answer = response.choices[0].message.content
        return answer.strip() if answer else None
    except Exception:
        logger.exception("openai_text_generation_failed")
        return None
