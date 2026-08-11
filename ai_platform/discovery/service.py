"""Agent capability discovery & routing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "005_differentiators.sql"


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else []
    return list(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class AgentCapabilityRecord(BaseModel):
    id: str
    namespace_id: str
    agent_ref: str
    address: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    delivery_mode: str = "pull"
    status: str = "online"
    last_active: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RegisterCapabilityRequest(BaseModel):
    agent_ref: str
    address: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    delivery_mode: str = "pull"
    metadata: dict[str, Any] = Field(default_factory=dict)


class DiscoveryQuery(BaseModel):
    capabilities: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    status: str = "online"
    limit: int = 10


class AgentDiscoveryService:
    """Capability-based agent discovery — route to best agent for a task."""

    def __init__(
        self,
        db_path: str | None = None,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        # no-op or sqlite-only script; centralized migrate_aux_stores handles full migrate
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())

    async def register(
        self, namespace_id: str, req: RegisterCapabilityRequest
    ) -> AgentCapabilityRecord:
        now = datetime.now(timezone.utc)
        existing = await self.sql.fetchone(
            "SELECT id FROM agent_capabilities WHERE namespace_id = ? AND agent_ref = ?",
            namespace_id,
            req.agent_ref,
        )
        if existing:
            cap_id = existing["id"]
            await self.sql.execute(
                "UPDATE agent_capabilities SET address = ?, capabilities_json = ?, "
                "schemas_json = ?, delivery_mode = ?, status = 'online', last_active = ?, "
                "metadata_json = ? WHERE id = ?",
                req.address or req.agent_ref,
                json.dumps(req.capabilities),
                json.dumps(req.schemas),
                req.delivery_mode,
                now.isoformat(),
                json.dumps(req.metadata),
                cap_id,
            )
        else:
            cap_id = new_id("cap")
            await self.sql.execute(
                "INSERT INTO agent_capabilities "
                "(id, namespace_id, agent_ref, address, capabilities_json, schemas_json, "
                "delivery_mode, status, last_active, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'online', ?, ?, ?)",
                cap_id,
                namespace_id,
                req.agent_ref,
                req.address or req.agent_ref,
                json.dumps(req.capabilities),
                json.dumps(req.schemas),
                req.delivery_mode,
                now.isoformat(),
                json.dumps(req.metadata),
                now.isoformat(),
            )
        return AgentCapabilityRecord(
            id=cap_id,
            namespace_id=namespace_id,
            agent_ref=req.agent_ref,
            address=req.address or req.agent_ref,
            capabilities=req.capabilities,
            schemas=req.schemas,
            delivery_mode=req.delivery_mode,
            status="online",
            last_active=now,
            metadata=req.metadata,
            created_at=now,
        )

    async def discover(
        self, namespace_id: str, query: DiscoveryQuery
    ) -> list[AgentCapabilityRecord]:
        agents = await self.list_agents(namespace_id)
        scored: list[tuple[float, AgentCapabilityRecord]] = []
        for agent in agents:
            if query.status and agent.status != query.status:
                continue
            score = 0.0
            if query.capabilities:
                overlap = set(query.capabilities).intersection(set(agent.capabilities))
                if not overlap:
                    continue
                score += len(overlap) / max(len(query.capabilities), 1)
            if query.schemas:
                schema_overlap = set(query.schemas).intersection(set(agent.schemas))
                score += 0.5 * len(schema_overlap) / max(len(query.schemas), 1)
            if not query.capabilities and not query.schemas:
                score = 1.0
            scored.append((score, agent))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [a for _, a in scored[: query.limit]]

    async def route_best(
        self, namespace_id: str, required_capabilities: list[str]
    ) -> AgentCapabilityRecord | None:
        results = await self.discover(
            namespace_id,
            DiscoveryQuery(capabilities=required_capabilities, limit=1),
        )
        return results[0] if results else None

    async def list_agents(self, namespace_id: str) -> list[AgentCapabilityRecord]:
        rows = await self.sql.fetchall(
            "SELECT * FROM agent_capabilities WHERE namespace_id = ? ORDER BY agent_ref",
            namespace_id,
        )
        return [self._row_to_record(r) for r in rows]

    async def heartbeat(self, namespace_id: str, agent_ref: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE agent_capabilities SET last_active = ?, status = 'online' "
            "WHERE namespace_id = ? AND agent_ref = ?",
            now,
            namespace_id,
            agent_ref,
        )

    async def sync_from_bundle(
        self, namespace_id: str, bundle: dict[str, dict]
    ) -> int:
        """Register published Agent resources into the discovery index."""
        count = 0
        for key, doc in bundle.items():
            if not key.startswith("Agent:"):
                continue
            name = doc.get("name") or key.split(":", 1)[1]
            spec = doc.get("spec", {})
            caps = spec.get("capabilities", [])
            if not caps:
                role = spec.get("role", "executor")
                caps = [role, f"agent:{name}"]
            await self.register(
                namespace_id,
                RegisterCapabilityRequest(
                    agent_ref=f"agents/{name}",
                    address=f"{name}@platform.local",
                    capabilities=caps,
                    schemas=spec.get("schemas", []),
                    metadata={"role": spec.get("role"), "fromBundle": True},
                ),
            )
            count += 1
        return count

    def _row_to_record(self, row: dict[str, Any]) -> AgentCapabilityRecord:
        return AgentCapabilityRecord(
            id=row["id"],
            namespace_id=row["namespace_id"],
            agent_ref=row["agent_ref"],
            address=row["address"],
            capabilities=_as_list(row["capabilities_json"]),
            schemas=_as_list(row["schemas_json"]),
            delivery_mode=row["delivery_mode"],
            status=row["status"],
            last_active=_parse_dt(row["last_active"]),
            metadata=_as_dict(row["metadata_json"]),
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
        )
