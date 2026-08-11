"""Pluggable memory backends."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import MemoryEntry, MemoryProfileSpec


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
        matched = [
            e for e in entries if q in str(e.content).lower()
        ]
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


class MemoryService:
    """Layered memory per MemoryProfile spec."""

    def __init__(self, backend: MemoryBackend | None = None) -> None:
        self._backend = backend or InMemoryBackend()
        self._snapshots: dict[str, list[MemoryEntry]] = {}

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
