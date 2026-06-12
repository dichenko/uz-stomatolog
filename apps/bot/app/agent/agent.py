"""LangChain ReAct agent for Madina VoiceFlow."""

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import create_react_agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import ALL_TOOLS
from app.config import Settings, get_settings
from app.llm.manager import (
    SAFE_LLM_ERROR_MESSAGE,
    build_chat_model,
    flatten_message_content,
    run_agent_with_fallback,
)
from app.llm.repository import RuntimeProviderConfig

logger = logging.getLogger(__name__)

def create_agent(
    provider_config: RuntimeProviderConfig,
    settings: Settings | None = None,
):
    llm = build_chat_model(provider_config, settings=settings)
    return create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
    )


async def run_agent(
    *,
    input_text: str,
    config: RunnableConfig,
    chat_history: list | None = None,
    system_prompt: str = "",
) -> str:
    settings = get_settings()
    configurable = config.setdefault("configurable", {})
    side_effects_tracker = configurable.setdefault(
        "side_effects",
        {"executed": False, "tools": []},
    )
    session = configurable.get("session")
    user = configurable.get("user")
    trace_id = configurable.get("trace_id")

    async def invoke(provider_config: RuntimeProviderConfig) -> str:
        agent = create_agent(provider_config, settings=settings)
        messages = _build_messages(
            input_text=input_text,
            chat_history=chat_history,
            system_prompt=system_prompt,
            provider_code=provider_config.provider_code,
        )
        result = await agent.ainvoke(
            {"messages": messages},
            config=config,
        )
        last_message = result["messages"][-1]
        content = last_message.content if hasattr(last_message, "content") else str(last_message)
        text = flatten_message_content(content).strip()
        return text or SAFE_LLM_ERROR_MESSAGE

    return await run_agent_with_fallback(
        invoke=invoke,
        session=session if isinstance(session, AsyncSession) else None,
        settings=settings,
        request_id=str(trace_id) if trace_id else None,
        telegram_user_id=getattr(user, "telegram_user_id", None),
        side_effects_tracker=side_effects_tracker,
    )


def _build_messages(
    *,
    input_text: str,
    chat_history: list | None,
    system_prompt: str,
    provider_code: str,
) -> list:
    messages = []
    if chat_history:
        messages.extend(chat_history)
    if system_prompt.strip():
        if provider_code == "anthropic":
            system_content: Any = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_content = system_prompt
        messages.append(SystemMessage(content=system_content))
    messages.append(HumanMessage(content=input_text))
    return messages
