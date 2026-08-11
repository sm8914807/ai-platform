"""Lightweight AMTP-inspired inter-agent message bus (SQLite or Postgres)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.db.sql import SqlBackend, SqliteBackend, create_sql_backend


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class AgentMessage(BaseModel):
    id: str
    namespace_id: str
    sender: str
    recipient: str
    subject: str | None = None
    schema_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "delivered", "acked", "failed"] = "pending"
    delivery_mode: Literal["pull", "push"] = "pull"
    idempotency_key: str | None = None
    attempt: int = 0
    created_at: datetime
    delivered_at: datetime | None = None
    acked_at: datetime | None = None


class SendMessageRequest(BaseModel):
    sender: str
    recipient: str
    subject: str | None = None
    schema_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_mode: Literal["pull", "push"] = "pull"
    idempotency_key: str | None = None


class RegisterInboxRequest(BaseModel):
    agent_address: str
    delivery_mode: Literal["pull", "push"] = "pull"
    webhook_url: str | None = None


class MessageBus:
    """At-least-once agent messaging with pull inbox + optional push webhook."""

    def __init__(
        self,
        db_path: str | None = None,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        # Aux migrate is centralized; keep no-op-friendly for unit tests on sqlite.
        if isinstance(self.sql, SqliteBackend):
            from ai_platform.db.sql import SQLITE_MIGRATIONS

            for path in SQLITE_MIGRATIONS:
                if path.name == "006_messaging.sql" and path.exists():
                    await self.sql.migrate_script(path.read_text())

    async def register_inbox(
        self, namespace_id: str, req: RegisterInboxRequest
    ) -> dict[str, Any]:
        inbox_id = new_id("inbox")
        now = datetime.now(timezone.utc).isoformat()
        existing = await self.sql.fetchone(
            "SELECT id FROM agent_inboxes WHERE namespace_id = ? AND agent_address = ?",
            namespace_id,
            req.agent_address,
        )
        if existing:
            await self.sql.execute(
                "UPDATE agent_inboxes SET delivery_mode = ?, webhook_url = ? WHERE id = ?",
                req.delivery_mode,
                req.webhook_url,
                existing["id"],
            )
        else:
            await self.sql.execute(
                "INSERT INTO agent_inboxes "
                "(id, namespace_id, agent_address, delivery_mode, webhook_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                inbox_id,
                namespace_id,
                req.agent_address,
                req.delivery_mode,
                req.webhook_url,
                now,
            )
        return {
            "agentAddress": req.agent_address,
            "deliveryMode": req.delivery_mode,
            "webhookUrl": req.webhook_url,
        }

    async def send(self, namespace_id: str, req: SendMessageRequest) -> AgentMessage:
        if req.idempotency_key:
            existing = await self._by_idempotency(namespace_id, req.idempotency_key)
            if existing:
                return existing

        msg_id = new_id("msg")
        now = datetime.now(timezone.utc)
        status: Literal["pending", "delivered", "acked", "failed"] = "pending"
        delivered_at: datetime | None = None
        error: dict[str, Any] | None = None

        delivery_mode = req.delivery_mode
        webhook_url = None
        inbox = await self._get_inbox(namespace_id, req.recipient)
        if inbox:
            delivery_mode = inbox["delivery_mode"]  # type: ignore[assignment]
            webhook_url = inbox.get("webhook_url")

        if delivery_mode == "push" and webhook_url:
            try:
                await self._push_webhook(webhook_url, req)
                status = "delivered"
                delivered_at = datetime.now(timezone.utc)
            except Exception as e:
                status = "failed"
                error = {"message": str(e)}

        try:
            await self.sql.execute(
                "INSERT INTO agent_messages "
                "(id, namespace_id, sender, recipient, subject, schema_id, payload_json, "
                "status, delivery_mode, idempotency_key, attempt, error_json, created_at, delivered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                msg_id,
                namespace_id,
                req.sender,
                req.recipient,
                req.subject,
                req.schema_id,
                json.dumps(req.payload),
                status,
                delivery_mode,
                req.idempotency_key,
                1 if status != "pending" else 0,
                json.dumps(error) if error else None,
                now.isoformat(),
                delivered_at.isoformat() if delivered_at else None,
            )
        except Exception:
            if req.idempotency_key:
                existing = await self._by_idempotency(namespace_id, req.idempotency_key)
                if existing:
                    return existing
            raise

        return AgentMessage(
            id=msg_id,
            namespace_id=namespace_id,
            sender=req.sender,
            recipient=req.recipient,
            subject=req.subject,
            schema_id=req.schema_id,
            payload=req.payload,
            status=status,
            delivery_mode=delivery_mode,
            idempotency_key=req.idempotency_key,
            attempt=1 if status != "pending" else 0,
            created_at=now,
            delivered_at=delivered_at,
        )

    async def pull_inbox(
        self, namespace_id: str, agent_address: str, limit: int = 20
    ) -> list[AgentMessage]:
        rows = await self.sql.fetchall(
            "SELECT * FROM agent_messages WHERE namespace_id = ? AND recipient = ? "
            "AND status = 'pending' ORDER BY created_at ASC LIMIT ?",
            namespace_id,
            agent_address,
            limit,
        )
        now = datetime.now(timezone.utc).isoformat()
        messages: list[AgentMessage] = []
        for row in rows:
            await self.sql.execute(
                "UPDATE agent_messages SET status = 'delivered', delivered_at = ?, "
                "attempt = attempt + 1 WHERE id = ?",
                now,
                row["id"],
            )
            msg = self._row_to_msg(row)
            msg.status = "delivered"
            msg.delivered_at = datetime.now(timezone.utc)
            messages.append(msg)
        return messages

    async def ack(self, message_id: str) -> AgentMessage | None:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE agent_messages SET status = 'acked', acked_at = ? WHERE id = ?",
            now,
            message_id,
        )
        row = await self.sql.fetchone(
            "SELECT * FROM agent_messages WHERE id = ?", message_id
        )
        return self._row_to_msg(row) if row else None

    async def list_messages(
        self, namespace_id: str, agent_address: str | None = None, limit: int = 50
    ) -> list[AgentMessage]:
        if agent_address:
            rows = await self.sql.fetchall(
                "SELECT * FROM agent_messages WHERE namespace_id = ? AND "
                "(sender = ? OR recipient = ?) ORDER BY created_at DESC LIMIT ?",
                namespace_id,
                agent_address,
                agent_address,
                limit,
            )
        else:
            rows = await self.sql.fetchall(
                "SELECT * FROM agent_messages WHERE namespace_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                namespace_id,
                limit,
            )
        return [self._row_to_msg(r) for r in rows]

    async def _get_inbox(self, namespace_id: str, address: str) -> dict[str, Any] | None:
        return await self.sql.fetchone(
            "SELECT * FROM agent_inboxes WHERE namespace_id = ? AND agent_address = ?",
            namespace_id,
            address,
        )

    async def _by_idempotency(
        self, namespace_id: str, key: str
    ) -> AgentMessage | None:
        row = await self.sql.fetchone(
            "SELECT * FROM agent_messages WHERE namespace_id = ? AND idempotency_key = ?",
            namespace_id,
            key,
        )
        return self._row_to_msg(row) if row else None

    async def _push_webhook(self, url: str, req: SendMessageRequest) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "sender": req.sender,
                    "recipient": req.recipient,
                    "subject": req.subject,
                    "schemaId": req.schema_id,
                    "payload": req.payload,
                },
                headers={"X-AMTP-Local-Delivery": "true"},
            )
            resp.raise_for_status()

    def _row_to_msg(self, row: dict[str, Any]) -> AgentMessage:
        return AgentMessage(
            id=row["id"],
            namespace_id=row["namespace_id"],
            sender=row["sender"],
            recipient=row["recipient"],
            subject=row["subject"],
            schema_id=row["schema_id"],
            payload=_as_dict(row["payload_json"]),
            status=row["status"],
            delivery_mode=row["delivery_mode"],
            idempotency_key=row["idempotency_key"],
            attempt=row["attempt"],
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
            delivered_at=_parse_dt(row["delivered_at"]),
            acked_at=_parse_dt(row["acked_at"]),
        )
