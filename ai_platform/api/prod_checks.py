"""Fail-closed checks for production deployments."""

from __future__ import annotations

from ai_platform.api.settings import Settings

_WEAK_SECRETS = frozenset(
    {
        "",
        "change-me",
        "change-me-jwt-secret",
        "dev-secrets-key-change-me",
        "dev-platform-secret-change-in-prod",
        "secret",
        "password",
        "platform",
        "test",
        "ci-test-secret",
        "e2e-ci-secret",
    }
)

_PROD_ALIASES = frozenset({"production", "prod"})


def is_production(env: str | None) -> bool:
    return (env or "").strip().lower() in _PROD_ALIASES


def is_weak_secret(value: str | None, *, min_len: int = 16) -> bool:
    if value is None:
        return True
    text = value.strip()
    if len(text) < min_len:
        return True
    return text.lower() in _WEAK_SECRETS


class ProductionHardeningError(RuntimeError):
    """Raised when PLATFORM_ENV=production settings are unsafe."""


def assert_production_ready(settings: Settings) -> None:
    """Refuse to boot when production env has unsafe auth/secrets/governor defaults."""
    if not is_production(settings.env):
        return

    errors: list[str] = []

    if not settings.auth_required:
        errors.append("PLATFORM_AUTH_REQUIRED must be true in production")

    if settings.allow_dev_login:
        errors.append(
            "PLATFORM_ALLOW_DEV_LOGIN must be false in production "
            "(use OIDC / Okta / Azure AD)"
        )

    if is_weak_secret(settings.secrets_key):
        errors.append(
            "PLATFORM_SECRETS_KEY must be set to a strong non-default value "
            "(≥16 chars, not a documented placeholder)"
        )

    if is_weak_secret(settings.auth_secret):
        errors.append(
            "PLATFORM_AUTH_SECRET must be set to a strong non-default value "
            "(≥16 chars, not a documented placeholder)"
        )

    if not (settings.oidc_issuer and settings.oidc_client_id):
        errors.append(
            "PLATFORM_OIDC_ISSUER and PLATFORM_OIDC_CLIENT_ID are required in "
            "production when dev login is disabled"
        )

    backend = (settings.governor_backend or "auto").strip().lower()
    if backend == "redis" and not settings.redis_url:
        errors.append("PLATFORM_GOVERNOR_BACKEND=redis requires PLATFORM_REDIS_URL")
    elif backend == "auto" and not settings.redis_url:
        errors.append(
            "PLATFORM_REDIS_URL is required in production for multi-instance "
            "tool governor (or set PLATFORM_GOVERNOR_BACKEND=memory for a "
            "single-replica opt-out)"
        )

    if errors:
        raise ProductionHardeningError(
            "Production hardening failed:\n- " + "\n- ".join(errors)
        )
