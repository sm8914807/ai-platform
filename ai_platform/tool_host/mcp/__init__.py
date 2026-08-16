"""MCP client package."""

from ai_platform.tool_host.mcp.client import (
    McpCallResult,
    McpClient,
    McpToolInfo,
    build_transport_from_config,
)
from ai_platform.tool_host.mcp.transports import (
    HttpMcpTransport,
    McpTransportError,
    StdioMcpTransport,
)

__all__ = [
    "McpCallResult",
    "McpClient",
    "McpToolInfo",
    "McpTransportError",
    "HttpMcpTransport",
    "StdioMcpTransport",
    "build_transport_from_config",
]
