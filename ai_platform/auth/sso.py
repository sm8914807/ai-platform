"""OIDC / JWT authentication for control plane API."""

import hashlib
import hmac
import json
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from typing import Any

from ai_platform.auth.identity import IdentityStore
from ai_platform.core.ids import new_id


@dataclass
class AuthContext:
    user_id: str
    email: str
    org_id: str
    principal: str
    provider: str


class OidcValidator:
    """Phase 3: HMAC-signed JWT for dev; swap for real OIDC in production."""

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
    ) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {
            "sub": user_id,
            "email": email,
            "org_id": org_id,
            "teams": teams or [],
            "iss": self.issuer,
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
            if parts[2] != expected_sig:
                return None
            teams = payload.get("teams", [])
            principal = teams[0] if teams else f"user:{payload.get('email', '')}"
            return AuthContext(
                user_id=payload["sub"],
                email=payload.get("email", ""),
                org_id=payload.get("org_id", "default-org"),
                principal=principal,
                provider="oidc",
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
    """Login flow bridging identity store and OIDC tokens."""

    def __init__(
        self, identity_store: IdentityStore, oidc: OidcValidator | None = None
    ) -> None:
        self.identity = identity_store
        self.oidc = oidc or OidcValidator(secret="dev-platform-secret-change-in-prod")

    async def login(self, org_id: str, email: str, display_name: str | None = None) -> dict[str, Any]:
        user = await self.identity.get_user_by_email(org_id, email)
        if not user:
            user = await self.identity.create_user(org_id, email, display_name)
        teams = [f"team:{t}" for t in user.teams] if user.teams else [f"user:{email}"]
        token = self.oidc.create_token(user.id, user.email, org_id, teams)
        session_id = new_id("sso")
        return {
            "accessToken": token,
            "tokenType": "Bearer",
            "expiresIn": 3600,
            "user": {"id": user.id, "email": user.email},
            "sessionId": session_id,
        }

    def authenticate(self, authorization_header: str | None) -> AuthContext | None:
        if not authorization_header:
            return None
        token = authorization_header.replace("Bearer ", "").strip()
        return self.oidc.validate_token(token)
