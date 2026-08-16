"""SQLite-backed registry store."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from ai_platform.core.ids import new_id
from ai_platform.core.models import AuditEvent, PlatformResource, ResourceKind
from ai_platform.registry.store import RegistryStore, ResourceRecord, ResourceVersionRecord

MIGRATION_SQL = Path(__file__).parent.parent.parent / "migrations" / "001_initial.sql"


class SqliteRegistryStore(RegistryStore):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    async def _connect(self) -> aiosqlite.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path, timeout=30.0)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=30000")
        return conn

    async def migrate(self) -> None:
        sql = MIGRATION_SQL.read_text()
        # SQLite doesn't support JSONB — adapt types
        sql = sql.replace("JSONB", "TEXT").replace("TIMESTAMPTZ", "TEXT")
        conn = await self._connect()
        await conn.executescript(sql)
        migration_002 = Path(__file__).parent.parent.parent / "migrations" / "002_phase2.sql"
        if migration_002.exists():
            await conn.executescript(migration_002.read_text())
        migration_003 = Path(__file__).parent.parent.parent / "migrations" / "003_phase3.sql"
        if migration_003.exists():
            await conn.executescript(migration_003.read_text())
        migration_004 = Path(__file__).parent.parent.parent / "migrations" / "004_phase4.sql"
        if migration_004.exists():
            await conn.executescript(migration_004.read_text())
        migration_005 = Path(__file__).parent.parent.parent / "migrations" / "005_differentiators.sql"
        if migration_005.exists():
            await conn.executescript(migration_005.read_text())
        migration_006 = Path(__file__).parent.parent.parent / "migrations" / "006_messaging.sql"
        if migration_006.exists():
            await conn.executescript(migration_006.read_text())
        await conn.commit()
        await conn.close()

    async def ensure_namespace(self, namespace_path: str, env: str) -> str:
        conn = await self._connect()
        parts = namespace_path.split("/")
        org_name = parts[0] if parts else "default-org"
        project_name = parts[1] if len(parts) > 1 else "default-project"

        await conn.execute(
            "INSERT OR IGNORE INTO organizations (id, name) VALUES (?, ?)",
            (org_name, org_name),
        )
        row = await conn.execute_fetchall(
            "SELECT id FROM organizations WHERE name = ?", (org_name,)
        )
        org_id = row[0][0]

        project_id = f"{org_id}:{project_name}"
        await conn.execute(
            "INSERT OR IGNORE INTO projects (id, org_id, name) VALUES (?, ?, ?)",
            (project_id, org_id, project_name),
        )

        ns_id = f"{project_id}:{namespace_path}:{env}"
        await conn.execute(
            "INSERT OR IGNORE INTO namespaces (id, project_id, path, env) VALUES (?, ?, ?, ?)",
            (ns_id, project_id, namespace_path, env),
        )
        await conn.commit()
        await conn.close()
        return ns_id

    async def upsert_resource_version(
        self,
        namespace_id: str,
        resource: PlatformResource,
        author_id: str | None = None,
        commit_message: str | None = None,
    ) -> ResourceVersionRecord:
        conn = await self._connect()
        now = datetime.now(timezone.utc).isoformat()
        kind = resource.kind.value
        name = resource.metadata.name
        version = resource.metadata.version

        row = await conn.execute_fetchall(
            "SELECT id FROM resources WHERE namespace_id = ? AND kind = ? AND name = ?",
            (namespace_id, kind, name),
        )
        if row:
            resource_id = row[0][0]
            await conn.execute(
                "UPDATE resources SET latest_version = ?, updated_at = ? WHERE id = ?",
                (version, now, resource_id),
            )
        else:
            resource_id = new_id("res")
            await conn.execute(
                "INSERT INTO resources (id, namespace_id, kind, name, latest_version, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (resource_id, namespace_id, kind, name, version, now, now),
            )

        version_id = new_id("ver")
        status_json = resource.status.model_dump() if resource.status else {}
        await conn.execute(
            "INSERT INTO resource_versions "
            "(id, resource_id, version, spec_json, status_json, author_id, commit_message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                resource_id,
                version,
                json.dumps(resource.spec),
                json.dumps(status_json),
                author_id,
                commit_message,
                now,
            ),
        )
        await conn.commit()
        await conn.close()

        return ResourceVersionRecord(
            id=version_id,
            resource_id=resource_id,
            version=version,
            spec_json=resource.spec,
            status_json=status_json,
            author_id=author_id,
            commit_message=commit_message,
            bundle_hash=None,
            created_at=datetime.fromisoformat(now),
            kind=kind,
            name=name,
        )

    async def get_resource(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceRecord | None:
        conn = await self._connect()
        rows = await conn.execute_fetchall(
            "SELECT * FROM resources WHERE namespace_id = ? AND kind = ? AND name = ?",
            (namespace_id, kind.value, name),
        )
        await conn.close()
        if not rows:
            return None
        r = rows[0]
        return ResourceRecord(
            id=r["id"],
            namespace_id=r["namespace_id"],
            kind=r["kind"],
            name=r["name"],
            latest_version=r["latest_version"],
            published_version=r["published_version"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    async def get_version(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> ResourceVersionRecord | None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            return None
        conn = await self._connect()
        rows = await conn.execute_fetchall(
            "SELECT * FROM resource_versions WHERE resource_id = ? AND version = ?",
            (resource.id, version),
        )
        await conn.close()
        if not rows:
            return None
        v = rows[0]
        return ResourceVersionRecord(
            id=v["id"],
            resource_id=v["resource_id"],
            version=v["version"],
            spec_json=json.loads(v["spec_json"]),
            status_json=json.loads(v["status_json"]),
            author_id=v["author_id"],
            commit_message=v["commit_message"],
            bundle_hash=v["bundle_hash"],
            created_at=datetime.fromisoformat(v["created_at"]),
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
        conn = await self._connect()
        rows = await conn.execute_fetchall(
            "SELECT r.kind, r.name, r.published_version FROM resources r "
            "WHERE r.namespace_id = ? AND r.published_version IS NOT NULL",
            (namespace_id,),
        )
        await conn.close()
        out: list[ResourceVersionRecord] = []
        for kind, name, ver in rows:
            v = await self.get_version(namespace_id, ResourceKind(kind), name, ver)
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
        conn = await self._connect()
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "UPDATE resources SET published_version = ?, updated_at = ? WHERE id = ?",
            (version, now, resource.id),
        )
        await conn.commit()
        await conn.close()

    async def unpublish(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            raise ValueError(f"Resource not found: {kind.value}/{name}")
        conn = await self._connect()
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "UPDATE resources SET published_version = NULL, updated_at = ? WHERE id = ?",
            (now, resource.id),
        )
        await conn.commit()
        await conn.close()

    async def list_namespaces(self) -> list[dict[str, Any]]:
        conn = await self._connect()
        rows = await conn.execute_fetchall(
            "SELECT id, path, env FROM namespaces ORDER BY path, env"
        )
        await conn.close()
        return [{"id": r[0], "path": r[1], "env": r[2]} for r in rows]

    async def set_bundle_hash(self, resource_version_id: str, bundle_hash: str) -> None:
        conn = await self._connect()
        await conn.execute(
            "UPDATE resource_versions SET bundle_hash = ? WHERE id = ?",
            (bundle_hash, resource_version_id),
        )
        await conn.commit()
        await conn.close()

    async def append_audit(self, event: AuditEvent) -> AuditEvent:
        conn = await self._connect()
        await conn.execute(
            "INSERT INTO audit_events (id, org_id, actor_id, action, resource_ref, payload_json, ip, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.org_id,
                event.actor_id,
                event.action,
                event.resource_ref,
                json.dumps(event.payload),
                event.ip,
                event.created_at.isoformat(),
            ),
        )
        await conn.commit()
        await conn.close()
        return event

    async def register_runtime_node(
        self, namespace_id: str, node_type: str = "sdk", metadata: dict[str, Any] | None = None
    ) -> str:
        conn = await self._connect()
        node_id = new_id("node")
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO runtime_nodes (id, namespace_id, node_type, last_heartbeat, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (node_id, namespace_id, node_type, now, json.dumps(metadata or {}), now),
        )
        await conn.commit()
        await conn.close()
        return node_id
