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
    redis_url: str | None = None
    planner_mode: str = "auto"  # auto | llm | heuristic
    planner_model_ref: str | None = None
    # When True (default), /v1 and /scim require a Bearer JWT from /v1/auth/login.
    auth_required: bool = True
    auth_secret: str | None = None
    # Comma-separated browser origins for CORS (Studio).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Real OIDC (Okta / Azure AD / Keycloak). When issuer+client_id are set, Studio can IdP-login.
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = "http://localhost:5173/"
    oidc_scopes: str = "openid profile email"
    oidc_audience: str | None = None
    # Keep email/password-less HMAC login when OIDC is configured (set false in production).
    allow_dev_login: bool = True
    # OpenTelemetry / OTLP (HTTP). Example: http://localhost:4318/v1/traces
    otlp_endpoint: str | None = None
    otlp_service_name: str = "ai-platform-api"
    otlp_console: bool = False
    # Keep finished spans in-memory (tests / local debugging).
    otlp_memory: bool = False
