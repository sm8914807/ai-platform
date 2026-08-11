"""Secrets manager — encrypted at rest, short-lived resolve tokens (SQLite or Postgres)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet
from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.db.sql import SqlBackend, create_sql_backend

SECRETS_DDL = """
CREATE TABLE IF NOT EXISTS secrets (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  rotated_at TEXT,
  UNIQUE(namespace_id, name)
);
"""


def _fernet_from_key(key: str | None) -> Fernet:
    raw = key or os.getenv("PLATFORM_SECRETS_KEY") or "dev-secrets-key-change-me"
    digest = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


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


class SecretMeta(BaseModel):
    id: str
    namespace_id: str
    name: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    rotated_at: datetime | None = None


class SecretsManager:
    """Encrypt secrets at rest; resolve by name for tools/runtime."""

    def __init__(
        self,
        db_path: str | None = None,
        master_key: str | None = None,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")
        self._fernet = _fernet_from_key(master_key)
        self._leases: dict[str, dict[str, Any]] = {}

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite":
            await self.sql.migrate_script(SECRETS_DDL)

    async def put(
        self,
        namespace_id: str,
        name: str,
        value: str,
        metadata: dict[str, Any] | None = None,
    ) -> SecretMeta:
        ciphertext = self._fernet.encrypt(value.encode()).decode()
        now = datetime.now(timezone.utc)
        existing = await self.sql.fetchone(
            "SELECT id, created_at FROM secrets WHERE namespace_id = ? AND name = ?",
            namespace_id,
            name,
        )
        if existing:
            secret_id = existing["id"]
            await self.sql.execute(
                "UPDATE secrets SET ciphertext = ?, metadata_json = ?, rotated_at = ? WHERE id = ?",
                ciphertext,
                json.dumps(metadata or {}),
                now.isoformat(),
                secret_id,
            )
            created = _parse_dt(existing["created_at"]) or now
            rotated = now
        else:
            secret_id = new_id("sec")
            await self.sql.execute(
                "INSERT INTO secrets (id, namespace_id, name, ciphertext, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                secret_id,
                namespace_id,
                name,
                ciphertext,
                json.dumps(metadata or {}),
                now.isoformat(),
            )
            created = now
            rotated = None
        return SecretMeta(
            id=secret_id,
            namespace_id=namespace_id,
            name=name,
            metadata=metadata or {},
            created_at=created,
            rotated_at=rotated,
        )

    async def get(self, namespace_id: str, name: str) -> str | None:
        row = await self.sql.fetchone(
            "SELECT ciphertext FROM secrets WHERE namespace_id = ? AND name = ?",
            namespace_id,
            name,
        )
        if not row:
            return None
        return self._fernet.decrypt(row["ciphertext"].encode()).decode()

    async def list(self, namespace_id: str) -> list[SecretMeta]:
        rows = await self.sql.fetchall(
            "SELECT id, namespace_id, name, metadata_json, created_at, rotated_at "
            "FROM secrets WHERE namespace_id = ?",
            namespace_id,
        )
        out: list[SecretMeta] = []
        for r in rows:
            out.append(
                SecretMeta(
                    id=r["id"],
                    namespace_id=r["namespace_id"],
                    name=r["name"],
                    metadata=_as_dict(r["metadata_json"]),
                    created_at=_parse_dt(r["created_at"]) or datetime.now(timezone.utc),
                    rotated_at=_parse_dt(r["rotated_at"]),
                )
            )
        return out

    async def delete(self, namespace_id: str, name: str) -> bool:
        existing = await self.sql.fetchone(
            "SELECT id FROM secrets WHERE namespace_id = ? AND name = ?",
            namespace_id,
            name,
        )
        if not existing:
            return False
        await self.sql.execute(
            "DELETE FROM secrets WHERE namespace_id = ? AND name = ?",
            namespace_id,
            name,
        )
        return True

    def issue_lease(
        self, namespace_id: str, name: str, ttl_seconds: int = 300
    ) -> str:
        token = new_id("lease")
        self._leases[token] = {
            "namespace_id": namespace_id,
            "name": name,
            "expires_at": time.time() + ttl_seconds,
        }
        return token

    async def resolve_lease(self, token: str) -> str | None:
        lease = self._leases.get(token)
        if not lease:
            return None
        if time.time() > lease["expires_at"]:
            del self._leases[token]
            return None
        value = await self.get(lease["namespace_id"], lease["name"])
        del self._leases[token]
        return value
