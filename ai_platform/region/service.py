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
TELEMETRY_MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "007_edge_telemetry.sql"

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
        if self.sql.kind == "sqlite" and TELEMETRY_MIGRATION.exists():
            await self.sql.migrate_script(TELEMETRY_MIGRATION.read_text())
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

    async def record_edge_telemetry(
        self, node_id: str, events: list[dict[str, Any]] | None = None
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE edge_runtimes SET last_telemetry_at = ?, status = 'online' WHERE id = ?",
            now,
            node_id,
        )
        rows = events or [{"type": "heartbeat"}]
        stored = 0
        for ev in rows:
            if not isinstance(ev, dict):
                continue
            event_id = new_id("telem")
            latency = ev.get("latencyMs", ev.get("latency_ms"))
            success = ev.get("success")
            if success is not None:
                success = 1 if bool(success) else 0
            await self.sql.execute(
                "INSERT INTO edge_telemetry_events "
                "(id, node_id, event_type, latency_ms, success, payload_json, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                event_id,
                node_id,
                str(ev.get("type") or ev.get("eventType") or "heartbeat"),
                float(latency) if latency is not None else None,
                success,
                json.dumps(ev),
                now,
            )
            stored += 1
        return stored

    async def list_edge_telemetry(
        self,
        *,
        node_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        lim = max(1, min(limit, 500))
        if node_id:
            rows = await self.sql.fetchall(
                "SELECT * FROM edge_telemetry_events WHERE node_id = ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                node_id,
                lim,
            )
        else:
            rows = await self.sql.fetchall(
                "SELECT * FROM edge_telemetry_events ORDER BY recorded_at DESC LIMIT ?",
                lim,
            )
        out: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload_json") or "{}"
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload) if payload else {}
                except json.JSONDecodeError:
                    payload = {}
            success = row.get("success")
            if success is not None:
                success = bool(success)
            out.append(
                {
                    "id": row["id"],
                    "nodeId": row["node_id"],
                    "eventType": row.get("event_type") or "heartbeat",
                    "latencyMs": row.get("latency_ms"),
                    "success": success,
                    "payload": payload if isinstance(payload, dict) else {},
                    "recordedAt": row.get("recorded_at"),
                }
            )
        return out

    async def telemetry_summary(self, *, hours: int = 24) -> dict[str, Any]:
        """Aggregate recent telemetry for Studio charts."""
        events = await self.list_edge_telemetry(limit=500)
        # Keep newest-first list; chart wants chronological for bars.
        cutoff = datetime.now(timezone.utc).timestamp() - max(1, hours) * 3600
        filtered: list[dict[str, Any]] = []
        for ev in events:
            raw = ev.get("recordedAt")
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError):
                ts = datetime.now(timezone.utc).timestamp()
            if ts >= cutoff:
                filtered.append({**ev, "_ts": ts})
        filtered.sort(key=lambda e: e["_ts"])

        # Bucket into up to 12 time slots.
        buckets = 12
        series: list[dict[str, Any]] = []
        if filtered:
            start = filtered[0]["_ts"]
            end = filtered[-1]["_ts"]
            span = max(end - start, 1.0)
            slot = span / buckets
            for i in range(buckets):
                lo = start + i * slot
                hi = lo + slot
                group = [e for e in filtered if lo <= e["_ts"] < hi or (i == buckets - 1 and e["_ts"] <= hi)]
                latencies = [
                    float(e["latencyMs"])
                    for e in group
                    if e.get("latencyMs") is not None
                ]
                ok = sum(1 for e in group if e.get("success") is not False)
                series.append(
                    {
                        "index": i,
                        "count": len(group),
                        "successRate": (ok / len(group)) if group else None,
                        "avgLatencyMs": (
                            round(sum(latencies) / len(latencies), 2) if latencies else None
                        ),
                    }
                )
        nodes = await self.list_edge_nodes(limit=200)
        online = sum(1 for n in nodes if n.get("status") == "online")
        return {
            "hours": hours,
            "eventCount": len(filtered),
            "nodeCount": len(nodes),
            "onlineCount": online,
            "series": series,
            "recent": [{k: v for k, v in e.items() if k != "_ts"} for e in filtered[-20:]],
        }

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
