"""AMTP-inspired federation — domain discovery + cross-domain message forwarding."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.messaging.bus import MessageBus, SendMessageRequest


class FederatedDomain(BaseModel):
    domain: str
    gateway: str
    version: str = "1.0"
    auth: list[str] = Field(default_factory=lambda: ["api_key"])
    features: list[str] = Field(default_factory=lambda: ["inbox", "push", "schemas"])
    discovered_at: datetime | None = None


class FederationRegistry:
    """In-memory + optional HTTP discovery of peer AMTP gateways."""

    def __init__(self) -> None:
        self._domains: dict[str, FederatedDomain] = {}

    def register(self, domain: FederatedDomain) -> None:
        domain.discovered_at = datetime.now(timezone.utc)
        self._domains[domain.domain] = domain

    def get(self, domain: str) -> FederatedDomain | None:
        return self._domains.get(domain)

    def list(self) -> list[FederatedDomain]:
        return list(self._domains.values())

    async def discover_http(self, gateway_url: str) -> FederatedDomain:
        """GET {gateway}/.well-known/amtp or /v1/federation/info"""
        base = gateway_url.rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            for path in ("/.well-known/amtp", "/v1/federation/info"):
                try:
                    resp = await client.get(base + path)
                    if resp.status_code == 200:
                        data = resp.json()
                        domain = FederatedDomain(
                            domain=data.get("domain") or urlparse(base).hostname or "unknown",
                            gateway=data.get("gateway", base),
                            version=data.get("version", "1.0"),
                            auth=data.get("auth", ["api_key"]),
                            features=data.get("features", []),
                        )
                        self.register(domain)
                        return domain
                except Exception:
                    continue
        # Fallback: register gateway as opaque domain
        host = urlparse(base).hostname or "peer"
        domain = FederatedDomain(domain=host, gateway=base)
        self.register(domain)
        return domain


def parse_agent_address(address: str) -> tuple[str, str | None]:
    """Return (local_name, domain) — domain None means local."""
    if "@" in address:
        local, domain = address.rsplit("@", 1)
        return local, domain
    return address, None


class FederationGateway:
    """Send to local inbox or forward to peer domain gateway."""

    def __init__(
        self,
        local_domain: str,
        message_bus: MessageBus,
        registry: FederationRegistry | None = None,
        api_keys: dict[str, str] | None = None,
    ) -> None:
        self.local_domain = local_domain
        self.bus = message_bus
        self.registry = registry or FederationRegistry()
        self.api_keys = api_keys or {}

    def info(self) -> dict[str, Any]:
        return {
            "domain": self.local_domain,
            "gateway": f"local://{self.local_domain}",
            "version": "1.0",
            "auth": ["api_key"],
            "features": ["inbox", "push", "federation"],
            "peers": [d.model_dump(mode="json") for d in self.registry.list()],
        }

    async def send_federated(
        self,
        namespace_id: str,
        sender: str,
        recipient: str,
        payload: dict[str, Any],
        subject: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        local_name, domain = parse_agent_address(recipient)

        # Normalize local recipients
        if domain is None or domain == self.local_domain:
            # Ensure address form for local bus
            local_recipient = recipient if recipient.startswith("agents/") else f"agents/{local_name}"
            if "@" in local_recipient:
                local_recipient = f"agents/{local_name}"
            msg = await self.bus.send(
                namespace_id,
                SendMessageRequest(
                    sender=sender,
                    recipient=local_recipient,
                    subject=subject,
                    payload=payload,
                    idempotency_key=idempotency_key,
                ),
            )
            return {"mode": "local", "message": msg.model_dump(mode="json")}

        peer = self.registry.get(domain)
        if not peer:
            raise ValueError(f"Unknown federated domain: {domain}. Discover/register it first.")

        headers = {"Content-Type": "application/json"}
        key = self.api_keys.get(domain)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        body = {
            "sender": sender if "@" in sender else f"{sender}@{self.local_domain}",
            "recipient": recipient,
            "subject": subject,
            "payload": payload,
            "idempotencyKey": idempotency_key or new_id("fed"),
            "originDomain": self.local_domain,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{peer.gateway.rstrip('/')}/v1/federation/inbound",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            return {"mode": "federated", "domain": domain, "response": resp.json()}

    async def receive_inbound(
        self, namespace_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle message from a peer gateway."""
        recipient = body.get("recipient", "")
        local_name, domain = parse_agent_address(recipient)
        if domain and domain != self.local_domain:
            raise ValueError(f"Inbound message not for this domain ({self.local_domain})")
        local_recipient = f"agents/{local_name}" if not local_name.startswith("agents/") else local_name
        msg = await self.bus.send(
            namespace_id,
            SendMessageRequest(
                sender=body.get("sender", "unknown@peer"),
                recipient=local_recipient,
                subject=body.get("subject"),
                payload=body.get("payload") or {},
                idempotency_key=body.get("idempotencyKey"),
            ),
        )
        return {"accepted": True, "messageId": msg.id}
