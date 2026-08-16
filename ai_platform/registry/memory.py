"""In-memory registry for tests and local dev."""

from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import AuditEvent, PlatformResource, ResourceKind
from ai_platform.registry.store import RegistryStore, ResourceRecord, ResourceVersionRecord


class InMemoryRegistryStore(RegistryStore):
    def __init__(self) -> None:
        self._namespaces: dict[str, str] = {}
        self._resources: dict[str, ResourceRecord] = {}
        self._versions: dict[str, ResourceVersionRecord] = {}
        self._audit: list[AuditEvent] = []
        self._nodes: dict[str, dict[str, Any]] = {}

    def _resource_key(self, namespace_id: str, kind: str, name: str) -> str:
        return f"{namespace_id}:{kind}:{name}"

    async def ensure_namespace(self, namespace_path: str, env: str) -> str:
        key = f"{namespace_path}:{env}"
        if key not in self._namespaces:
            self._namespaces[key] = f"mem:{namespace_path}:{env}"
        return self._namespaces[key]

    async def list_namespaces(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, ns_id in self._namespaces.items():
            path, _, env = key.rpartition(":")
            out.append({"id": ns_id, "path": path, "env": env})
        out.sort(key=lambda r: (r["path"], r["env"]))
        return out

    async def upsert_resource_version(
        self,
        namespace_id: str,
        resource: PlatformResource,
        author_id: str | None = None,
        commit_message: str | None = None,
    ) -> ResourceVersionRecord:
        key = self._resource_key(namespace_id, resource.kind.value, resource.metadata.name)
        version = resource.metadata.version
        now = datetime.now(timezone.utc)

        record = self._resources.get(key)
        if not record:
            record = ResourceRecord(
                id=new_id("res"),
                namespace_id=namespace_id,
                kind=resource.kind.value,
                name=resource.metadata.name,
                latest_version=version,
                published_version=None,
                created_at=now,
                updated_at=now,
            )
            self._resources[key] = record
        else:
            record.latest_version = version
            record.updated_at = now

        version_record = ResourceVersionRecord(
            id=new_id("ver"),
            resource_id=record.id,
            version=version,
            spec_json=resource.spec,
            status_json=resource.status.model_dump() if resource.status else {},
            author_id=author_id,
            commit_message=commit_message,
            bundle_hash=None,
            created_at=now,
            kind=record.kind,
            name=record.name,
        )
        self._versions[f"{record.id}:{version}"] = version_record
        return version_record

    async def get_resource(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceRecord | None:
        return self._resources.get(self._resource_key(namespace_id, kind.value, name))

    async def get_version(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> ResourceVersionRecord | None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            return None
        return self._versions.get(f"{resource.id}:{version}")

    async def get_published_version(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceVersionRecord | None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource or not resource.published_version:
            return None
        return self._versions.get(f"{resource.id}:{resource.published_version}")

    async def list_published(self, namespace_id: str) -> list[ResourceVersionRecord]:
        out: list[ResourceVersionRecord] = []
        for resource in self._resources.values():
            if resource.namespace_id != namespace_id or not resource.published_version:
                continue
            v = self._versions.get(f"{resource.id}:{resource.published_version}")
            if v:
                out.append(v)
        return out

    async def publish(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            raise ValueError(f"Resource not found: {kind.value}/{name}")
        if f"{resource.id}:{version}" not in self._versions:
            raise ValueError(f"Version not found: {version}")
        resource.published_version = version
        resource.updated_at = datetime.now(timezone.utc)

    async def unpublish(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> None:
        resource = await self.get_resource(namespace_id, kind, name)
        if not resource:
            raise ValueError(f"Resource not found: {kind.value}/{name}")
        resource.published_version = None
        resource.updated_at = datetime.now(timezone.utc)

    async def set_bundle_hash(self, resource_version_id: str, bundle_hash: str) -> None:
        for v in self._versions.values():
            if v.id == resource_version_id:
                v.bundle_hash = bundle_hash
                return
        raise ValueError(f"Version id not found: {resource_version_id}")

    async def append_audit(self, event: AuditEvent) -> AuditEvent:
        self._audit.append(event)
        return event

    async def register_runtime_node(
        self, namespace_id: str, node_type: str = "sdk", metadata: dict[str, Any] | None = None
    ) -> str:
        node_id = new_id("node")
        self._nodes[node_id] = {
            "namespace_id": namespace_id,
            "node_type": node_type,
            "metadata": metadata or {},
        }
        return node_id
