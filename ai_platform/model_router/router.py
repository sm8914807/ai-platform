"""Model routing with fallback."""

from dataclasses import dataclass
from typing import Any

from ai_platform.core.models import ModelRouteSpec


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    temperature: float = 0.7
    max_tokens: int | None = None


@dataclass
class ModelResponse:
    content: str
    provider: str
    model: str
    usage: dict[str, int]
    latency_ms: float


class ModelProvider:
    """Base model provider adapter."""

    name: str

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError


class MockModelProvider(ModelProvider):
    """Deterministic mock for Phase 1 tests and local dev."""

    name = "mock"

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        last = request.messages[-1].get("content", "") if request.messages else ""
        return ModelResponse(
            content=f"[mock:{model}] processed: {last}",
            provider=self.name,
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            latency_ms=5.0,
        )


class ModelRouter:
    """Routes requests across providers per ModelRoute spec."""

    def __init__(
        self,
        providers: dict[str, ModelProvider] | None = None,
        metrics_collector: Any | None = None,
        route_name: str | None = None,
        namespace_id: str | None = None,
    ) -> None:
        self._providers = providers or {"mock": MockModelProvider()}
        self._cache: dict[str, ModelResponse] = {}
        self._metrics = metrics_collector
        self._route_name = route_name
        self._namespace_id = namespace_id

    async def complete(self, route_spec: ModelRouteSpec, request: ModelRequest) -> ModelResponse:
        candidates = sorted(
            route_spec.candidates,
            key=lambda c: (not c.fallback, -c.weight),
        )
        cache_key = None
        if route_spec.caching.get("enabled"):
            cache_key = str(hash((tuple(request.messages), route_spec.strategy)))
            if cache_key in self._cache:
                return self._cache[cache_key]

        last_error: Exception | None = None
        for candidate in candidates:
            provider = self._providers.get(candidate.provider)
            if not provider:
                continue
            try:
                response = await provider.complete(candidate.model, request)
                if cache_key:
                    self._cache[cache_key] = response
                if self._metrics and self._route_name and self._namespace_id:
                    await self._metrics.record(
                        self._route_name,
                        self._namespace_id,
                        response.provider,
                        response.model,
                        response.latency_ms,
                        True,
                        response.usage.get("total_tokens", 0) * 0.0001,
                    )
                return response
            except Exception as e:
                last_error = e
                if self._metrics and self._route_name and self._namespace_id:
                    await self._metrics.record(
                        self._route_name,
                        self._namespace_id,
                        candidate.provider,
                        candidate.model,
                        0.0,
                        False,
                    )
                if not candidate.fallback:
                    continue
        if last_error:
            raise last_error
        raise RuntimeError("No model candidates available")
