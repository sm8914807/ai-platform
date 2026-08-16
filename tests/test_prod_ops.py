"""Production hardening, Redis governor config, and audit activity log."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.prod_checks import (
    ProductionHardeningError,
    assert_production_ready,
    is_weak_secret,
)
from ai_platform.api.settings import Settings
from ai_platform.core.ids import new_id
from ai_platform.core.models import AuditEvent
from ai_platform.governor.engine import ToolGovernor
from ai_platform.registry.memory import InMemoryRegistryStore


def test_weak_secret_detection():
    assert is_weak_secret(None)
    assert is_weak_secret("change-me")
    assert is_weak_secret("short")
    assert not is_weak_secret("a-sufficiently-long-production-secret")


def test_production_hardening_requires_secrets_and_oidc():
    settings = Settings(
        env="production",
        auth_required=True,
        allow_dev_login=False,
        secrets_key="a-sufficiently-long-production-secret",
        auth_secret="another-sufficiently-long-jwt-secret",
        oidc_issuer="https://login.example.com",
        oidc_client_id="app",
        redis_url="redis://localhost:6379/0",
    )
    assert_production_ready(settings)

    with pytest.raises(ProductionHardeningError, match="SECRETS_KEY"):
        assert_production_ready(
            Settings(
                env="production",
                allow_dev_login=False,
                auth_secret="another-sufficiently-long-jwt-secret",
                oidc_issuer="https://login.example.com",
                oidc_client_id="app",
                redis_url="redis://localhost:6379/0",
            )
        )

    with pytest.raises(ProductionHardeningError, match="ALLOW_DEV_LOGIN"):
        assert_production_ready(
            Settings(
                env="production",
                allow_dev_login=True,
                secrets_key="a-sufficiently-long-production-secret",
                auth_secret="another-sufficiently-long-jwt-secret",
                oidc_issuer="https://login.example.com",
                oidc_client_id="app",
                redis_url="redis://localhost:6379/0",
            )
        )

    with pytest.raises(ProductionHardeningError, match="REDIS_URL"):
        assert_production_ready(
            Settings(
                env="production",
                allow_dev_login=False,
                secrets_key="a-sufficiently-long-production-secret",
                auth_secret="another-sufficiently-long-jwt-secret",
                oidc_issuer="https://login.example.com",
                oidc_client_id="app",
            )
        )

    # Explicit single-replica opt-out
    assert_production_ready(
        Settings(
            env="production",
            allow_dev_login=False,
            secrets_key="a-sufficiently-long-production-secret",
            auth_secret="another-sufficiently-long-jwt-secret",
            oidc_issuer="https://login.example.com",
            oidc_client_id="app",
            governor_backend="memory",
        )
    )


def test_create_app_refuses_unsafe_production(tmp_path: Path):
    with pytest.raises(ProductionHardeningError):
        create_app(
            Settings(
                env="prod",
                db_path=str(tmp_path / "x.db"),
                allow_dev_login=True,
            )
        )


def test_governor_from_config_backends():
    mem = ToolGovernor.from_config(backend="memory")
    assert mem.backend == "memory"
    assert mem.fail_closed is False

    redis = ToolGovernor.from_config(
        redis_url="redis://localhost:6379/0", backend="auto"
    )
    assert redis.backend == "redis"
    assert redis.fail_closed is True

    with pytest.raises(ValueError, match="PLATFORM_REDIS_URL"):
        ToolGovernor.from_config(backend="redis")


@pytest.mark.asyncio
async def test_list_audit_memory_store():
    store = InMemoryRegistryStore()
    now = datetime.now(UTC)
    await store.append_audit(
        AuditEvent(
            id=new_id("audit"),
            org_id="acme",
            actor_id="u1",
            action="resource.published",
            resource_ref="agents/a",
            payload={"version": "1.0.0"},
            created_at=now,
        )
    )
    await store.append_audit(
        AuditEvent(
            id=new_id("audit"),
            org_id="acme",
            actor_id="u1",
            action="auth.login",
            resource_ref="users/a@acme.com",
            created_at=now,
        )
    )
    await store.append_audit(
        AuditEvent(
            id=new_id("audit"),
            org_id="other",
            actor_id="u2",
            action="auth.login",
            created_at=now,
        )
    )
    all_acme = await store.list_audit("acme", limit=10)
    assert len(all_acme) == 2
    logins = await store.list_audit("acme", action="auth.login")
    assert len(logins) == 1
    assert logins[0].action == "auth.login"


@pytest.mark.asyncio
async def test_audit_api_and_login_event(tmp_path: Path):
    settings = Settings(
        db_path=str(tmp_path / "audit.db"),
        auth_required=True,
        auth_secret="local-test-auth-secret-xx",
        secrets_key="local-test-secrets-key-xx",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            health = await ac.get("/health")
            assert health.status_code == 200
            body = health.json()
            assert body["env"] == "development"
            assert body["governorBackend"] == "memory"
            assert body["devLoginEnabled"] is True

            login = await ac.post(
                "/v1/auth/login",
                json={"email": "ops@example.com", "orgId": "default-org"},
            )
            assert login.status_code == 200
            token = login.json()["accessToken"]
            headers = {"Authorization": f"Bearer {token}"}

            audit = await ac.get(
                "/v1/default-org/default-project/audit?limit=20",
                headers=headers,
            )
            assert audit.status_code == 200
            events = audit.json()["events"]
            assert any(e["action"] == "auth.login" for e in events)

            put = await ac.put(
                "/v1/default-org/default-project/secrets/demo",
                headers=headers,
                json={"value": "super-secret"},
            )
            assert put.status_code == 200
            audit2 = await ac.get(
                "/v1/default-org/default-project/audit?action=secret.put",
                headers=headers,
            )
            assert audit2.status_code == 200
            assert audit2.json()["count"] >= 1
