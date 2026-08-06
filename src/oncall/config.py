"""Application configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
)


class BrokerSettings(BaseSettings):
    """Worker bootstrap settings that do not require unrelated app secrets."""

    model_config = ENV_FILE_CONFIG

    redis_url: str = "redis://127.0.0.1:16379/0"


class Settings(BaseSettings):
    """Strongly typed runtime configuration for the OnCall service."""

    model_config = ENV_FILE_CONFIG

    app_env: Literal["local", "production"] = "local"
    database_url: str
    redis_url: str
    jwt_secret: SecretStr = Field(min_length=32)
    bailian_api_key: SecretStr
    bailian_base_url: str
    bailian_chat_model: str
    agent_loop_max_turns: int = Field(default=8, ge=2, le=20)
    agent_specialist_max_turns: int = Field(default=4, ge=2, le=10)
    agent_max_tool_calls: int = Field(default=16, ge=4, le=64)
    openai_agents_tracing_enabled: bool = False
    bailian_embedding_model: str = Field(min_length=1, max_length=256)
    bailian_embedding_dimension: int = Field(default=1024, gt=0, le=32768)
    milvus_uri: str
    milvus_knowledge_collection: str = Field(
        default="oncall_operational_knowledge",
        # Reserve ten characters for the companion ``__manifest`` collection.
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,244}$",
    )
    conversation_router_live: bool = True
    mem0_enabled: bool = True
    mem0_telemetry: bool = False
    mem0_collection: str = Field(
        default="oncall_conversation_memory",
        pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,254}$",
    )
    recovery_mcp_url: str
    recovery_mcp_secret: SecretStr
    recovery_approval_secret: SecretStr = Field(min_length=32)
    # Recovery verification queries the error rate over a short rate window, so a
    # brief wait after rollback is enough for that window to clear the pre-rollback
    # 5xx errors before the health check runs.
    recovery_verification_wait_seconds: float = Field(default=45.0, ge=0, le=300)

    @model_validator(mode="after")
    def validate_embedding_model_dimension(self) -> "Settings":
        """Reject dimensions unsupported by the configured Bailian model."""

        if self.bailian_embedding_model == "text-embedding-v4":
            supported = {64, 128, 256, 512, 768, 1024, 1536, 2048}
            if self.bailian_embedding_dimension not in supported:
                raise ValueError(
                    "text-embedding-v4 dimension must be one of "
                    f"{sorted(supported)}; got {self.bailian_embedding_dimension}"
                )
        if (
            self.recovery_mcp_secret.get_secret_value()
            == self.recovery_approval_secret.get_secret_value()
        ):
            raise ValueError("Recovery transport and approval secrets must be independent")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""

    return Settings()


@lru_cache
def get_broker_settings() -> BrokerSettings:
    """Return Redis bootstrap settings using the same `.env` contract as the API."""

    return BrokerSettings()
