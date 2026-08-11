"""Edge runtime — local bundle cache, telemetry-only federation."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ai_platform.core.models import EdgeRuntimeConfig
from ai_platform.orchestrator.engine import Orchestrator
from ai_platform.sdk.platform import PlatformConfig


class EdgeRuntime:
    """Lightweight runtime for edge deployments — cache bundle locally, optional telemetry-only."""

    def __init__(
        self,
        config: PlatformConfig,
        edge_config: EdgeRuntimeConfig,
        orchestrator: Orchestrator,
    ) -> None:
        self.config = config
        self.edge_config = edge_config
        self.orchestrator = orchestrator
        self._node_id: str | None = None
        self._bundle_key = f"{config.namespace}:{config.environment}"
        self._cache_path = Path(edge_config.bundle_cache_path)

    @classmethod
    async def start(
        cls,
        endpoint: str | None = None,
        namespace: str = "default-org/default-project",
        environment: str = "development",
        region: str | None = None,
        telemetry_only: bool = False,
        cache_path: str = ".platform/bundle.cache.json",
        **kwargs: Any,
    ) -> "EdgeRuntime":
        edge_config = EdgeRuntimeConfig(
            mode="edge",
            bundle_cache_path=cache_path,
            telemetry_only=telemetry_only,
            region=region,
        )
        platform_config = PlatformConfig(
            endpoint=(endpoint or "http://localhost:8080").rstrip("/"),
            namespace=namespace,
            environment=environment,
            api_key=kwargs.get("api_key"),
        )
        from ai_platform.agent.engine import AgentEngine

        orchestrator = Orchestrator(AgentEngine())
        runtime = cls(platform_config, edge_config, orchestrator)

        if runtime._cache_path.exists():
            await runtime._load_cache()
        try:
            await runtime._sync_from_control_plane()
        except Exception:
            if not runtime._cache_path.exists():
                raise

        return runtime

    async def _load_cache(self) -> None:
        data = json.loads(self._cache_path.read_text())
        resources = data.get("resources", [])
        self.orchestrator.load_bundle(self._bundle_key, resources)

    async def _save_cache(self, data: dict[str, Any]) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(data, indent=2))

    async def _sync_from_control_plane(self) -> None:
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        region_endpoint = self.config.endpoint
        if self.edge_config.region:
            async with httpx.AsyncClient(timeout=15.0) as client:
                reg_resp = await client.get(f"{self.config.endpoint}/v1/regions")
                if reg_resp.status_code == 200:
                    for r in reg_resp.json().get("regions", []):
                        if r["name"] == self.edge_config.region:
                            region_endpoint = r["endpoint"].rstrip("/")
                            break

        url = (
            f"{region_endpoint}/v1/bundles/{self.config.environment}"
            f"?namespace={self.config.namespace}"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            if not self.edge_config.telemetry_only:
                reg = await client.post(
                    f"{region_endpoint}/v1/edge/register",
                    json={
                        "namespace": self.config.namespace,
                        "environment": self.config.environment,
                        "region": self.edge_config.region,
                        "nodeType": "edge",
                    },
                    headers=headers,
                )
                if reg.status_code == 200:
                    self._node_id = reg.json().get("nodeId")

            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Bundle fetch failed: {resp.status_code}")
            data = resp.json()
            await self._save_cache(data)
            self.orchestrator.load_bundle(self._bundle_key, data.get("resources", []))

    async def report_telemetry(self, events: list[dict[str, Any]]) -> None:
        if not self._node_id:
            return
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"{self.config.endpoint}/v1/edge/{self._node_id}/telemetry",
                json={"events": events},
                headers=headers,
            )

    async def run(self, resource_ref: str, input: dict[str, Any] | None = None) -> Any:
        from ai_platform.core.models import ExecutionRequest

        request = ExecutionRequest(resource_ref=resource_ref, input=input or {})
        result = await self.orchestrator.execute(self._bundle_key, request)
        if self.edge_config.telemetry_only:
            await self.report_telemetry(
                [{"ref": resource_ref, "input": input, "timestamp": datetime.now(timezone.utc).isoformat()}]
            )
        return result
