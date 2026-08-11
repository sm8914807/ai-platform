"""Phase 1 tests."""

import pytest

from ai_platform.agent.engine import AgentEngine
from ai_platform.bundler.compiler import BundleCompiler
from ai_platform.core.models import (
    PlatformResource,
    ResourceKind,
    ResourceMetadata,
    ResourceStatus,
)
from ai_platform.core.validation import validate_platform_resource
from ai_platform.model_router.router import ModelRouter, ModelRequest
from ai_platform.registry.memory import InMemoryRegistryStore
from ai_platform.registry.store import ResourceVersionRecord
from ai_platform.tool_host.host import ToolHost
from ai_platform.core.models import ToolManifest, ToolSpec


@pytest.mark.asyncio
async def test_registry_publish_flow():
    store = InMemoryRegistryStore()
    ns = await store.ensure_namespace("org-acme/project-support", "development")

    resource = PlatformResource(
        kind=ResourceKind.AGENT,
        metadata=ResourceMetadata(name="support-agent", namespace="org-acme/project-support"),
        spec={
            "role": "executor",
            "modelRef": "models/gpt-4o-routed",
            "promptRef": "prompts/support-v3",
        },
    )
    await store.upsert_resource_version(ns, resource)
    await store.publish(ns, ResourceKind.AGENT, "support-agent", "0.0.1")
    published = await store.get_published_version(ns, ResourceKind.AGENT, "support-agent")
    assert published is not None
    assert published.spec_json["role"] == "executor"


@pytest.mark.asyncio
async def test_bundle_compile_and_sign():
    compiler = BundleCompiler()
    published = [
        ResourceVersionRecord(
            id="v1",
            resource_id="r1",
            version="1.0.0",
            spec_json={"role": "executor"},
            status_json={},
            author_id=None,
            commit_message=None,
            bundle_hash=None,
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            kind="Agent",
            name="support-agent",
        )
    ]
    manifest = compiler.compile("ns/dev", "development", published)
    assert manifest.bundle_hash.startswith("sha256:")
    assert manifest.signature


def test_agent_schema_validation():
    resource = PlatformResource(
        kind=ResourceKind.AGENT,
        metadata=ResourceMetadata(name="a", namespace="ns"),
        spec={
            "role": "executor",
            "modelRef": "models/m",
            "promptRef": "prompts/p",
        },
    )
    errors = validate_platform_resource(resource)
    assert errors == []


@pytest.mark.asyncio
async def test_model_router_mock():
    router = ModelRouter()
    from ai_platform.core.models import ModelCandidate, ModelRouteSpec

    spec = ModelRouteSpec(candidates=[ModelCandidate(provider="mock", model="mock-1")])
    resp = await router.complete(spec, ModelRequest(messages=[{"role": "user", "content": "hi"}]))
    assert "mock" in resp.content


@pytest.mark.asyncio
async def test_tool_host_mcp():
    host = ToolHost()
    spec = ToolSpec(
        adapter="mcp",
        manifest=ToolManifest(name="search", inputSchema={}, outputSchema={}),
        config={"server": "local"},
    )
    result = await host.invoke(spec, {"query": "test"})
    assert result.output["adapter"] == "mcp"


@pytest.mark.asyncio
async def test_agent_engine_execute():
    engine = AgentEngine()
    bundle = {
        "Agent:support-agent": {
            "kind": "Agent",
            "name": "support-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
            },
        },
        "Prompt:p": {
            "kind": "Prompt",
            "name": "p",
            "spec": {"template": "Help with: {{ input }}"},
        },
        "ModelRoute:m": {
            "kind": "ModelRoute",
            "name": "m",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        },
    }
    result = await engine.execute(bundle, "agents/support-agent", {"message": "billing"})
    assert result.type == "done"
    assert "billing" in result.data.get("content", "")
