from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Secrets are read from the environment, never logged."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Knowledge SaaS API"
    environment: str = "development"
    debug: bool = False
    database_url: str = "sqlite+aiosqlite:///./data/knowledge.db"
    storage_backend: str = "local"
    storage_root: Path = Path("./data/objects")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str = "us-east-1"
    redis_url: str | None = None
    queue_backend: str = "inline"

    # Local development authentication is deliberately opt-in outside development/test.
    allow_dev_auth: bool = True
    dev_api_key: str = Field(default="dev-local-token", repr=False)
    dev_user_id: str | None = None
    jwt_issuer: str | None = None
    jwt_audience: str | None = None
    jwt_public_key: str | None = Field(default=None, repr=False)
    oidc_jwks_url: str | None = None

    max_upload_bytes: int = 5 * 1024 * 1024
    max_document_chars: int = 2_000_000
    max_question_chars: int = 4_000
    max_search_chars: int = 500
    max_top_k: int = 10
    rate_limit_per_minute: int = 120
    monthly_usage_quota: int = 100_000
    embedding_provider: str = "local"
    llm_provider: str = "local"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_chat_model: str = "gpt-4o-mini"
    anthropic_api_key: str | None = Field(default=None, repr=False)
    anthropic_model: str = "claude-3-5-haiku-latest"
    cors_origins: str = ""

    @property
    def is_local_auth_enabled(self) -> bool:
        return self.allow_dev_auth and self.environment.lower() in {"development", "dev", "test"}

    @property
    def data_dir(self) -> Path:
        if self.storage_root.is_absolute():
            return self.storage_root.parent
        return Path.cwd() / self.storage_root.parent


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
