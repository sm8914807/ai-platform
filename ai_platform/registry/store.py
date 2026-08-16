"""Registry persistence layer."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ai_platform.core.models import AuditEvent, PlatformResource, ResourceKind


class ResourceRecord:
    def __init__(
        self,
        id: str,
        namespace_id: str,
        kind: str,
        name: str,
        latest_version: str | None,
        published_version: str | None,
        created_at: datetime,
        updated_at: datetime,
    ):
        self.id = id
        self.namespace_id = namespace_id
        self.kind = kind
        self.name = name
        self.latest_version = latest_version
        self.published_version = published_version
        self.created_at = created_at
        self.updated_at = updated_at


class ResourceVersionRecord:
    def __init__(
        self,
        id: str,
        resource_id: str,
        version: str,
        spec_json: dict[str, Any],
        status_json: dict[str, Any],
        author_id: str | None,
        commit_message: str | None,
        bundle_hash: str | None,
        created_at: datetime,
        kind: str | None = None,
        name: str | None = None,
    ):
        self.id = id
        self.resource_id = resource_id
        self.version = version
        self.spec_json = spec_json
        self.status_json = status_json
        self.author_id = author_id
        self.commit_message = commit_message
        self.bundle_hash = bundle_hash
        self.created_at = created_at
        self.kind = kind
        self.name = name


class RegistryStore(ABC):
    @abstractmethod
    async def ensure_namespace(self, namespace_path: str, env: str) -> str:
        """Return namespace_id, creating org/project/namespace if needed."""

    @abstractmethod
    async def upsert_resource_version(
        self,
        namespace_id: str,
        resource: PlatformResource,
        author_id: str | None = None,
        commit_message: str | None = None,
    ) -> ResourceVersionRecord:
        ...

    @abstractmethod
    async def get_resource(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceRecord | None:
        ...

    @abstractmethod
    async def get_version(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> ResourceVersionRecord | None:
        ...

    @abstractmethod
    async def get_published_version(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> ResourceVersionRecord | None:
        ...

    @abstractmethod
    async def list_published(self, namespace_id: str) -> list[ResourceVersionRecord]:
        ...

    @abstractmethod
    async def publish(
        self, namespace_id: str, kind: ResourceKind, name: str, version: str
    ) -> None:
        ...

    @abstractmethod
    async def unpublish(
        self, namespace_id: str, kind: ResourceKind, name: str
    ) -> None:
        """Clear the published pointer (draft versions remain)."""

    @abstractmethod
    async def list_namespaces(self) -> list[dict[str, Any]]:
        """Return ``[{id, path, env}, ...]`` known to the registry."""

    @abstractmethod
    async def set_bundle_hash(
        self, resource_version_id: str, bundle_hash: str
    ) -> None:
        ...

    @abstractmethod
    async def append_audit(
        self, event: AuditEvent
    ) -> AuditEvent:
        ...

    @abstractmethod
    async def list_audit(
        self,
        org_id: str,
        *,
        limit: int = 50,
        action: str | None = None,
    ) -> list[AuditEvent]:
        ...

    @abstractmethod
    async def purge_audit(self, org_id: str, *, retain_days: int = 90) -> int:
        """Delete audit rows older than retain_days. Returns deleted count."""
        ...

    @abstractmethod
    async def register_runtime_node(
        self, namespace_id: str, node_type: str = "sdk", metadata: dict[str, Any] | None = None
    ) -> str:
        ...
