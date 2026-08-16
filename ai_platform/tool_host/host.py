"""Tool host — REST / OpenAPI / MCP adapters."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from ai_platform.core.models import ToolSpec
from ai_platform.tool_host.mcp.client import McpClient, build_transport_from_config
from ai_platform.tool_host.mcp.transports import McpTransportError


class ToolResult:
    def __init__(self, output: Any, latency_ms: float = 0.0) -> None:
        self.output = output
        self.latency_ms = latency_ms


class ToolAdapter(ABC):
    @abstractmethod
    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        ...


class RestToolAdapter(ToolAdapter):
    """REST tool — config.url + method (offline mock when no live URL)."""

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        url = spec.config.get("url", "")
        method = spec.config.get("method", "GET").upper()
        output = {
            "adapter": "rest",
            "method": method,
            "url": url,
            "input": input_data,
            "mock": True,
        }
        return ToolResult(output, (time.perf_counter() - start) * 1000)


class OpenAPIToolAdapter(ToolAdapter):
    """OpenAPI-defined REST tool (offline mock)."""

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
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


def _mcp_should_mock(config: dict[str, Any]) -> bool:
    if config.get("mock") or config.get("dryRun"):
        return True
    transport = str(config.get("transport") or "").lower()
    if transport in {"http", "streamable-http", "sse", "https"}:
        return not bool(config.get("url") or config.get("endpoint"))
    if transport in {"stdio", "subprocess", "local"}:
        return not bool(config.get("command"))
    # Legacy CRDs only set server= — keep mock for offline demos unless command/url present.
    if config.get("command") or config.get("url") or config.get("endpoint"):
        return False
    return True


def _truncate(value: Any, max_bytes: int) -> Any:
    text = value if isinstance(value, str) else str(value)
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return value if not isinstance(value, str) else text
    return encoded[:max_bytes].decode(errors="replace") + "…[truncated]"


class MCPToolAdapter(ToolAdapter):
    """Production MCP adapter — stdio or Streamable HTTP JSON-RPC."""

    def __init__(self, *, timeout_seconds: float = 30.0, max_output_bytes: int = 256_000) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        payload = dict(input_data)
        payload.pop("_namespace_id", None)
        config = dict(spec.config)
        server = config.get("server", "local")
        tool_name = str(config.get("toolName") or config.get("tool") or spec.manifest.name)

        if _mcp_should_mock(config):
            output = {
                "adapter": "mcp",
                "server": server,
                "tool": tool_name,
                "input": payload,
                "mock": True,
            }
            return ToolResult(output, (time.perf_counter() - start) * 1000)

        transport = build_transport_from_config(config, timeout_seconds=self.timeout_seconds)
        client = McpClient(
            transport,
            protocol_version=str(
                config.get("protocolVersion") or config.get("protocol_version") or "2025-03-26"
            ),
        )
        try:
            result = await client.call_tool(tool_name, payload)
            output: dict[str, Any] = {
                "adapter": "mcp",
                "server": server,
                "tool": tool_name,
                "transport": config.get("transport")
                or ("http" if config.get("url") else "stdio"),
                "isError": result.is_error,
                "content": result.content,
                "text": _truncate(result.text(), self.max_output_bytes),
                "mock": False,
            }
            if result.structured is not None:
                output["structuredContent"] = result.structured
            if result.is_error:
                output["error"] = result.text()
            return ToolResult(output, (time.perf_counter() - start) * 1000)
        except McpTransportError as e:
            return ToolResult(
                {
                    "adapter": "mcp",
                    "server": server,
                    "tool": tool_name,
                    "mock": False,
                    "isError": True,
                    "error": str(e),
                    "code": e.code,
                },
                (time.perf_counter() - start) * 1000,
            )
        finally:
            await client.close()


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
