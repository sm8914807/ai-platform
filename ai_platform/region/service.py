"""Multi-region control plane routing and failover (SQLite or Postgres)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import RegionConfig
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "004_phase4.sql"

DEFAULT_REGIONS = [
    {"name": "us-east-1", "endpoint": "http://localhost:8080", "data_residency": "us", "is_primary": True},
    {"name": "eu-west-1", "endpoint": "http://localhost:8081", "data_residency": "eu", "is_primary": False},
]


class RegionService:
    def __init__(
        self, db_path: str | None = None, sql: SqlBackend | None = None
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())
        await self._seed_defaults()

    async def _seed_defaults(self) -> None:
        existing = await self.list_regions()
        if existing:
            return
        for r in DEFAULT_REGIONS:
            await self.register_region(
                r["name"], r["endpoint"], r["data_residency"], r["is_primary"]
            )

    async def register_region(
        self,
        name: str,
        endpoint: str,
        data_residency: str | None = None,
        is_primary: bool = False,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        if is_primary:
            await self.sql.execute("UPDATE regions SET is_primary = ?", False)
        existing = await self.sql.fetchone("SELECT id FROM regions WHERE name = ?", name)
        if existing:
            await self.sql.execute(
                "UPDATE regions SET endpoint = ?, data_residency = ?, is_primary = ?, "
                "status = 'active' WHERE id = ?",
                endpoint,
                data_residency,
                is_primary,
                existing["id"],
            )
            return existing["id"]
        region_id = new_id("region")
        await self.sql.execute(
            "INSERT INTO regions (id, name, endpoint, data_residency, is_primary, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            region_id,
            name,
            endpoint,
            data_residency,
            is_primary,
            now,
        )
        return region_id

    async def list_regions(self) -> list[RegionConfig]:
        rows = await self.sql.fetchall(
            "SELECT * FROM regions ORDER BY is_primary DESC, name"
        )
        return [
            RegionConfig(
                id=r["id"],
                name=r["name"],
                endpoint=r["endpoint"],
                data_residency=r["data_residency"],
                is_primary=bool(r["is_primary"]),
                status=r["status"],
            )
            for r in rows
        ]

    async def get_primary(self) -> RegionConfig | None:
        regions = await self.list_regions()
        for r in regions:
            if r.is_primary and r.status == "active":
                return r
        return regions[0] if regions else None

    async def resolve_for_residency(self, residency: str | None) -> RegionConfig | None:
        regions = await self.list_regions()
        if residency:
            for r in regions:
                if r.data_residency == residency and r.status == "active":
                    return r
        return await self.get_primary()

    async def failover(self, failed_region_name: str) -> RegionConfig | None:
        await self.sql.execute(
            "UPDATE regions SET status = 'offline' WHERE name = ?", failed_region_name
        )
        regions = await self.list_regions()
        for r in regions:
            if r.name != failed_region_name and r.status == "active":
                await self.set_primary(r.name)
                return r
        return None

    async def set_primary(self, name: str) -> None:
        await self.sql.execute("UPDATE regions SET is_primary = ?", False)
        await self.sql.execute(
            "UPDATE regions SET is_primary = ?, status = 'active' WHERE name = ?",
            True,
            name,
        )

    async def register_edge_node(
        self,
        namespace_id: str,
        region_name: str | None,
        bundle_hash: str | None,
        cache_path: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        node_id = new_id("edge")
        now = datetime.now(timezone.utc).isoformat()
        region_id = None
        if region_name:
            for r in await self.list_regions():
                if r.name == region_name:
                    region_id = r.id
                    break
        await self.sql.execute(
            "INSERT INTO edge_runtimes "
            "(id, namespace_id, region_id, node_type, bundle_hash, bundle_cache_path, "
            "last_sync_at, metadata_json, status, created_at) "
            "VALUES (?, ?, ?, 'edge', ?, ?, ?, ?, 'online', ?)",
            node_id,
            namespace_id,
            region_id,
            bundle_hash,
            cache_path,
            now,
            json.dumps(metadata or {}),
            now,
        )
        return node_id

    async def record_edge_telemetry(self, node_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE edge_runtimes SET last_telemetry_at = ? WHERE id = ?", now, node_id
        )

    async def list_edge_nodes(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self.sql.fetchall(
            "SELECT e.*, r.name AS region_name FROM edge_runtimes e "
            "LEFT JOIN regions r ON r.id = e.region_id "
            "ORDER BY e.created_at DESC LIMIT ?",
            max(1, min(limit, 500)),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            meta = row.get("metadata_json") or "{}"
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta) if meta else {}
                except json.JSONDecodeError:
                    meta = {}
            out.append(
                {
                    "id": row["id"],
                    "namespaceId": row["namespace_id"],
                    "regionId": row.get("region_id"),
                    "regionName": row.get("region_name"),
                    "nodeType": row.get("node_type") or "edge",
                    "bundleHash": row.get("bundle_hash"),
                    "bundleCachePath": row.get("bundle_cache_path"),
                    "lastSyncAt": row.get("last_sync_at"),
                    "lastTelemetryAt": row.get("last_telemetry_at"),
                    "status": row.get("status") or "online",
                    "metadata": meta if isinstance(meta, dict) else {},
                    "createdAt": row.get("created_at"),
                }
            )
        return out
