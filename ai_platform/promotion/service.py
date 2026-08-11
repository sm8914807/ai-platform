"""Environment promotion — staging to production."""

from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import EnvironmentSpec, ResourceKind
from ai_platform.registry.store import RegistryStore


class PromotionService:
    def __init__(self, registry: RegistryStore) -> None:
        self.registry = registry
        self._pending: dict[str, dict[str, Any]] = {}

    async def request_promotion(
        self,
        namespace_id: str,
        from_env: str,
        to_env: str,
        requested_by: str,
        bundle_hash: str,
    ) -> str:
        promo_id = new_id("promo")
        self._pending[promo_id] = {
            "id": promo_id,
            "namespace_id": namespace_id,
            "from_env": from_env,
            "to_env": to_env,
            "status": "pending",
            "requested_by": requested_by,
            "bundle_hash": bundle_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return promo_id

    async def approve_promotion(self, promo_id: str, approved_by: str) -> dict[str, Any]:
        promo = self._pending.get(promo_id)
        if not promo:
            raise ValueError(f"Promotion not found: {promo_id}")
        promo["status"] = "approved"
        promo["approved_by"] = approved_by
        promo["completed_at"] = datetime.now(timezone.utc).isoformat()
        return promo

    async def promote_resources(
        self,
        namespace_path: str,
        from_env: str,
        to_env: str,
    ) -> int:
        """Copy published versions from source env namespace to target env namespace."""
        source_ns = await self.registry.ensure_namespace(namespace_path, from_env)
        target_ns = await self.registry.ensure_namespace(namespace_path, to_env)
        published = await self.registry.list_published(source_ns)
        count = 0
        for ver in published:
            if not ver.kind or not ver.name:
                continue
            from ai_platform.core.models import PlatformResource, ResourceMetadata

            resource = PlatformResource(
                kind=ResourceKind(ver.kind),
                metadata=ResourceMetadata(
                    name=ver.name,
                    namespace=namespace_path,
                    version=ver.version,
                ),
                spec=ver.spec_json,
            )
            await self.registry.upsert_resource_version(target_ns, resource)
            await self.registry.publish(target_ns, ResourceKind(ver.kind), ver.name, ver.version)
            count += 1
        return count

    def get_environment_spec(self, bundle: dict[str, dict], env_name: str) -> EnvironmentSpec | None:
        doc = bundle.get(f"Environment:{env_name}")
        if not doc:
            return None
        return EnvironmentSpec.model_validate(doc["spec"])
