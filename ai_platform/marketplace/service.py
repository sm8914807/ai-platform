"""Marketplace plugin catalog and installation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from ai_platform.core.ids import new_id
from ai_platform.core.models import PluginManifest, PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.registry.store import RegistryStore

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "003_phase3.sql"


class MarketplaceCatalog:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def migrate(self) -> None:
        conn = await aiosqlite.connect(self.db_path)
        if MIGRATION.exists():
            await conn.executescript(MIGRATION.read_text())
        await conn.commit()
        await conn.close()

    async def publish_plugin(
        self, name: str, manifest: PluginManifest, author: str | None = None
    ) -> str:
        plugin_id = new_id("plugin")
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT INTO marketplace_plugins "
            "(id, name, version, plugin_type, author, tier, manifest_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                plugin_id,
                name,
                manifest.version,
                manifest.type,
                author or manifest.author,
                manifest.tier,
                json.dumps(manifest.model_dump()),
                now,
            ),
        )
        await conn.commit()
        await conn.close()
        return plugin_id

    async def list_plugins(self, tier: str | None = None) -> list[dict[str, Any]]:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        if tier:
            rows = await conn.execute_fetchall(
                "SELECT * FROM marketplace_plugins WHERE tier = ? ORDER BY name", (tier,)
            )
        else:
            rows = await conn.execute_fetchall(
                "SELECT * FROM marketplace_plugins ORDER BY name, version"
            )
        await conn.close()
        return [dict(r) for r in rows]

    async def get_plugin(self, name: str, version: str | None = None) -> dict[str, Any] | None:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        if version:
            rows = await conn.execute_fetchall(
                "SELECT * FROM marketplace_plugins WHERE name = ? AND version = ?",
                (name, version),
            )
        else:
            rows = await conn.execute_fetchall(
                "SELECT * FROM marketplace_plugins WHERE name = ? ORDER BY version DESC LIMIT 1",
                (name,),
            )
        await conn.close()
        return dict(rows[0]) if rows else None


class MarketplaceService:
    def __init__(self, catalog: MarketplaceCatalog, registry: RegistryStore) -> None:
        self.catalog = catalog
        self.registry = registry

    async def install(
        self,
        namespace_id: str,
        namespace_path: str,
        plugin_name: str,
        version: str | None = None,
        installed_by: str | None = None,
    ) -> dict[str, Any]:
        plugin = await self.catalog.get_plugin(plugin_name, version)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_name}")

        manifest = PluginManifest.model_validate(json.loads(plugin["manifest_json"]))
        if manifest.tier == "community":
            pass  # allowed in all envs for Phase 3
        installed_resources: list[str] = []

        for res_doc in manifest.resources:
            kind_str = res_doc.get("kind")
            if not kind_str:
                continue
            meta = res_doc.get("metadata", {})
            resource = PlatformResource(
                kind=ResourceKind(kind_str),
                metadata=ResourceMetadata(
                    name=meta.get("name", plugin_name),
                    namespace=namespace_path,
                    version=meta.get("version", manifest.version),
                ),
                spec=res_doc.get("spec", {}),
            )
            await self.registry.upsert_resource_version(namespace_id, resource, installed_by)
            await self.registry.publish(
                namespace_id, resource.kind, resource.metadata.name, resource.metadata.version
            )
            installed_resources.append(f"{kind_str}/{resource.metadata.name}")

        install_id = new_id("inst")
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.catalog.db_path)
        await conn.execute(
            "INSERT INTO marketplace_installations (id, plugin_id, namespace_id, installed_by, installed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (install_id, plugin["id"], namespace_id, installed_by, now),
        )
        await conn.commit()
        await conn.close()

        return {
            "installationId": install_id,
            "plugin": plugin_name,
            "version": plugin["version"],
            "resources": installed_resources,
        }
