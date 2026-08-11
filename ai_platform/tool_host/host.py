"""Tool host — MCP and OpenAPI adapters."""

from abc import ABC, abstractmethod
from typing import Any

from ai_platform.core.models import ToolSpec


class ToolResult:
    def __init__(self, output: Any, latency_ms: float = 0.0) -> None:
        self.output = output
        self.latency_ms = latency_ms


class ToolAdapter(ABC):
    @abstractmethod
    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        ...


class RestToolAdapter(ToolAdapter):
    """REST tool — config.url + method."""

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        import time

        start = time.perf_counter()
        url = spec.config.get("url", "")
        method = spec.config.get("method", "GET").upper()
        # Phase 1: echo mock for offline dev
        output = {
            "adapter": "rest",
            "method": method,
            "url": url,
            "input": input_data,
            "mock": True,
        }
        return ToolResult(output, (time.perf_counter() - start) * 1000)


class OpenAPIToolAdapter(ToolAdapter):
    """OpenAPI-defined REST tool."""

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        import time

        start = time.perf_counter()
        operation_id = spec.config.get("operationId", spec.manifest.name)
        base_url = spec.config.get("baseUrl", "")
        output = {
            "adapter": "openapi",
            "operationId": operation_id,
            "baseUrl": base_url,
            "input": input_data,
            "mock": True,
        }
        return ToolResult(output, (time.perf_counter() - start) * 1000)


class MCPToolAdapter(ToolAdapter):
    """MCP tool adapter — Phase 1 mock; wire real MCP client in Phase 2."""

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        import time

        start = time.perf_counter()
        server = spec.config.get("server", "local")
        tool_name = spec.manifest.name
        output = {
            "adapter": "mcp",
            "server": server,
            "tool": tool_name,
            "input": input_data,
            "mock": True,
        }
        return ToolResult(output, (time.perf_counter() - start) * 1000)


class ToolHost:
    def __init__(self) -> None:
        self._adapters: dict[str, ToolAdapter] = {
            "rest": RestToolAdapter(),
            "openapi": OpenAPIToolAdapter(),
            "mcp": MCPToolAdapter(),
        }

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        adapter = self._adapters.get(spec.adapter)
        if not adapter:
            raise ValueError(f"No adapter for: {spec.adapter}")
        return await adapter.invoke(spec, input_data)

    def register_adapter(self, name: str, adapter: ToolAdapter) -> None:
        self._adapters[name] = adapter
