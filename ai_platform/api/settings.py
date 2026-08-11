"""Control plane settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLATFORM_")

    db_path: str = ".platform/registry.db"
    # Postgres DSN for multi-tenant SaaS (overrides SQLite registry when set).
    # Also accepts DATABASE_URL without the PLATFORM_ prefix via env alias below.
    database_url: str | None = None
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    default_namespace: str = "default-org/default-project"
    default_env: str = "development"
    signing_key_pem: str | None = None
    primary_region: str = "us-east-1"
    federation_domain: str = "local.ai-platform"
    secrets_key: str | None = None
    embedding_provider: str = "auto"  # auto | local | openai
    sandbox_timeout_seconds: float = 30.0
