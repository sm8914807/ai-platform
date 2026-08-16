"""Git sync — apply/export YAML resources."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
import yaml

from ai_platform.core.ids import new_id
from ai_platform.core.models import GitSyncResult, PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.registry.store import RegistryStore

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "003_phase3.sql"


class GitSyncService:
    def __init__(self, registry: RegistryStore, db_path: str) -> None:
        self.registry = registry
        self.db_path = db_path

    async def migrate(self) -> None:
        conn = await aiosqlite.connect(self.db_path)
        if MIGRATION.exists():
            await conn.executescript(MIGRATION.read_text())
        await conn.commit()
        await conn.close()

    async def register_repo(self, namespace_id: str, repo_path: str, branch: str = "main") -> str:
        repo_id = new_id("git")
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT INTO git_sync_repos (id, namespace_id, repo_path, branch, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (repo_id, namespace_id, repo_path, branch, now),
        )
        await conn.commit()
        await conn.close()
        return repo_id

    async def list_repos(self, namespace_id: str) -> list[dict[str, Any]]:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            rows = await conn.execute_fetchall(
                "SELECT id, namespace_id, repo_path, branch, last_sync_at, last_commit, status, created_at "
                "FROM git_sync_repos WHERE namespace_id = ? ORDER BY created_at DESC",
                (namespace_id,),
            )
        except Exception:
            await conn.close()
            return []
        await conn.close()
        return [
            {
                "id": row["id"],
                "namespaceId": row["namespace_id"],
                "repoPath": row["repo_path"],
                "branch": row["branch"],
                "lastSyncAt": row["last_sync_at"],
                "lastCommit": row["last_commit"],
                "status": row["status"] or "unknown",
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    async def sync_from_directory(
        self,
        namespace_id: str,
        namespace_path: str,
        directory: Path,
        publish: bool = True,
        author: str | None = None,
    ) -> GitSyncResult:
        if not directory.exists() or not directory.is_dir():
            return GitSyncResult(
                repo_id="",
                applied=0,
                skipped=0,
                errors=[f"directory not found: {directory}"],
                commit=None,
            )
        repo_id = await self.register_repo(namespace_id, str(directory))
        applied = 0
        skipped = 0
        errors: list[str] = []

        for path in sorted(directory.rglob("*.yaml")) + sorted(directory.rglob("*.yml")):
            try:
                doc = yaml.safe_load(path.read_text())
                if not doc or not doc.get("kind"):
                    skipped += 1
                    continue
                kind = ResourceKind(doc["kind"])
                meta = doc["metadata"]
                resource = PlatformResource(
                    kind=kind,
                    metadata=ResourceMetadata(
                        name=meta["name"],
                        namespace=namespace_path,
                        version=meta.get("version", "1.0.0"),
                        labels=meta.get("labels", {}),
                    ),
                    spec=doc["spec"],
                )
                await self.registry.upsert_resource_version(
                    namespace_id, resource, author, f"git-sync:{path.name}"
                )
                if publish:
                    await self.registry.publish(
                        namespace_id, kind, resource.metadata.name, resource.metadata.version
                    )
                applied += 1
            except Exception as e:
                errors.append(f"{path.name}: {e}")

        commit = self._dir_fingerprint(directory)
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "UPDATE git_sync_repos SET last_sync_at = ?, last_commit = ?, status = ? WHERE id = ?",
            (now, commit, "synced" if not errors else "partial", repo_id),
        )
        await conn.commit()
        await conn.close()

        return GitSyncResult(
            repo_id=repo_id, applied=applied, skipped=skipped, errors=errors, commit=commit
        )

    async def export_to_directory(
        self, namespace_id: str, namespace_path: str, directory: Path
    ) -> int:
        directory.mkdir(parents=True, exist_ok=True)
        published = await self.registry.list_published(namespace_id)
        count = 0
        for ver in published:
            if not ver.kind or not ver.name:
                continue
            doc = {
                "apiVersion": "platform.ai/v1",
                "kind": ver.kind,
                "metadata": {
                    "name": ver.name,
                    "namespace": namespace_path,
                    "version": ver.version,
                },
                "spec": ver.spec_json,
            }
            filename = f"{ver.kind.lower()}-{ver.name}.yaml"
            (directory / filename).write_text(yaml.dump(doc, sort_keys=False))
            count += 1
        return count

    def _dir_fingerprint(self, directory: Path) -> str:
        h = hashlib.sha256()
        for path in sorted(directory.rglob("*.yaml")):
            h.update(path.read_bytes())
        for path in sorted(directory.rglob("*.yml")):
            h.update(path.read_bytes())
        return h.hexdigest()[:16]
