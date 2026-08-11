"""AMTP 1.0 federation — DNS discovery, fan-out, schemas, status, inbox auth."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.db.sql import SqlBackend, create_sql_backend
from ai_platform.messaging.bus import MessageBus, SendMessageRequest

# ---------------------------------------------------------------------------
# IDs (UUIDv7-ish + UUIDv4 idempotency)
# ---------------------------------------------------------------------------


def uuidv7() -> str:
    """Time-ordered UUID (v7-compatible shape)."""
    try:
        return str(uuid.uuid7())  # type: ignore[attr-defined]
    except AttributeError:
        ms = int(time.time() * 1000)
        rand = uuid.uuid4().int & ((1 << 62) - 1)
        value = (ms << 80) | (0x7 << 76) | rand
        return str(uuid.UUID(int=value & ((1 << 128) - 1)))


def uuidv4() -> str:
    return str(uuid.uuid4())


def idempotency_from_content(payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


# ---------------------------------------------------------------------------
# Protocol types
# ---------------------------------------------------------------------------


class AMTPCapabilities(BaseModel):
    version: str = "1.0"
    domain: str
    gateway: str
    auth: list[str] = Field(default_factory=lambda: ["apikey", "none"])
    max_size: int = 10_485_760
    features: list[str] = Field(
        default_factory=lambda: [
            "agent-discovery",
            "schema-validation",
            "inbox",
            "push",
            "federation",
        ]
    )
    schemas: list[str] = Field(default_factory=list)
    discovered_at: datetime | None = None
    ttl_seconds: int = 300


class AMTPMessage(BaseModel):
    version: str = "1.0"
    message_id: str = Field(default_factory=uuidv7)
    idempotency_key: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sender: str
    recipients: list[str] = Field(default_factory=list)
    subject: str | None = None
    schema_: str | None = Field(default=None, alias="schema")
    headers: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    coordination: dict[str, Any] | None = None
    signature: dict[str, Any] | None = None
    in_reply_to: str | None = None
    response_type: str | None = None
    workflow_id: str | None = None

    model_config = {"populate_by_name": True}


class RecipientStatus(BaseModel):
    recipient: str
    status: Literal[
        "pending", "queued", "delivering", "delivered", "failed", "retrying"
    ] = "pending"
    attempt: int = 0
    error: dict[str, Any] | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MessageStatus(BaseModel):
    message_id: str
    status: str
    recipients: list[RecipientStatus] = Field(default_factory=list)


class LocalAmtpAgent(BaseModel):
    address: str  # local name or full agent@domain
    api_key: str | None = None
    delivery_mode: Literal["pull", "push"] = "pull"
    push_target: str | None = None
    supported_schemas: list[str] = Field(default_factory=list)
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


ADDRESS_RE = re.compile(r"^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$|^[A-Za-z0-9._+/-]+$")


def parse_address(address: str) -> tuple[str, str | None]:
    if "@" in address:
        local, domain = address.rsplit("@", 1)
        return local, domain
    return address, None


def format_address(local: str, domain: str) -> str:
    local = local.removeprefix("agents/")
    return f"{local}@{domain}"


# ---------------------------------------------------------------------------
# DNS discovery
# ---------------------------------------------------------------------------


class DnsDiscovery:
    """Resolve `_amtp.{domain}` TXT records (with in-memory TTL cache)."""

    def __init__(self, allow_http: bool | None = None, ttl: int = 300) -> None:
        self.allow_http = (
            allow_http
            if allow_http is not None
            else os.getenv("PLATFORM_AMTP_ALLOW_HTTP", "true").lower() == "true"
        )
        self.ttl = ttl
        self._cache: dict[str, tuple[float, AMTPCapabilities]] = {}

    def _lookup_txt(self, name: str) -> list[str]:
        try:
            import dns.resolver  # type: ignore

            answers = dns.resolver.resolve(name, "TXT")
            out: list[str] = []
            for rdata in answers:
                parts = getattr(rdata, "strings", None) or [bytes(rdata)]
                text = "".join(
                    p.decode() if isinstance(p, bytes) else str(p) for p in parts
                )
                out.append(text)
            return out
        except Exception:
            # Fallback: system getaddrinfo won't do TXT; use dns via socket stub / empty
            return []

    def parse_txt(self, domain: str, records: list[str]) -> AMTPCapabilities | None:
        for rec in records:
            if "v=amtp1" not in rec and "v=amtp" not in rec:
                continue
            fields: dict[str, str] = {}
            for part in rec.split(";"):
                part = part.strip()
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k.strip()] = v.strip()
            gateway = fields.get("gateway", "")
            if not gateway:
                continue
            parsed = urlparse(gateway)
            if parsed.scheme == "http" and not self.allow_http:
                raise ValueError(f"HTTP gateway not allowed for {domain}")
            auth = [a.strip() for a in fields.get("auth", "none").split(",") if a.strip()]
            features = [
                f.strip() for f in fields.get("features", "").split(",") if f.strip()
            ]
            max_size = int(fields.get("max-size", "10485760"))
            return AMTPCapabilities(
                domain=domain,
                gateway=gateway.rstrip("/"),
                auth=auth or ["none"],
                max_size=max_size,
                features=features
                or ["agent-discovery", "schema-validation", "inbox", "push"],
                discovered_at=datetime.now(timezone.utc),
                ttl_seconds=self.ttl,
            )
        return None

    async def discover(self, domain: str) -> AMTPCapabilities:
        cached = self._cache.get(domain)
        if cached and cached[0] > time.time():
            return cached[1]

        name = f"_amtp.{domain}"
        records = await asyncio.to_thread(self._lookup_txt, name)
        caps = self.parse_txt(domain, records)
        if not caps:
            # Dev fallback: treat domain as host with platform gateway path
            host = domain
            scheme = "http" if self.allow_http else "https"
            port = os.getenv("PLATFORM_AMTP_FALLBACK_PORT", "8080")
            gateway = f"{scheme}://{host}:{port}"
            caps = AMTPCapabilities(
                domain=domain,
                gateway=gateway,
                discovered_at=datetime.now(timezone.utc),
                features=["inbox", "push", "federation"],
            )
        self._cache[domain] = (time.time() + self.ttl, caps)
        return caps

    def register_static(self, caps: AMTPCapabilities) -> None:
        self._cache[caps.domain] = (time.time() + caps.ttl_seconds, caps)


# ---------------------------------------------------------------------------
# Schema registry + validation
# ---------------------------------------------------------------------------


class SchemaRegistry:
    def __init__(self, sql: SqlBackend) -> None:
        self.sql = sql

    async def put(self, schema_id: str, definition: dict[str, Any], version: str = "1.0") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        existing = await self.sql.fetchone(
            "SELECT id FROM amtp_schemas WHERE schema_id = ?", schema_id
        )
        if existing:
            await self.sql.execute(
                "UPDATE amtp_schemas SET definition_json = ?, version = ? WHERE id = ?",
                json.dumps(definition),
                version,
                existing["id"],
            )
            rid = existing["id"]
        else:
            rid = new_id("asch")
            await self.sql.execute(
                "INSERT INTO amtp_schemas (id, schema_id, version, definition_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rid,
                schema_id,
                version,
                json.dumps(definition),
                now,
            )
        return {"id": rid, "schemaId": schema_id, "version": version}

    async def list(self) -> list[dict[str, Any]]:
        rows = await self.sql.fetchall(
            "SELECT schema_id, version, definition_json, created_at FROM amtp_schemas"
        )
        out = []
        for r in rows:
            defn = r["definition_json"]
            if isinstance(defn, str):
                defn = json.loads(defn)
            out.append(
                {
                    "schemaId": r["schema_id"],
                    "version": r["version"],
                    "definition": defn,
                    "createdAt": r["created_at"],
                }
            )
        return out

    async def get(self, schema_id: str) -> dict[str, Any] | None:
        row = await self.sql.fetchone(
            "SELECT * FROM amtp_schemas WHERE schema_id = ?", schema_id
        )
        if not row:
            return None
        defn = row["definition_json"]
        if isinstance(defn, str):
            defn = json.loads(defn)
        return {"schemaId": row["schema_id"], "version": row["version"], "definition": defn}

    def matches(self, schema_id: str | None, patterns: list[str]) -> bool:
        if not schema_id:
            return True  # unstructured allowed
        if not patterns:
            return True
        for p in patterns:
            if p.endswith(".*"):
                if schema_id.startswith(p[:-1]):
                    return True
            elif p == schema_id or p == "*":
                return True
        return False


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class AMTPGateway:
    """Full AMTP-style gateway on top of the local message bus."""

    def __init__(
        self,
        domain: str,
        message_bus: MessageBus,
        sql: SqlBackend | None = None,
        namespace_id: str | None = None,
        admin_key: str | None = None,
        max_size: int = 10_485_760,
    ) -> None:
        self.domain = domain
        self.bus = message_bus
        self.sql = sql or message_bus.sql
        self.default_namespace = namespace_id or "default-org/default-project:development"
        self.admin_key = admin_key or os.getenv("PLATFORM_AMTP_ADMIN_KEY", "dev-admin-key")
        self.max_size = max_size
        self.dns = DnsDiscovery()
        self.schemas = SchemaRegistry(self.sql)
        self._agents: dict[str, LocalAmtpAgent] = {}
        # Seed local domain caps
        self.dns.register_static(
            AMTPCapabilities(
                domain=domain,
                gateway=f"local://{domain}",
                auth=["apikey", "none"],
                max_size=max_size,
            )
        )

    def info(self) -> dict[str, Any]:
        return {
            "version": "1.0",
            "domain": self.domain,
            "gateway": f"local://{self.domain}",
            "auth": ["apikey", "none"],
            "maxSize": self.max_size,
            "features": [
                "agent-discovery",
                "schema-validation",
                "inbox",
                "push",
                "federation",
            ],
            "agents": [a.model_dump() for a in self._agents.values()],
        }

    # --- Agent admin ---

    async def register_agent(self, agent: LocalAmtpAgent) -> LocalAmtpAgent:
        addr = agent.address
        if "@" not in addr:
            addr = format_address(addr, self.domain)
            agent = agent.model_copy(update={"address": addr})
        self._agents[addr] = agent
        key_hash = (
            hashlib.sha256(agent.api_key.encode()).hexdigest() if agent.api_key else None
        )
        now = datetime.now(timezone.utc).isoformat()
        existing = await self.sql.fetchone(
            "SELECT id FROM amtp_agents WHERE domain = ? AND address = ?",
            self.domain,
            addr,
        )
        if existing:
            await self.sql.execute(
                "UPDATE amtp_agents SET api_key_hash = ?, delivery_mode = ?, push_target = ?, "
                "supported_schemas_json = ?, active = ?, metadata_json = ? WHERE id = ?",
                key_hash,
                agent.delivery_mode,
                agent.push_target,
                json.dumps(agent.supported_schemas),
                1 if agent.active else 0,
                json.dumps(agent.metadata),
                existing["id"],
            )
        else:
            await self.sql.execute(
                "INSERT INTO amtp_agents "
                "(id, domain, address, api_key_hash, delivery_mode, push_target, "
                "supported_schemas_json, active, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                new_id("aagt"),
                self.domain,
                addr,
                key_hash,
                agent.delivery_mode,
                agent.push_target,
                json.dumps(agent.supported_schemas),
                1 if agent.active else 0,
                json.dumps(agent.metadata),
                now,
            )
        # Also register bus inbox
        from ai_platform.messaging.bus import RegisterInboxRequest

        local = parse_address(addr)[0]
        await self.bus.register_inbox(
            self.default_namespace,
            RegisterInboxRequest(
                agent_address=f"agents/{local}",
                delivery_mode=agent.delivery_mode,
                webhook_url=agent.push_target,
            ),
        )
        return agent

    async def list_agents(self, active_only: bool = False) -> list[dict[str, Any]]:
        rows = await self.sql.fetchall(
            "SELECT address, delivery_mode, push_target, supported_schemas_json, active, metadata_json "
            "FROM amtp_agents WHERE domain = ?",
            self.domain,
        )
        out = []
        for r in rows:
            if active_only and not r["active"]:
                continue
            schemas = r["supported_schemas_json"]
            if isinstance(schemas, str):
                schemas = json.loads(schemas)
            meta = r["metadata_json"]
            if isinstance(meta, str):
                meta = json.loads(meta)
            out.append(
                {
                    "address": r["address"],
                    "deliveryMode": r["delivery_mode"],
                    "pushTarget": r["push_target"],
                    "supportedSchemas": schemas,
                    "active": bool(r["active"]),
                    "metadata": meta,
                }
            )
        # Merge in-memory
        for a in self._agents.values():
            if not any(x["address"] == a.address for x in out):
                out.append(
                    {
                        "address": a.address,
                        "deliveryMode": a.delivery_mode,
                        "pushTarget": a.push_target,
                        "supportedSchemas": a.supported_schemas,
                        "active": a.active,
                        "metadata": a.metadata,
                    }
                )
        return out

    def verify_agent_key(self, address: str, bearer: str | None) -> bool:
        agent = self._agents.get(address)
        if agent and agent.api_key:
            return bearer == agent.api_key
        # Allow open inbox in dev when no key configured
        if agent and not agent.api_key:
            return True
        return bearer == self.admin_key

    # --- Capabilities ---

    async def capabilities(self, domain: str | None = None) -> AMTPCapabilities:
        d = domain or self.domain
        if d == self.domain:
            schemas = [s["schemaId"] for s in await self.schemas.list()]
            return AMTPCapabilities(
                domain=self.domain,
                gateway=f"local://{self.domain}",
                auth=["apikey", "none"],
                max_size=self.max_size,
                schemas=schemas,
                discovered_at=datetime.now(timezone.utc),
            )
        return await self.dns.discover(d)

    def register_peer(
        self, domain: str, gateway: str, auth: list[str] | None = None
    ) -> AMTPCapabilities:
        caps = AMTPCapabilities(
            domain=domain,
            gateway=gateway.rstrip("/"),
            auth=auth or ["apikey", "none"],
            discovered_at=datetime.now(timezone.utc),
        )
        self.dns.register_static(caps)
        return caps

    # --- Send / receive ---

    async def send(
        self,
        msg: AMTPMessage,
        namespace_id: str | None = None,
    ) -> dict[str, Any]:
        ns = namespace_id or self.default_namespace
        raw = msg.model_dump_json().encode()
        if len(raw) > self.max_size:
            raise ValueError("MESSAGE_TOO_LARGE")

        if not msg.idempotency_key:
            msg.idempotency_key = idempotency_from_content(
                {"sender": msg.sender, "recipients": msg.recipients, "payload": msg.payload}
            )

        # Normalize sender
        if "@" not in msg.sender:
            msg.sender = format_address(msg.sender, self.domain)

        recipients = msg.recipients or []
        if not recipients:
            raise ValueError("recipients required")

        statuses: list[RecipientStatus] = []
        results: list[dict[str, Any]] = []

        async def deliver_one(recipient: str) -> RecipientStatus:
            local, domain = parse_address(recipient)
            st = RecipientStatus(recipient=recipient, status="delivering")
            try:
                if domain is None or domain == self.domain:
                    await self._deliver_local(ns, msg, local)
                    st.status = "delivered"
                else:
                    await self._deliver_remote(msg, recipient, domain)
                    st.status = "delivered"
            except Exception as e:
                st.status = "failed"
                st.error = {"message": str(e), "code": "DELIVERY_FAILED"}
            st.attempt = 1
            st.updated_at = datetime.now(timezone.utc)
            await self._persist_status(msg.message_id, st)
            return st

        # Fan-out in parallel
        statuses = list(await asyncio.gather(*[deliver_one(r) for r in recipients]))
        overall = (
            "delivered"
            if all(s.status == "delivered" for s in statuses)
            else ("failed" if all(s.status == "failed" for s in statuses) else "partial")
        )
        return {
            "messageId": msg.message_id,
            "idempotencyKey": msg.idempotency_key,
            "status": overall,
            "recipients": [s.model_dump(mode="json") for s in statuses],
        }

    async def _deliver_local(self, ns: str, msg: AMTPMessage, local_name: str) -> None:
        addr = format_address(local_name, self.domain)
        agent = self._agents.get(addr)
        patterns = agent.supported_schemas if agent else []
        if not self.schemas.matches(msg.schema_, patterns):
            raise ValueError(f"SCHEMA_REJECTED: {msg.schema_}")

        bus_recipient = f"agents/{local_name}"
        await self.bus.send(
            ns,
            SendMessageRequest(
                sender=msg.sender,
                recipient=bus_recipient,
                subject=msg.subject,
                schema_id=msg.schema_,
                payload={
                    **msg.payload,
                    "_amtp": {
                        "messageId": msg.message_id,
                        "version": msg.version,
                        "workflowId": msg.workflow_id,
                        "inReplyTo": msg.in_reply_to,
                    },
                },
                delivery_mode=(agent.delivery_mode if agent else "pull"),
                idempotency_key=msg.idempotency_key,
            ),
        )

    async def _deliver_remote(
        self, msg: AMTPMessage, recipient: str, domain: str
    ) -> None:
        caps = await self.dns.discover(domain)
        if len(msg.model_dump_json().encode()) > caps.max_size:
            raise ValueError("MESSAGE_TOO_LARGE_REMOTE")

        body = msg.model_dump(mode="json", by_alias=True)
        body["recipients"] = [recipient]  # single-recipient hop
        body["timestamp"] = msg.timestamp.isoformat()

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "AMTP-Gateway/1.0",
            "X-AMTP-Version": "1.0",
        }
        url = f"{caps.gateway}/v1/messages"

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(url, headers=headers, json=body)
                    if resp.status_code < 400:
                        return
                    if 400 <= resp.status_code < 500 and resp.status_code != 429:
                        raise ValueError(f"remote {resp.status_code}: {resp.text[:200]}")
                    last_err = ValueError(f"remote {resp.status_code}")
            except httpx.HTTPError as e:
                last_err = e
            await asyncio.sleep(0.5 * (2**attempt))
        raise last_err or RuntimeError("remote delivery failed")

    async def receive(self, body: dict[str, Any], namespace_id: str | None = None) -> dict:
        """Inbound federated message (POST /v1/messages)."""
        msg = AMTPMessage.model_validate(body)
        # Ensure recipients are for this domain
        for r in msg.recipients:
            _, d = parse_address(r)
            if d and d != self.domain:
                raise ValueError(f"recipient domain mismatch: {r}")
        return await self.send(msg, namespace_id=namespace_id)

    async def _persist_status(self, message_id: str, st: RecipientStatus) -> None:
        now = st.updated_at.isoformat()
        existing = await self.sql.fetchone(
            "SELECT id FROM amtp_delivery_status WHERE message_id = ? AND recipient = ?",
            message_id,
            st.recipient,
        )
        if existing:
            await self.sql.execute(
                "UPDATE amtp_delivery_status SET status = ?, attempt = ?, error_json = ?, "
                "updated_at = ? WHERE id = ?",
                st.status,
                st.attempt,
                json.dumps(st.error) if st.error else None,
                now,
                existing["id"],
            )
        else:
            await self.sql.execute(
                "INSERT INTO amtp_delivery_status "
                "(id, message_id, recipient, status, attempt, error_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                new_id("ast"),
                message_id,
                st.recipient,
                st.status,
                st.attempt,
                json.dumps(st.error) if st.error else None,
                now,
            )

    async def get_status(self, message_id: str) -> MessageStatus:
        rows = await self.sql.fetchall(
            "SELECT recipient, status, attempt, error_json, updated_at "
            "FROM amtp_delivery_status WHERE message_id = ?",
            message_id,
        )
        recipients: list[RecipientStatus] = []
        for r in rows:
            err = r["error_json"]
            if isinstance(err, str) and err:
                err = json.loads(err)
            recipients.append(
                RecipientStatus(
                    recipient=r["recipient"],
                    status=r["status"],
                    attempt=r["attempt"],
                    error=err if isinstance(err, dict) else None,
                )
            )
        overall = "pending"
        if recipients:
            if all(x.status == "delivered" for x in recipients):
                overall = "delivered"
            elif all(x.status == "failed" for x in recipients):
                overall = "failed"
            else:
                overall = "partial"
        return MessageStatus(message_id=message_id, status=overall, recipients=recipients)

    def dns_txt_record(self, public_gateway: str) -> str:
        """Generate the `_amtp.{domain}` TXT value operators should publish."""
        return (
            f"v=amtp1;gateway={public_gateway.rstrip('/')};"
            f"auth=none,apikey;max-size={self.max_size};"
            f"features=agent-discovery,schema-validation,inbox,push,federation"
        )
