"""Postgres-backed registry store for multi-tenant SaaS."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import AuditEvent, PlatformResource, ResourceKind
from ai_platform.db.backend import PgPool
from ai_platform.registry.store import RegistryStore, ResourceRecord, ResourceVersionRecord

PG_CORE = Path(__file__).parent.parent.parent / "migrations" / "postgres" / "001_core.sql"


class PostgresRegistryStore(RegistryStore):
    def __init__(self, dsn: str) -> None:
        self.pool = PgPool(dsn)

    async def migrate(self) -> None:
        sql = PG_CORE.read_text()
        await self.pool.executemany_script(sql)

    async def close(self) -> None:
        await self.pool.close()

    async def ensure_namespace(self, namespace_path: str, env: str) -> str:
        parts = namespace_path.split("/")
        org_name = parts[0] if parts else "default-org"
        project_name = parts[1] if len(parts) > 1 else "default-project"

        await self.pool.execute(
            "INSERT INTO organizations (id, name) VALUES ($1, $2) ON CONFLICT (id) DO NOTHING",
            org_name,
            org_name,
        )
        project_id = f"{org_name}:{project_name}"
        await self.pool.execute(
            "INSERT INTO projects (id, org_id, name) VALUES ($1, $2, $3) ON CONFLICT (id) DO NOTHING",
            project_id,
            org_name,
            project_name,
        )
        ns_id = f"{project_id}:{namespace_path}:{env}"
        await self.pool.execute(
            "INSERT INTO namespaces (id, project_id, path, env) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (id) DO NOTHING",
            ns_id,
            project_id,
            namespace_path,
            env,
        )
        return ns_id

    async def upsert_resource_version(
        self,
        namespace_id: str,
        resource: PlatformResource,
        author_id: str | None = None,
        commit_message: str | None = None,
    ) -> ResourceVersionRecord:
        now = datetime.now(timezone.utc)
        kind = resource.kind.value
        name = resource.metadata.name
        version = resource.metadata.version

        row = await self.pool.fetchrow(
            "SELECT id FROM resources WHERE namespace_id = $1 AND kind = $2 AND name = $3",
            namespace_id,
            kind,
            name,
        )
        if row:
            resource_id = row["id"]
            await self.pool.execute(
                "UPDATE resources SET latest_version = $1, updated_at = $2 WHERE id = $3",
                version,
                now,
                resource_id,
            )
        else:
            resource_id = new_id("res")
            await self.pool.execute(
                "INSERT INTO resources (id, namespace_id, kind, name, latest_version, created_at, updated_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                resource_id,
                namespace_id,
                kind,
                name,
                version,
                now,
                now,
            )

        version_id = new_id("ver")
        status_json = resource.status.model_dump() if resource.status else {}
        await self.pool.execute(
            "INSERT INTO resource_versions "
            "(id, resource_id, version, spec_json, status_json, author_id, commit_message, created_at) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8) "
            "ON CONFLICT (resource_id, version) DO UPDATE SET "
            "spec_json = EXCLUDED.spec_json, status_json = EXCLUDED.status_json, "
            "author_id = EXCLUDED.author_id, commit_message = EXCLUDED.commit_message",
            version_id,
            resource_id,
            version,
            json.dumps(resource.spec),
            json.dumps(status_json),
            author_id,
            commit_message,
            now,
        )
        return ResourceVersionRecord(
            id=version_id,
            resource_id=resource_id,
            version=version,
            spec_json=resource.spec,
            status_json=status_json,
            author_id=author_id,
            commit_message=commit_message,
            bundle_hash=None,
            created_at=now,
            kind=kind,
            name=name,
        )

    async def get_resource(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceRecord | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM resources WHERE namespace_id = $1 AND kind = $2 AND name = $3",
            namespace_id,
            kind.value,
            name,
        )
        if not row:
            return None
        return ResourceRecord(
            id=row["id"],
            namespace_id=row["namespace_id"],
            kind=row["kind"],
            name=row["name"],
            latest_version=row["latest_version"],
            published_version=row["published_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_version(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> ResourceVersionRecord | None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            return None
        row = await self.pool.fetchrow(
            "SELECT * FROM resource_versions WHERE resource_id = $1 AND version = $2",
            resource.id,
            version,
        )
        if not row:
            return None
        spec = row["spec_json"]
        status = row["status_json"]
        if isinstance(spec, str):
            spec = json.loads(spec)
        if isinstance(status, str):
            status = json.loads(status)
        return ResourceVersionRecord(
            id=row["id"],
            resource_id=row["resource_id"],
            version=row["version"],
            spec_json=dict(spec),
            status_json=dict(status),
            author_id=row["author_id"],
            commit_message=row["commit_message"],
            bundle_hash=row["bundle_hash"],
            created_at=row["created_at"],
            kind=resource.kind,
            name=resource.name,
        )

    async def get_published_version(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceVersionRecord | None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource or not resource.published_version:
            return None
        return await self.get_version(namespace_id, kind, name, resource.published_version)

    async def list_published(self, namespace_id: str) -> list[ResourceVersionRecord]:
        rows = await self.pool.fetch(
            "SELECT kind, name, published_version FROM resources "
            "WHERE namespace_id = $1 AND published_version IS NOT NULL",
            namespace_id,
        )
        out: list[ResourceVersionRecord] = []
        for r in rows:
            v = await self.get_version(
                namespace_id, ResourceKind(r["kind"]), r["name"], r["published_version"]
            )
            if v:
                out.append(v)
        return out

    async def publish(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            raise ValueError(f"Resource not found: {kind.value}/{name}")
        ver = await self.get_version(namespace_id, kind, name, version)
        if not ver:
            raise ValueError(f"Version not found: {version}")
        await self.pool.execute(
            "UPDATE resources SET published_version = $1, updated_at = $2 WHERE id = $3",
            version,
            datetime.now(timezone.utc),
            resource.id,
        )

    async def set_bundle_hash(self, resource_version_id: str, bundle_hash: str) -> None:
        await self.pool.execute(
            "UPDATE resource_versions SET bundle_hash = $1 WHERE id = $2",
            bundle_hash,
            resource_version_id,
        )

    async def append_audit(self, event: AuditEvent) -> AuditEvent:
        await self.pool.execute(
            "INSERT INTO audit_events (id, org_id, actor_id, action, resource_ref, payload_json, ip, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)",
            event.id,
            event.org_id,
            event.actor_id,
            event.action,
            event.resource_ref,
            json.dumps(event.payload),
            event.ip,
            event.created_at,
        )
        return event

    async def register_runtime_node(
        self, namespace_id: str, node_type: str = "sdk", metadata: dict[str, Any] | None = None
    ) -> str:
        node_id = new_id("node")
        now = datetime.now(timezone.utc)
        await self.pool.execute(
            "INSERT INTO runtime_nodes (id, namespace_id, node_type, last_heartbeat, metadata_json, created_at) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6)",
            node_id,
            namespace_id,
            node_type,
            now,
            json.dumps(metadata or {}),
            now,
        )
        return node_id
