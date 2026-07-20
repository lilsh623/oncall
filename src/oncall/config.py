"""Application configuration loaded from the environment."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed runtime configuration for the OnCall service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "production"] = "local"
    database_url: str
    redis_url: str
    jwt_secret: SecretStr
    bailian_api_key: SecretStr
    bailian_base_url: str
    bailian_chat_model: str
    bailian_embedding_model: str
    milvus_uri: str
    recovery_mcp_url: str
    recovery_mcp_secret: SecretStr


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings instance."""

    return Settings()
