"""Application configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
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
    bailian_embedding_model: str
    milvus_uri: str
    recovery_mcp_url: str
    recovery_mcp_secret: SecretStr
    alertmanager_webhook_secrets: dict[str, SecretStr] = Field(default_factory=dict)
    alert_fingerprint_stable_labels: tuple[str, ...] = ()


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""

    return Settings()


@lru_cache
def get_broker_settings() -> BrokerSettings:
    """Return Redis bootstrap settings using the same `.env` contract as the API."""

    return BrokerSettings()
