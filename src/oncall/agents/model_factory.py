"""Chat model factory for OpenAI-compatible Bailian models."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from oncall.config import Settings, get_settings


def create_chat_model(agent_name: str, settings: Settings | None = None) -> ChatOpenAI:
    """Create the configured chat model; V1 shares one model across agents."""

    config = settings or get_settings()
    return ChatOpenAI(
        model=config.bailian_chat_model,
        api_key=config.bailian_api_key.get_secret_value(),
        base_url=config.bailian_base_url,
        temperature=0,
        timeout=30,
        default_headers={"X-OnCall-Agent": agent_name},
    )
