"""Runtime SDK — platform.start() and execution."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx

from ai_platform.agent.engine import AgentEngine
from ai_platform.bundler.compiler import BundleCompiler
from ai_platform.core.models import ExecutionEvent, ExecutionRequest
from ai_platform.orchestrator.engine import Orchestrator
from ai_platform.telemetry.tracing import setup_tracing


@dataclass
class PlatformConfig:
    endpoint: str = "http://localhost:8080"
    api_key: str | None = None
    environment: str = "development"
    namespace: str = "default-org/default-project"
    otlp_endpoint: str | None = None
    bundle_ttl_seconds: int = 300


@dataclass
class RunResult:
    events: list[ExecutionEvent] = field(default_factory=list)
    stream: AsyncIterator[ExecutionEvent] | None = None

    async def collect(self) -> list[ExecutionEvent]:
        if self.stream:
            out = []
            async for e in self.stream:
                out.append(e)
            return out
        return self.events


class Platform:
    """Thin SDK bootstrap — syncs bundle and delegates to orchestrator."""

    def __init__(self, config: PlatformConfig, orchestrator: Orchestrator) -> None:
        self.config = config
        self.orchestrator = orchestrator
        self._bundle_key = f"{config.namespace}:{config.environment}"
        self._bundle_hash: str | None = None
        self._node_id: str | None = None
        self._cached_at: datetime | None = None

    @classmethod
    async def start(
        cls,
        api_key: str | None = None,
        environment: str = "development",
        namespace: str = "default-org/default-project",
        endpoint: str = "http://localhost:8080",
        otlp_endpoint: str | None = None,
        **kwargs: Any,
    ) -> "Platform":
        config = PlatformConfig(
            endpoint=endpoint.rstrip("/"),
            api_key=api_key or kwargs.get("api_key"),
            environment=environment,
            namespace=namespace,
            otlp_endpoint=otlp_endpoint,
        )
        setup_tracing("ai-platform-sdk", config.otlp_endpoint)
        orchestrator = Orchestrator(AgentEngine())
        platform = cls(config, orchestrator)
        await platform._bootstrap()
        return platform

    def start_sync(self, **kwargs: Any) -> "Platform":
        return asyncio.run(self.start(**kwargs))

    async def _bootstrap(self) -> None:
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            reg = await client.post(
                f"{self.config.endpoint}/v1/nodes/register",
                json={
                    "namespace": self.config.namespace,
                    "environment": self.config.environment,
                    "node_type": "sdk",
                },
                headers=headers,
            )
            if reg.status_code == 200:
                self._node_id = reg.json().get("nodeId")

            await self._sync_bundle(client, headers)

    async def _sync_bundle(self, client: httpx.AsyncClient, headers: dict[str, str]) -> None:
        url = (
            f"{self.config.endpoint}/v1/bundles/{self.config.environment}"
            f"?namespace={self.config.namespace}"
        )
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Bundle fetch failed: {resp.status_code} {resp.text}")

        data = resp.json()
        self._bundle_hash = data.get("bundleHash")
        resources = data.get("resources", [])
        self.orchestrator.load_bundle(self._bundle_key, resources)
        self._cached_at = datetime.now(timezone.utc)

    async def run(
        self,
        resource_ref: str,
        input: dict[str, Any] | None = None,
        session_id: str | None = None,
        stream: bool = False,
    ) -> RunResult:
        request = ExecutionRequest(
            resource_ref=resource_ref,
            input=input or {},
            session_id=session_id,
            stream=stream,
        )
        result = await self.orchestrator.execute(self._bundle_key, request)

        if stream:
            return RunResult(stream=result)

        if isinstance(result, ExecutionEvent):
            return RunResult(events=[result])
        return RunResult(events=[])

    async def refresh_bundle(self) -> None:
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            await self._sync_bundle(client, headers)
