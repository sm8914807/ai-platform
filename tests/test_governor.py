"""Agent action governor — tool quotas pause for approval instead of HTTP 429."""

import pytest

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.models import ToolboxEntry
from ai_platform.governor.engine import (
    MemoryCounterStore,
    RedisCounterStore,
    ToolGovernor,
    parse_rate_limit,
    quota_for_tool,
)
from ai_platform.workflow.engine import WorkflowEngine


def test_parse_rate_limit_forms():
    assert parse_rate_limit("20/min").count == 20
    assert parse_rate_limit("20/min").window_seconds == 60
    assert parse_rate_limit("3 per hour").window_seconds == 3600
    assert parse_rate_limit("100/day").window_seconds == 86400
    with pytest.raises(ValueError):
        parse_rate_limit("fast")


def test_toolbox_entry_camel_case():
    entry = ToolboxEntry.model_validate(
        {"ref": "tools/get-customer", "rateLimit": "20/min", "requireApproval": False}
    )
    assert entry.rate_limit == "20/min"


def test_quota_for_tool_prefers_toolbox():
    bundle = {
        "Toolbox:crm": {
            "kind": "Toolbox",
            "spec": {"tools": [{"ref": "tools/get-customer", "rateLimit": "3/hour"}]},
        },
        "Tool:get-customer": {
            "kind": "Tool",
            "spec": {
                "adapter": "mcp",
                "manifest": {"name": "get-customer", "inputSchema": {}, "outputSchema": {}},
                "rateLimit": "100/min",
            },
        },
    }
    limit, _ = quota_for_tool(bundle, "tools/get-customer")
    assert limit == "3/hour"


@pytest.mark.asyncio
async def test_memory_store_trips_after_limit():
    store = MemoryCounterStore()
    first = await store.consume("k", 1, 60)
    second = await store.consume("k", 1, 60)
    assert first.allowed
    assert not second.allowed
    assert second.remaining == 0


@pytest.mark.asyncio
async def test_governor_fail_closed_on_store_error():
    class Boom:
        async def consume(self, key, limit, window_seconds):
            raise RuntimeError("redis down")

    gov = ToolGovernor(store=Boom(), fail_closed=True)
    decision = await gov.check(tool_ref="tools/x", rate_limit="1/min")
    assert not decision.allowed
    assert decision.reason == "store_unavailable"


@pytest.mark.asyncio
async def test_redis_store_uses_eval():
    class FakeRedis:
        def __init__(self):
            self.calls = []

        async def eval(self, script, n, key, limit, window):
            self.calls.append((n, key, limit, window))
            return [1, int(limit), int(limit) - 1, int(window)]

    fake = FakeRedis()
    store = RedisCounterStore("redis://localhost:6379/0", client=fake)
    result = await store.consume("governor:org:ns:tools/x", 5, 60)
    assert result.allowed
    assert result.remaining == 4
    assert fake.calls[0][1] == "governor:org:ns:tools/x"


def _tool_bundle(rate_limit: str = "1/hour") -> dict:
    return {
        "Agent:support-agent": {
            "kind": "Agent",
            "name": "support-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
                "toolboxRef": "toolboxes/crm-tools",
            },
        },
        "Prompt:p": {"kind": "Prompt", "name": "p", "spec": {"template": "Help: {{ input }}"}},
        "ModelRoute:m": {
            "kind": "ModelRoute",
            "name": "m",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        },
        "Toolbox:crm-tools": {
            "kind": "Toolbox",
            "name": "crm-tools",
            "spec": {"tools": [{"ref": "tools/get-customer", "rateLimit": rate_limit}]},
        },
        "Tool:get-customer": {
            "kind": "Tool",
            "name": "get-customer",
            "spec": {
                "adapter": "mcp",
                "manifest": {
                    "name": "get-customer",
                    "inputSchema": {},
                    "outputSchema": {},
                },
                "config": {"server": "crm-mcp"},
            },
        },
    }


@pytest.mark.asyncio
async def test_agent_tool_quota_emits_approval_required():
    gov = ToolGovernor(MemoryCounterStore(), fail_closed=False)
    engine = AgentEngine(governor=gov)
    bundle = _tool_bundle("1/hour")
    first = await engine.execute(
        bundle, "agents/support-agent", {"message": "lookup", "use_tool": True}
    )
    assert first.type == "done"
    second = await engine.execute(
        bundle, "agents/support-agent", {"message": "lookup", "use_tool": True}
    )
    assert second.type == "approval_required"
    assert second.data["reason"] == "rate_limit_exceeded"
    assert second.data["toolRef"] == "tools/get-customer"
    assert second.data["approvalRef"] == "approval-flows/rate-limit"


@pytest.mark.asyncio
async def test_workflow_tool_quota_pauses_for_approval():
    gov = ToolGovernor(MemoryCounterStore(), fail_closed=False)
    engine = WorkflowEngine(governor=gov)
    await engine.initialize()
    bundle = {
        "Workflow:refund": {
            "kind": "Workflow",
            "name": "refund",
            "spec": {"steps": [{"id": "lookup", "type": "tool", "ref": "tools/get-customer"}]},
        },
        **{k: v for k, v in _tool_bundle("1/hour").items() if k != "Agent:support-agent"},
    }
    first = await engine.run(
        bundle, "workflows/refund", {"customerId": "c1"}, org_id="org", namespace_id="ns"
    )
    assert first.status == "completed"

    events = []
    stream = await engine.run(
        bundle,
        "workflows/refund",
        {"customerId": "c2"},
        org_id="org",
        namespace_id="ns",
        stream=True,
    )
    async for ev in stream:
        events.append(ev)
    paused = next(ev for ev in events if ev.type == "approval_required")
    assert paused.data["reason"] == "rate_limit_exceeded"
    run_id = paused.execution_id
    assert run_id is not None

    approved = await engine.approve(run_id)
    assert approved.status == "running"
    resumed = await engine.resume(run_id, bundle, org_id="org", namespace_id="ns")
    assert resumed.status == "completed"
    assert resumed.steps["lookup"]["status"] == "ok"
