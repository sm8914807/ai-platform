"""OIDC / JWT authentication for control plane API.

- Platform session tokens: HMAC JWT (``OidcValidator``) used by Studio after login.
- Enterprise IdP: real OIDC via ``OidcProvider`` (Okta / Azure AD / Keycloak).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Any

from ai_platform.auth.identity import IdentityStore
from ai_platform.auth.oidc_provider import OidcProvider, OidcProviderError
from ai_platform.core.ids import new_id


@dataclass
class AuthContext:
    user_id: str
    email: str
    org_id: str
    principal: str
    provider: str


class OidcValidator:
    """HMAC-signed platform session JWT (issued after IdP or dev login)."""

    def __init__(self, secret: str, issuer: str = "https://platform.ai") -> None:
        self.secret = secret
        self.issuer = issuer

    def create_token(
        self,
        user_id: str,
        email: str,
        org_id: str,
        teams: list[str] | None = None,
        ttl_seconds: int = 3600,
        *,
        provider: str = "platform",
    ) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user_id,
            "email": email,
            "org_id": org_id,
            "teams": teams or [],
            "iss": self.issuer,
            "provider": provider,
            "exp": int(time.time()) + ttl_seconds,
        }
        return self._encode(header, payload)

    def validate_token(self, token: str) -> AuthContext | None:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            payload = json.loads(self._b64decode(parts[1]))
            if payload.get("exp", 0) < time.time():
                return None
            expected_sig = self._sign(f"{parts[0]}.{parts[1]}")
            if not hmac.compare_digest(parts[2], expected_sig):
                return None
            teams = payload.get("teams", [])
            principal = teams[0] if teams else f"user:{payload.get('email', '')}"
            return AuthContext(
                user_id=payload["sub"],
                email=payload.get("email", ""),
                org_id=payload.get("org_id", "default-org"),
                principal=principal,
                provider=str(payload.get("provider") or "platform"),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def _encode(self, header: dict[str, Any], payload: dict[str, Any]) -> str:
        h = self._b64(json.dumps(header, separators=(",", ":")))
        p = self._b64(json.dumps(payload, separators=(",", ":")))
        sig = self._sign(f"{h}.{p}")
        return f"{h}.{p}.{sig}"

    def _sign(self, data: str) -> str:
        sig = hmac.new(self.secret.encode(), data.encode(), hashlib.sha256).digest()
        return self._b64(sig)

    def _b64(self, data: str | bytes) -> str:
        raw = data.encode() if isinstance(data, str) else data
        return urlsafe_b64encode(raw).rstrip(b"=").decode()

    def _b64decode(self, data: str) -> str:
        pad = "=" * (-len(data) % 4)
        from base64 import urlsafe_b64decode

        return urlsafe_b64decode(data + pad).decode()


class SsoService:
    """Login flow bridging identity store, platform JWTs, and optional OIDC IdP."""

    def __init__(
        self,
        identity_store: IdentityStore,
        oidc: OidcValidator | None = None,
        *,
        oidc_provider: OidcProvider | None = None,
        allow_dev_login: bool = True,
        default_org_id: str = "default-org",
    ) -> None:
        self.identity = identity_store
        self.oidc = oidc or OidcValidator(secret="dev-platform-secret-change-in-prod")
        self.oidc_provider = oidc_provider
        self.allow_dev_login = allow_dev_login
        self.default_org_id = default_org_id
        self._pending: dict[str, dict[str, Any]] = {}

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_provider and self.oidc_provider.enabled)

    def auth_config(self) -> dict[str, Any]:
        mode = "oidc" if self.oidc_enabled else "dev"
        cfg: dict[str, Any] = {
            "mode": mode,
            "devLoginEnabled": bool(self.allow_dev_login),
            "defaultOrgId": self.default_org_id,
        }
        if self.oidc_enabled and self.oidc_provider:
            cfg["oidc"] = self.oidc_provider.public_config()
        return cfg

    async def login(
        self, org_id: str, email: str, display_name: str | None = None
    ) -> dict[str, Any]:
        if not self.allow_dev_login:
            raise PermissionError(
                "Dev email login disabled; use OIDC (Okta / Azure AD) sign-in"
            )
        user = await self.identity.get_user_by_email(org_id, email)
        if not user:
            user = await self.identity.create_user(org_id, email, display_name)
        return self._session_response(user.id, user.email, org_id, user.teams, provider="dev")

    async def begin_oidc(
        self,
        *,
        code_challenge: str,
        org_id: str | None = None,
        redirect_uri: str | None = None,
    ) -> dict[str, Any]:
        if not self.oidc_enabled or not self.oidc_provider:
            raise OidcProviderError("OIDC is not configured")
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(16)
        if redirect_uri:
            self.oidc_provider.redirect_uri = redirect_uri
        self._pending[state] = {
            "nonce": nonce,
            "org_id": org_id or self.default_org_id,
            "created_at": time.time(),
        }
        # Drop stale states
        cutoff = time.time() - 600
        self._pending = {k: v for k, v in self._pending.items() if v["created_at"] >= cutoff}
        url = await self.oidc_provider.authorization_url(
            state=state, code_challenge=code_challenge, nonce=nonce
        )
        return {"authorizationUrl": url, "state": state, "nonce": nonce}

    async def complete_oidc(
        self,
        *,
        code: str,
        state: str,
        code_verifier: str,
        org_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.oidc_enabled or not self.oidc_provider:
            raise OidcProviderError("OIDC is not configured")
        pending = self._pending.pop(state, None)
        nonce = pending.get("nonce") if pending else None
        resolved_org = (pending or {}).get("org_id") or org_id or self.default_org_id

        tokens = await self.oidc_provider.exchange_code(code=code, code_verifier=code_verifier)
        claims = await self.oidc_provider.validate_id_token(tokens["id_token"], nonce=nonce)

        user = await self.identity.get_user_by_email(resolved_org, claims.email)
        if not user:
            user = await self.identity.create_user(
                resolved_org, claims.email, claims.name, external_id=claims.subject
            )
        elif claims.subject and not user.external_id:
            # Best-effort bind external id when store supports update via recreate path.
            user.external_id = claims.subject

        return self._session_response(
            user.id,
            user.email,
            resolved_org,
            user.teams,
            provider="oidc",
            extras={
                "idTokenClaims": {
                    "sub": claims.subject,
                    "iss": claims.issuer,
                    "email": claims.email,
                }
            },
        )

    def authenticate(self, authorization_header: str | None) -> AuthContext | None:
        if not authorization_header:
            return None
        token = authorization_header.replace("Bearer ", "").strip()
        return self.oidc.validate_token(token)

    def _session_response(
        self,
        user_id: str,
        email: str,
        org_id: str,
        teams: list[str] | None,
        *,
        provider: str,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        team_principals = [f"team:{t}" for t in (teams or [])] if teams else [f"user:{email}"]
        token = self.oidc.create_token(
            user_id, email, org_id, team_principals, provider=provider
        )
        out: dict[str, Any] = {
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresIn": 3600,
            "user": {"id": user_id, "email": email},
            "sessionId": new_id("sso"),
            "provider": provider,
        }
        if extras:
            out.update(extras)
        return out
