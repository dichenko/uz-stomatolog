"""LangChain ReAct Agent — Madina VoiceFlow v1.2."""

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.agent.tools import ALL_TOOLS
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_agent = None
_agent_settings_hash: int | None = None


def _settings_hash(settings: Settings) -> int:
    return hash((
        settings.text_llm_provider,
        settings.openai_text_model,
        settings.claude_text_model,
        settings.claude_base_url,
    ))


def _build_llm(settings: Settings):
    if settings.text_llm_provider == "claude":
        api_key = settings.claude_api_key.get_secret_value() if settings.claude_api_key else ""
        return ChatAnthropic(
            model=settings.claude_text_model,
            api_key=api_key,
            base_url=settings.claude_base_url.rstrip("/"),
            temperature=0,
            max_tokens=settings.claude_max_tokens,
            timeout=settings.claude_timeout_ms / 1000,
        )
    else:
        api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        return ChatOpenAI(
            model=settings.openai_text_model,
            api_key=api_key,
            base_url=settings.openai_base_url or None,
            temperature=0,
        )


def create_agent(settings: Settings | None = None):
    global _agent, _agent_settings_hash
    resolved = settings or get_settings()
    new_hash = _settings_hash(resolved)
    if _agent is not None and _agent_settings_hash == new_hash:
        return _agent

    llm = _build_llm(resolved)
    _agent = create_react_agent(
        model=llm,
        tools=ALL_TOOLS,
        prompt="",  # system prompt is injected dynamically at runtime
    )
    _agent_settings_hash = new_hash
    return _agent


async def run_agent(
    *,
    input_text: str,
    config: RunnableConfig,
    chat_history: list | None = None,
    system_prompt: str = "",
) -> str:
    agent = create_agent()
    messages = [SystemMessage(content=system_prompt)] if system_prompt.strip() else []
    messages.append(HumanMessage(content=input_text))
    if chat_history:
        messages = list(chat_history) + messages[1:] if messages else list(chat_history) + [messages[-1]]
    result = await agent.ainvoke(
        {"messages": messages},
        config=config,
    )
    last_message = result["messages"][-1]
    content = last_message.content if hasattr(last_message, "content") else str(last_message)
    if isinstance(content, list):
        content = "\n".join(
            c.get("text", "") if isinstance(c, dict) else str(c)
            for c in content
            if c
        )
    if isinstance(content, str):
        return content.strip() or "Извините, произошла ошибка. Попробуйте ещё раз."
    return str(content)
