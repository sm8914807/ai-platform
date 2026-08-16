"""Production MCP client — initialize, tools/list, tools/call."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ai_platform.tool_host.mcp.transports import (
    HttpMcpTransport,
    McpTransport,
    McpTransportError,
    StdioMcpTransport,
)

DEFAULT_PROTOCOL = "2025-03-26"
CLIENT_INFO = {"name": "ai-platform", "version": "0.8.0"}


@dataclass
class McpToolInfo:
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpCallResult:
    content: list[dict[str, Any]]
    is_error: bool = False
    structured: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        parts: list[str] = []
        for item in self.content:
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts)


class McpClient:
    """Session-oriented MCP client for one server connection."""

    def __init__(self, transport: McpTransport, *, protocol_version: str = DEFAULT_PROTOCOL) -> None:
        self.transport = transport
        self.protocol_version = protocol_version
        self._initialized = False
        self.server_info: dict[str, Any] = {}
        self.capabilities: dict[str, Any] = {}

    async def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return self.server_info
        try:
            result = await self.transport.request(
                "initialize",
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {
                        "tools": {},
                    },
                    "clientInfo": CLIENT_INFO,
                },
            )
        except McpTransportError:
            # Stateless / newer servers may reject initialize — continue without session.
            self._initialized = True
            return self.server_info
        if isinstance(result, dict):
            self.server_info = result.get("serverInfo") or {}
            self.capabilities = result.get("capabilities") or {}
            negotiated = result.get("protocolVersion")
            if negotiated:
                self.protocol_version = str(negotiated)
        try:
            await self.transport.notify("notifications/initialized", {})
        except McpTransportError:
            pass
        self._initialized = True
        return self.server_info

    async def list_tools(self, cursor: str | None = None) -> list[McpToolInfo]:
        await self.initialize()
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        result = await self.transport.request("tools/list", params or None)
        tools_raw = (result or {}).get("tools") if isinstance(result, dict) else []
        out: list[McpToolInfo] = []
        for t in tools_raw or []:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            out.append(
                McpToolInfo(
                    name=str(t["name"]),
                    description=t.get("description"),
                    input_schema=t.get("inputSchema") or t.get("input_schema") or {},
                    raw=t,
                )
            )
        return out

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> McpCallResult:
        await self.initialize()
        result = await self.transport.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        if not isinstance(result, dict):
            return McpCallResult(content=[{"type": "text", "text": str(result)}], raw={})
        content = result.get("content") or []
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}]
        return McpCallResult(
            content=content,
            is_error=bool(result.get("isError")),
            structured=result.get("structuredContent")
            if isinstance(result.get("structuredContent"), dict)
            else None,
            raw=result,
        )

    async def close(self) -> None:
        await self.transport.close()


def build_transport_from_config(config: dict[str, Any], *, timeout_seconds: float = 30.0) -> McpTransport:
    """Build a transport from Tool CRD ``spec.config``."""
    transport = str(config.get("transport") or "").lower().strip()
    protocol = str(config.get("protocolVersion") or config.get("protocol_version") or DEFAULT_PROTOCOL)

    if transport in {"http", "streamable-http", "sse", "https"}:
        url = config.get("url") or config.get("endpoint")
        if not url:
            raise McpTransportError("MCP http transport requires config.url")
        headers = dict(config.get("headers") or {})
        if config.get("apiKey"):
            headers.setdefault("Authorization", f"Bearer {config['apiKey']}")
        auth = config.get("auth")
        if isinstance(auth, dict):
            if auth.get("bearer"):
                headers.setdefault("Authorization", f"Bearer {auth['bearer']}")
            if auth.get("headers") and isinstance(auth["headers"], dict):
                headers.update({str(k): str(v) for k, v in auth["headers"].items()})
        return HttpMcpTransport(
            str(url),
            headers=headers,
            timeout_seconds=timeout_seconds,
            protocol_version=protocol,
        )

    if transport in {"stdio", "subprocess", "local", ""}:
        command = config.get("command")
        # Infer stdio when command is present even if transport omitted.
        if not command and transport:
            raise McpTransportError("MCP stdio transport requires config.command")
        if not command:
            raise McpTransportError(
                "MCP config needs transport=http (url) or transport=stdio (command)"
            )
        args = config.get("args") or []
        if not isinstance(args, list):
            raise McpTransportError("config.args must be a list")
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        # Inject secret into env if provided as apiKey without explicit env key.
        if config.get("apiKey") and "MCP_API_KEY" not in env:
            env = {**env, "MCP_API_KEY": str(config["apiKey"])}
        return StdioMcpTransport(
            str(command),
            [str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()},
            cwd=config.get("cwd"),
            timeout_seconds=timeout_seconds,
        )

    raise McpTransportError(f"unsupported MCP transport: {transport}")


@asynccontextmanager
async def with_client(
    config: dict[str, Any], *, timeout_seconds: float = 30.0
) -> AsyncIterator[McpClient]:
    """Async context helper yielding an initialized client."""
    transport = build_transport_from_config(config, timeout_seconds=timeout_seconds)
    client = McpClient(
        transport,
        protocol_version=str(
            config.get("protocolVersion") or config.get("protocol_version") or DEFAULT_PROTOCOL
        ),
    )
    try:
        await client.initialize()
        yield client
    finally:
        await client.close()
