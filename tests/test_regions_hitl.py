"""Regions/edge listing + HITL inbox durability."""

import tempfile

import pytest

from ai_platform.region.service import RegionService
from ai_platform.workflow.engine import WorkflowEngine


@pytest.mark.asyncio
async def test_list_edge_nodes_after_register():
    db = tempfile.mktemp(suffix=".db")
    svc = RegionService(db)
    await svc.migrate()
    regions = await svc.list_regions()
    assert regions
    node_id = await svc.register_edge_node(
        "ns-1",
        regions[0].name,
        "hash-abc",
        ".platform/bundle.cache.json",
        {"host": "edge-1"},
    )
    nodes = await svc.list_edge_nodes()
    assert any(n["id"] == node_id for n in nodes)
    match = next(n for n in nodes if n["id"] == node_id)
    assert match["regionName"] == regions[0].name
    assert match["namespaceId"] == "ns-1"
    assert match["metadata"]["host"] == "edge-1"


@pytest.mark.asyncio
async def test_set_primary_region():
    db = tempfile.mktemp(suffix=".db")
    svc = RegionService(db)
    await svc.migrate()
    regions = await svc.list_regions()
    secondary = next(r for r in regions if not r.is_primary)
    await svc.set_primary(secondary.name)
    primary = await svc.get_primary()
    assert primary is not None
    assert primary.name == secondary.name


@pytest.mark.asyncio
async def test_hitl_inbox_and_durable_pending_approval(tmp_path):
    db = str(tmp_path / "workflows.db")
    engine = WorkflowEngine(db_path=db)
    await engine.initialize()
    bundle = {
        "Workflow:refund": {
            "kind": "Workflow",
            "name": "refund",
            "spec": {
                "steps": [
                    {
                        "id": "approve",
                        "type": "humanApproval",
                        "ref": "approval-flows/default",
                    },
                    {
                        "id": "done",
                        "type": "agent",
                        "ref": "agents/support-agent",
                        "when": "$.steps.approve.status == approved",
                    },
                ],
            },
        },
        "Agent:support-agent": {
            "kind": "Agent",
            "name": "support-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
            },
        },
        "Prompt:p": {"kind": "Prompt", "name": "p", "spec": {"template": "Task: {{ message }}"}},
        "ModelRoute:m": {
            "kind": "ModelRoute",
            "name": "m",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        },
    }
    state = await engine.run(
        bundle,
        "workflows/refund",
        {"amount": 40},
        org_id="org",
        namespace_id="ns-hitl",
        stream=False,
    )
    assert state.status == "waiting_approval"
    assert state.pending_approval is not None
    assert state.pending_approval["step_id"] == "approve"

    inbox = await engine.list_inbox(namespace_id="ns-hitl")
    assert len(inbox) == 1
    assert inbox[0]["runId"] == state.run_id
    assert inbox[0]["pendingApproval"]["step_id"] == "approve"

    # Simulate process restart: drop in-memory pending map.
    engine._pending_approvals.clear()
    recovered = await engine.get_run(state.run_id)
    assert recovered is not None
    assert recovered["pendingApproval"]["step_id"] == "approve"

    approved = await engine.approve(state.run_id, "approved")
    assert approved.status == "running"
    assert approved.steps["approve"]["status"] == "approved"

    resumed = await engine.resume(state.run_id, bundle, org_id="org", namespace_id="ns-hitl")
    assert resumed.status == "completed"
    assert "done" in resumed.steps

    empty = await engine.list_inbox(namespace_id="ns-hitl")
    assert empty == []
