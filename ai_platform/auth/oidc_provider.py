"""Real OIDC provider integration (Okta, Azure AD, Keycloak, etc.).

Supports authorization-code + PKCE, OIDC discovery, and JWKS-validated ID tokens.
Platform session JWTs remain HMAC-signed via ``OidcValidator`` after a successful IdP login.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def _b64url_json(data: str) -> dict[str, Any]:
    return json.loads(_b64url_decode(data).decode())


@dataclass
class OidcClaims:
    subject: str
    email: str
    name: str | None
    issuer: str
    audience: str | list[str]
    raw: dict[str, Any]


class OidcProviderError(Exception):
    pass


class OidcProvider:
    """OIDC client for enterprise IdPs."""

    def __init__(
        self,
        issuer: str,
        client_id: str,
        *,
        client_secret: str | None = None,
        redirect_uri: str = "http://localhost:5173/",
        scopes: str = "openid profile email",
        audience: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        discovery: dict[str, Any] | None = None,
        jwks: dict[str, Any] | None = None,
    ) -> None:
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.audience = audience or client_id
        self._http = http_client
        self._discovery = discovery
        self._jwks = jwks
        self._jwks_fetched_at = time.time() if jwks is not None else 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.issuer and self.client_id)

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=20.0)
        return self._http

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def discover(self) -> dict[str, Any]:
        if self._discovery:
            return self._discovery
        client = await self._client()
        url = f"{self.issuer}/.well-known/openid-configuration"
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise OidcProviderError(f"OIDC discovery failed ({resp.status_code}): {resp.text}")
        self._discovery = resp.json()
        return self._discovery

    async def jwks(self, *, force: bool = False) -> dict[str, Any]:
        if self._jwks and not force and (time.time() - self._jwks_fetched_at) < 3600:
            return self._jwks
        discovery = await self.discover()
        jwks_uri = discovery.get("jwks_uri")
        if not jwks_uri:
            raise OidcProviderError("OIDC discovery missing jwks_uri")
        client = await self._client()
        resp = await client.get(jwks_uri)
        if resp.status_code >= 400:
            raise OidcProviderError(f"JWKS fetch failed ({resp.status_code})")
        self._jwks = resp.json()
        self._jwks_fetched_at = time.time()
        return self._jwks

    async def authorization_url(
        self,
        *,
        state: str,
        code_challenge: str,
        nonce: str | None = None,
        extra: dict[str, str] | None = None,
    ) -> str:
        discovery = await self.discover()
        endpoint = discovery.get("authorization_endpoint")
        if not endpoint:
            raise OidcProviderError("OIDC discovery missing authorization_endpoint")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if nonce:
            params["nonce"] = nonce
        if extra:
            params.update(extra)
        return f"{endpoint}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
    ) -> dict[str, Any]:
        discovery = await self.discover()
        token_endpoint = discovery.get("token_endpoint")
        if not token_endpoint:
            raise OidcProviderError("OIDC discovery missing token_endpoint")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)
        client = await self._client()
        resp = await client.post(token_endpoint, data=data, headers=headers, auth=auth)
        if resp.status_code >= 400:
            raise OidcProviderError(f"Token exchange failed ({resp.status_code}): {resp.text}")
        payload = resp.json()
        if "id_token" not in payload:
            raise OidcProviderError("Token response missing id_token")
        return payload

    async def validate_id_token(self, id_token: str, *, nonce: str | None = None) -> OidcClaims:
        parts = id_token.split(".")
        if len(parts) != 3:
            raise OidcProviderError("Malformed id_token")
        header = _b64url_json(parts[0])
        payload = _b64url_json(parts[1])
        alg = header.get("alg", "RS256")
        if alg not in {"RS256", "RS384", "RS512"}:
            raise OidcProviderError(f"Unsupported id_token alg: {alg}")

        await self._verify_signature(header, parts[0], parts[1], parts[2])

        now = int(time.time())
        if int(payload.get("exp", 0)) < now:
            raise OidcProviderError("id_token expired")
        if int(payload.get("nbf", 0)) > now + 60:
            raise OidcProviderError("id_token not yet valid")
        iss = str(payload.get("iss") or "")
        # Azure sometimes returns issuer with trailing slash differences.
        if iss.rstrip("/") != self.issuer.rstrip("/"):
            raise OidcProviderError(f"id_token issuer mismatch: {iss}")
        aud = payload.get("aud")
        allowed = {self.audience, self.client_id}
        if isinstance(aud, list):
            if not allowed.intersection({str(a) for a in aud}):
                raise OidcProviderError("id_token audience mismatch")
        elif str(aud) not in allowed:
            raise OidcProviderError("id_token audience mismatch")
        if nonce and payload.get("nonce") and payload.get("nonce") != nonce:
            raise OidcProviderError("id_token nonce mismatch")

        email = (
            payload.get("email")
            or payload.get("preferred_username")
            or payload.get("upn")
            or ""
        )
        name = payload.get("name") or payload.get("given_name")
        sub = str(payload.get("sub") or "")
        if not sub:
            raise OidcProviderError("id_token missing sub")
        if not email:
            # Some IdPs put email only in profile; fall back to sub-based address.
            email = f"{sub}@oidc.local"
        return OidcClaims(
            subject=sub,
            email=str(email),
            name=str(name) if name else None,
            issuer=iss,
            audience=aud if isinstance(aud, (str, list)) else str(aud),
            raw=payload,
        )

    async def _verify_signature(
        self, header: dict[str, Any], h_b64: str, p_b64: str, sig_b64: str
    ) -> None:
        jwks = await self.jwks()
        kid = header.get("kid")
        keys = jwks.get("keys") or []
        jwk = None
        for candidate in keys:
            if kid and candidate.get("kid") == kid:
                jwk = candidate
                break
            if not kid and candidate.get("kty") == "RSA":
                jwk = candidate
                break
        if not jwk:
            # Refresh once in case of rotation.
            jwks = await self.jwks(force=True)
            for candidate in jwks.get("keys") or []:
                if kid and candidate.get("kid") == kid:
                    jwk = candidate
                    break
        if not jwk:
            raise OidcProviderError("No matching JWKS key for id_token")
        if jwk.get("kty") != "RSA":
            raise OidcProviderError("Only RSA JWKS keys are supported")

        n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
        public_key = RSAPublicNumbers(e, n).public_key()
        alg = header.get("alg", "RS256")
        hash_alg = {
            "RS256": hashes.SHA256(),
            "RS384": hashes.SHA384(),
            "RS512": hashes.SHA512(),
        }[alg]
        try:
            public_key.verify(
                _b64url_decode(sig_b64),
                f"{h_b64}.{p_b64}".encode(),
                padding.PKCS1v15(),
                hash_alg,
            )
        except Exception as exc:  # noqa: BLE001
            raise OidcProviderError("id_token signature invalid") from exc

    def public_config(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "clientId": self.client_id,
            "redirectUri": self.redirect_uri,
            "scopes": self.scopes,
            "audience": self.audience,
            "hasClientSecret": bool(self.client_secret),
        }


def rsa_jwk_from_private_pem(private_pem: bytes, kid: str = "test-key") -> tuple[dict[str, Any], Any]:
    """Test helper: build a JWK + private key for minting RS256 tokens."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = serialization.load_pem_private_key(private_pem, password=None)
    assert isinstance(key, rsa.RSAPrivateKey)
    pub = key.public_key().public_numbers()

    def b64int(val: int) -> str:
        length = (val.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(val.to_bytes(length, "big")).rstrip(b"=").decode()

    jwk = {"kty": "RSA", "kid": kid, "use": "sig", "alg": "RS256", "n": b64int(pub.n), "e": b64int(pub.e)}
    return jwk, key


def mint_rs256_jwt(private_key: Any, header: dict[str, Any], payload: dict[str, Any]) -> str:
    def b64(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(obj, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()

    h = b64(header)
    p = b64(payload)
    sig = private_key.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
    s = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    return f"{h}.{p}.{s}"
