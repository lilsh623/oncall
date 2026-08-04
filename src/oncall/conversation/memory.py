"""Optional local Mem0 long-term memory backed by the existing Milvus service."""

from __future__ import annotations

import asyncio
from functools import lru_cache
import os
from typing import Any, Protocol

import structlog

from oncall.config import get_settings
from oncall.experience.extraction import redact_text


class MemoryProvider(Protocol):
    async def search(self, query: str, *, user_id: str, limit: int = 5) -> list[str]: ...
    async def add_turn(
        self, *, user_id: str, conversation_id: str, user_text: str, assistant_text: str
    ) -> None: ...


class NullMemoryProvider:
    async def search(self, query: str, *, user_id: str, limit: int = 5) -> list[str]:
        return []

    async def add_turn(
        self, *, user_id: str, conversation_id: str, user_text: str, assistant_text: str
    ) -> None:
        return None


class Mem0MemoryProvider:
    """Use Mem0 for extracted long-term facts; PostgreSQL remains the transcript store."""

    def __init__(self) -> None:
        settings = get_settings()
        # The local-memory deployment must not emit third-party usage telemetry.
        os.environ["MEM0_TELEMETRY"] = "True" if settings.mem0_telemetry else "False"
        from mem0 import Memory

        config = {
            "vector_store": {
                "provider": "milvus",
                "config": {
                    "collection_name": settings.mem0_collection,
                    "url": settings.milvus_uri,
                    "embedding_model_dims": settings.bailian_embedding_dimension,
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.bailian_chat_model,
                    "api_key": settings.bailian_api_key.get_secret_value(),
                    "openai_base_url": settings.bailian_base_url,
                    "temperature": 0,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": settings.bailian_embedding_model,
                    "api_key": settings.bailian_api_key.get_secret_value(),
                    "openai_base_url": settings.bailian_base_url,
                    "embedding_dims": settings.bailian_embedding_dimension,
                },
            },
            "history_db_path": "/tmp/oncall_mem0_history.db",
        }
        self._memory = Memory.from_config(config)

    async def search(self, query: str, *, user_id: str, limit: int = 5) -> list[str]:
        result = await asyncio.to_thread(
            self._memory.search, redact_text(query), user_id=user_id, limit=limit
        )
        rows = result.get("results", []) if isinstance(result, dict) else result
        if not isinstance(rows, list):
            return []
        return [
            str(item.get("memory"))[:1000]
            for item in rows
            if isinstance(item, dict) and item.get("memory")
        ][:limit]

    async def add_turn(
        self, *, user_id: str, conversation_id: str, user_text: str, assistant_text: str
    ) -> None:
        messages = [
            {"role": "user", "content": redact_text(user_text)},
            {"role": "assistant", "content": redact_text(assistant_text)},
        ]
        await asyncio.to_thread(
            self._memory.add,
            messages,
            user_id=user_id,
            run_id=conversation_id,
            metadata={"source": "oncall-conversation", "conversation_id": conversation_id},
        )


@lru_cache
def get_memory_provider() -> MemoryProvider:
    if not get_settings().mem0_enabled:
        return NullMemoryProvider()
    try:
        return Mem0MemoryProvider()
    except Exception:
        structlog.get_logger(__name__).warning("mem0_unavailable_safe_fallback")
        return NullMemoryProvider()
