"""Real OIDC provider + SSO integration tests (offline RSA JWKS)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.auth.identity import IdentityStore
from ai_platform.auth.oidc_provider import (
    OidcProvider,
    OidcProviderError,
    mint_rs256_jwt,
    rsa_jwk_from_private_pem,
)
from ai_platform.auth.sso import OidcValidator, SsoService
from ai_platform.db.sql import create_sql_backend, migrate_aux_stores


@pytest.fixture
def rsa_material():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    jwk, loaded = rsa_jwk_from_private_pem(pem, kid="unit-key")
    return loaded, jwk


@pytest.mark.asyncio
async def test_validate_id_token_rs256(rsa_material):
    private_key, jwk = rsa_material
    issuer = "https://idp.example.com/oauth2/default"
    provider = OidcProvider(
        issuer=issuer,
        client_id="studio",
        redirect_uri="http://localhost:5173/",
        discovery={
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/v1/authorize",
            "token_endpoint": f"{issuer}/v1/token",
            "jwks_uri": f"{issuer}/v1/keys",
        },
        jwks={"keys": [jwk]},
    )
    now = int(time.time())
    token = mint_rs256_jwt(
        private_key,
        {"alg": "RS256", "typ": "JWT", "kid": "unit-key"},
        {
            "sub": "user-1",
            "email": "alice@example.com",
            "name": "Alice",
            "iss": issuer,
            "aud": "studio",
            "exp": now + 600,
            "iat": now,
            "nonce": "n1",
        },
    )
    claims = await provider.validate_id_token(token, nonce="n1")
    assert claims.email == "alice@example.com"
    assert claims.subject == "user-1"


@pytest.mark.asyncio
async def test_validate_id_token_rejects_bad_audience(rsa_material):
    private_key, jwk = rsa_material
    issuer = "https://login.microsoftonline.com/tenant/v2.0"
    provider = OidcProvider(
        issuer=issuer,
        client_id="azure-client",
        discovery={
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": f"{issuer}/keys",
        },
        jwks={"keys": [jwk]},
    )
    now = int(time.time())
    token = mint_rs256_jwt(
        private_key,
        {"alg": "RS256", "kid": "unit-key"},
        {
            "sub": "x",
            "email": "x@example.com",
            "iss": issuer,
            "aud": "someone-else",
            "exp": now + 600,
        },
    )
    with pytest.raises(OidcProviderError, match="audience"):
        await provider.validate_id_token(token)


@pytest.mark.asyncio
async def test_sso_complete_oidc_issues_platform_jwt(tmp_path: Path, rsa_material, monkeypatch):
    private_key, jwk = rsa_material
    issuer = "https://okta.example.com/oauth2/default"
    provider = OidcProvider(
        issuer=issuer,
        client_id="okta-app",
        client_secret="secret",
        redirect_uri="http://localhost:5173/",
        discovery={
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/v1/authorize",
            "token_endpoint": f"{issuer}/v1/token",
            "jwks_uri": f"{issuer}/v1/keys",
        },
        jwks={"keys": [jwk]},
    )

    now = int(time.time())
    id_token = mint_rs256_jwt(
        private_key,
        {"alg": "RS256", "kid": "unit-key"},
        {
            "sub": "okta|123",
            "email": "ops@acme.com",
            "name": "Ops",
            "iss": issuer,
            "aud": "okta-app",
            "exp": now + 600,
            "nonce": "nonce-1",
        },
    )

    async def fake_exchange(*, code: str, code_verifier: str):
        assert code == "auth-code"
        assert code_verifier == "verifier"
        return {"id_token": id_token, "access_token": "at", "token_type": "Bearer"}

    monkeypatch.setattr(provider, "exchange_code", fake_exchange)

    sql = create_sql_backend(db_path=str(tmp_path / "id.db"))
    await migrate_aux_stores(sql)
    identity = IdentityStore(sql=sql)
    sso = SsoService(
        identity,
        OidcValidator("test-secret"),
        oidc_provider=provider,
        allow_dev_login=False,
        default_org_id="acme",
    )

    started = await sso.begin_oidc(code_challenge="challenge", org_id="acme")
    assert "authorizationUrl" in started
    # Force known nonce for token
    sso._pending[started["state"]]["nonce"] = "nonce-1"

    session = await sso.complete_oidc(
        code="auth-code",
        state=started["state"],
        code_verifier="verifier",
        org_id="acme",
    )
    assert session["provider"] == "oidc"
    assert session["user"]["email"] == "ops@acme.com"
    ctx = sso.authenticate(f"Bearer {session['accessToken']}")
    assert ctx is not None
    assert ctx.email == "ops@acme.com"
    assert ctx.provider == "oidc"

    with pytest.raises(PermissionError):
        await sso.login("acme", "dev@acme.com")
    await sql.close()


@pytest.mark.asyncio
async def test_auth_config_and_dev_login_still_work(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "auth.db"), auth_required=True)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            cfg = await ac.get("/v1/auth/config")
            assert cfg.status_code == 200
            assert cfg.json()["mode"] == "dev"
            assert cfg.json()["devLoginEnabled"] is True

            login = await ac.post(
                "/v1/auth/login",
                json={"email": "ops@example.com", "orgId": "default-org"},
            )
            assert login.status_code == 200
            token = login.json()["accessToken"]
            ok = await ac.get(
                "/v1/default-org/default-project/resources",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert ok.status_code == 200


@pytest.mark.asyncio
async def test_auth_config_oidc_mode(tmp_path: Path):
    settings = Settings(
        db_path=str(tmp_path / "oidc.db"),
        auth_required=True,
        oidc_issuer="https://login.microsoftonline.com/tenant/v2.0",
        oidc_client_id="azure-app",
        oidc_client_secret="secret",
        oidc_redirect_uri="http://localhost:5173/",
        allow_dev_login=False,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            cfg = await ac.get("/v1/auth/config")
            body = cfg.json()
            assert body["mode"] == "oidc"
            assert body["devLoginEnabled"] is False
            assert body["oidc"]["clientId"] == "azure-app"

            denied = await ac.post(
                "/v1/auth/login",
                json={"email": "x@y.com", "orgId": "default-org"},
            )
            assert denied.status_code == 403
