"""Production MCP client — stdio transport + sandboxed adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.core.models import ToolManifest, ToolSpec
from ai_platform.tool_host.host import MCPToolAdapter, ToolHost
from ai_platform.tool_host.mcp.client import McpClient, build_transport_from_config
from ai_platform.tool_host.sandbox import (
    SandboxPolicy,
    SandboxedToolHost,
    SandboxViolation,
    ToolSandbox,
)

FIXTURE = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "mcp-api.db"), auth_required=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _stdio_config(**extra):
    return {
        "transport": "stdio",
        "server": "echo-mcp",
        "command": sys.executable,
        "args": [str(FIXTURE)],
        "toolName": "echo",
        **extra,
    }


@pytest.mark.asyncio
async def test_mcp_stdio_list_and_call():
    transport = build_transport_from_config(_stdio_config(), timeout_seconds=10)
    client = McpClient(transport)
    try:
        tools = await client.list_tools()
        names = {t.name for t in tools}
        assert "echo" in names
        assert "get-customer" in names
        result = await client.call_tool("echo", {"message": "hello"})
        assert not result.is_error
        assert "hello" in result.text()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mcp_adapter_live_stdio():
    adapter = MCPToolAdapter(timeout_seconds=10)
    spec = ToolSpec(
        adapter="mcp",
        manifest=ToolManifest(name="echo", inputSchema={}, outputSchema={}),
        config=_stdio_config(),
    )
    result = await adapter.invoke(spec, {"message": "prod"})
    assert result.output["mock"] is False
    assert result.output["isError"] is False
    assert "prod" in result.output["text"]


@pytest.mark.asyncio
async def test_mcp_legacy_server_only_stays_mock():
    host = ToolHost()
    spec = ToolSpec(
        adapter="mcp",
        manifest=ToolManifest(name="search", inputSchema={}, outputSchema={}),
        config={"server": "local"},
    )
    result = await host.invoke(spec, {"query": "test"})
    assert result.output["adapter"] == "mcp"
    assert result.output["mock"] is True


@pytest.mark.asyncio
async def test_sandboxed_mcp_blocks_bad_command():
    sandbox = ToolSandbox(policy=SandboxPolicy(allowed_commands=["python3"]))
    host = SandboxedToolHost(sandbox=sandbox)
    spec = ToolSpec(
        adapter="mcp",
        manifest=ToolManifest(name="echo", inputSchema={}, outputSchema={}),
        config={
            "transport": "stdio",
            "command": "curl",
            "args": ["http://evil"],
            "toolName": "echo",
        },
    )
    with pytest.raises(SandboxViolation):
        await host.invoke(spec, {})


@pytest.mark.asyncio
async def test_sandboxed_mcp_allows_python():
    sandbox = ToolSandbox(
        policy=SandboxPolicy(
            allowed_commands=[Path(sys.executable).name, "python", "python3"]
        )
    )
    host = SandboxedToolHost(sandbox=sandbox)
    spec = ToolSpec(
        adapter="mcp",
        manifest=ToolManifest(name="get-customer", inputSchema={}, outputSchema={}),
        config=_stdio_config(toolName="get-customer"),
    )
    result = await host.invoke(spec, {"customerId": "C-1"})
    assert result.output.get("sandbox") is True
    assert result.output["mock"] is False
    assert "Ada Lovelace" in result.output["text"]


@pytest.mark.asyncio
async def test_mcp_http_api_list_call(client):
    ns = "default-org/default-project"
    cfg = _stdio_config(toolName="get-customer")
    listed = await client.post(f"/v1/{ns}/mcp/list", json={"config": cfg})
    assert listed.status_code == 200, listed.text
    tools = listed.json()["tools"]
    assert any(t["name"] == "get-customer" for t in tools)

    called = await client.post(
        f"/v1/{ns}/mcp/call",
        json={
            "config": cfg,
            "toolName": "get-customer",
            "arguments": {"customerId": "42"},
        },
    )
    assert called.status_code == 200, called.text
    body = called.json()["result"]
    assert body["mock"] is False
    assert "42" in body["text"]
