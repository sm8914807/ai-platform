"""Pluggable memory backends — in-process or durable SQL (SQLite / Postgres)."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import MemoryEntry, MemoryProfileSpec
from ai_platform.db.sql import SqlBackend, create_sql_backend


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _entry_from_row(r: dict[str, Any]) -> MemoryEntry:
    return MemoryEntry(
        id=r["id"],
        scope=r["scope"],
        layer=r["layer"],
        content=_as_dict(r.get("content_json")),
        version=int(r.get("version") or 1),
        created_at=_parse_dt(r["created_at"]),
    )


class MemoryBackend(ABC):
    @abstractmethod
    async def read(self, scope: str, layer: str, limit: int = 50) -> list[MemoryEntry]:
        ...

    @abstractmethod
    async def write(self, scope: str, layer: str, content: dict[str, Any]) -> MemoryEntry:
        ...

    @abstractmethod
    async def search(
        self, scope: str, layer: str, query: str, limit: int = 10
    ) -> list[MemoryEntry]:
        ...

    @abstractmethod
    async def snapshot(self, scope: str) -> list[MemoryEntry]:
        ...

    @abstractmethod
    async def replay(self, scope: str, from_version: int = 1) -> list[MemoryEntry]:
        ...


class InMemoryBackend(MemoryBackend):
    def __init__(self) -> None:
        self._entries: dict[str, list[MemoryEntry]] = {}

    def _key(self, scope: str, layer: str) -> str:
        return f"{scope}:{layer}"

    async def read(self, scope: str, layer: str, limit: int = 50) -> list[MemoryEntry]:
        entries = self._entries.get(self._key(scope, layer), [])
        return entries[-limit:]

    async def write(self, scope: str, layer: str, content: dict[str, Any]) -> MemoryEntry:
        key = self._key(scope, layer)
        entries = self._entries.setdefault(key, [])
        entry = MemoryEntry(
            id=new_id("mem"),
            scope=scope,
            layer=layer,
            content=content,
            version=len(entries) + 1,
            created_at=datetime.now(timezone.utc),
        )
        entries.append(entry)
        return entry

    async def search(
        self, scope: str, layer: str, query: str, limit: int = 10
    ) -> list[MemoryEntry]:
        entries = await self.read(scope, layer, limit=1000)
        q = query.lower()
        matched = [e for e in entries if q in str(e.content).lower()]
        return matched[:limit]

    async def snapshot(self, scope: str) -> list[MemoryEntry]:
        out: list[MemoryEntry] = []
        for key, entries in self._entries.items():
            if key.startswith(f"{scope}:"):
                out.extend(entries)
        return out

    async def replay(self, scope: str, from_version: int = 1) -> list[MemoryEntry]:
        all_entries = await self.snapshot(scope)
        return [e for e in all_entries if e.version >= from_version]


class SqlMemoryBackend(MemoryBackend):
    """Persists conversation / layer memory in ``memory_entries`` (+ optional snapshots)."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")

    async def read(self, scope: str, layer: str, limit: int = 50) -> list[MemoryEntry]:
        rows = await self.sql.fetchall(
            "SELECT * FROM memory_entries WHERE scope = ? AND layer = ? "
            "ORDER BY version ASC",
            scope,
            layer,
        )
        entries = [_entry_from_row(r) for r in rows]
        return entries[-limit:]

    async def write(self, scope: str, layer: str, content: dict[str, Any]) -> MemoryEntry:
        row = await self.sql.fetchone(
            "SELECT COALESCE(MAX(version), 0) AS v FROM memory_entries "
            "WHERE scope = ? AND layer = ?",
            scope,
            layer,
        )
        version = int((row or {}).get("v") or 0) + 1
        entry_id = new_id("mem")
        now = datetime.now(timezone.utc)
        await self.sql.execute(
            "INSERT INTO memory_entries "
            "(id, scope, layer, content_json, version, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            entry_id,
            scope,
            layer,
            json.dumps(content),
            version,
            now.isoformat(),
            None,
        )
        return MemoryEntry(
            id=entry_id,
            scope=scope,
            layer=layer,
            content=content,
            version=version,
            created_at=now,
        )

    async def search(
        self, scope: str, layer: str, query: str, limit: int = 10
    ) -> list[MemoryEntry]:
        entries = await self.read(scope, layer, limit=1000)
        q = query.lower()
        matched = [e for e in entries if q in str(e.content).lower()]
        return matched[:limit]

    async def snapshot(self, scope: str) -> list[MemoryEntry]:
        rows = await self.sql.fetchall(
            "SELECT * FROM memory_entries WHERE scope = ? ORDER BY version ASC",
            scope,
        )
        return [_entry_from_row(r) for r in rows]

    async def replay(self, scope: str, from_version: int = 1) -> list[MemoryEntry]:
        rows = await self.sql.fetchall(
            "SELECT * FROM memory_entries WHERE scope = ? AND version >= ? "
            "ORDER BY version ASC",
            scope,
            from_version,
        )
        return [_entry_from_row(r) for r in rows]

    async def save_snapshot(self, scope: str, version: int, entries: list[MemoryEntry]) -> None:
        payload = [
            {
                "id": e.id,
                "scope": e.scope,
                "layer": e.layer,
                "content": e.content,
                "version": e.version,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ]
        await self.sql.execute(
            "INSERT INTO memory_snapshots (id, scope, version, entries_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            new_id("msnap"),
            scope,
            version,
            json.dumps(payload),
            datetime.now(timezone.utc).isoformat(),
        )


class MemoryService:
    """Layered memory per MemoryProfile spec."""

    def __init__(self, backend: MemoryBackend | None = None) -> None:
        self._backend = backend or InMemoryBackend()
        self._snapshots: dict[str, list[MemoryEntry]] = {}

    @classmethod
    def durable(
        cls,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
    ) -> MemoryService:
        return cls(backend=SqlMemoryBackend(db_path=db_path, sql=sql))

    async def read(
        self, scope: str, profile: MemoryProfileSpec | None, layer: str = "conversation"
    ) -> list[MemoryEntry]:
        return await self._backend.read(scope, layer)

    async def write(
        self,
        scope: str,
        content: dict[str, Any],
        profile: MemoryProfileSpec | None = None,
        layer: str = "conversation",
    ) -> MemoryEntry:
        entry = await self._backend.write(scope, layer, content)
        if profile and profile.versioning:
            snap = await self._backend.snapshot(scope)
            if isinstance(self._backend, SqlMemoryBackend):
                await self._backend.save_snapshot(scope, entry.version, snap)
            else:
                self._snapshots[f"{scope}:{entry.version}"] = snap
        return entry

    async def search(
        self, scope: str, query: str, layer: str = "semantic", limit: int = 10
    ) -> list[MemoryEntry]:
        return await self._backend.search(scope, layer, query, limit)

    async def replay(self, scope: str, from_version: int = 1) -> list[MemoryEntry]:
        return await self._backend.replay(scope, from_version)

    def conversation_messages(self, entries: list[MemoryEntry]) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        for e in entries:
            role = e.content.get("role", "user")
            text = e.content.get("content", e.content.get("text", ""))
            messages.append({"role": role, "content": str(text)})
        return messages
