"""Tests for high-value differentiators: context graph, discovery, dynamic workflows."""

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from ai_platform.context_graph.service import (
    ContextGraphService,
    CreateTraceRequest,
    PrecedentQuery,
    TraceEntity,
)
from ai_platform.discovery.service import (
    AgentDiscoveryService,
    DiscoveryQuery,
    RegisterCapabilityRequest,
)
from ai_platform.workflow.dynamic import (
    DynamicWorkflowEngine,
    DynamicWorkflowPlanner,
    HeuristicWorkflowPlanner,
    PlanRequest,
)


async def _migrate(db: str) -> None:
    migration = Path(__file__).parent.parent / "migrations" / "005_differentiators.sql"
    conn = await aiosqlite.connect(db)
    await conn.executescript(migration.read_text())
    await conn.commit()
    await conn.close()


@pytest.mark.asyncio
async def test_context_graph_trace_and_precedent():
    db = tempfile.mktemp(suffix=".db")
    await _migrate(db)
    graph = ContextGraphService(db)
    ns = "ns-org"

    t1 = await graph.create_trace(
        ns,
        CreateTraceRequest(
            agent_ref="agents/sales-agent",
            tags=["discount", "enterprise"],
            entities=[TraceEntity(type="customer", id="acme-1")],
            payload={
                "decision": "approve_27_percent",
                "reasoning": "Strong renewal history",
                "alternatives_rejected": ["30%: exceeds policy"],
            },
            outcome="approved",
        ),
    )
    t2 = await graph.create_trace(
        ns,
        CreateTraceRequest(
            agent_ref="agents/sales-agent",
            tags=["discount"],
            entities=[TraceEntity(type="customer", id="acme-1")],
            payload={"decision": "approve_20_percent"},
            outcome="approved",
        ),
    )
    await graph.link_traces(t2.id, t1.id, "based_on_precedent")

    precedents = await graph.query_precedents(
        ns,
        PrecedentQuery(tags=["discount"], entities=[TraceEntity(type="customer", id="acme-1")]),
    )
    assert len(precedents) >= 2
    links = await graph.get_linked(t2.id)
    assert any(l["link_type"] == "based_on_precedent" for l in links)


@pytest.mark.asyncio
async def test_agent_discovery_routing():
    db = tempfile.mktemp(suffix=".db")
    await _migrate(db)
    discovery = AgentDiscoveryService(db)
    ns = "ns-org"

    await discovery.register(
        ns,
        RegisterCapabilityRequest(
            agent_ref="agents/research-agent",
            capabilities=["research", "web"],
            schemas=["agntcy:research.*"],
        ),
    )
    await discovery.register(
        ns,
        RegisterCapabilityRequest(
            agent_ref="agents/billing-agent",
            capabilities=["billing", "support"],
        ),
    )

    found = await discovery.discover(ns, DiscoveryQuery(capabilities=["research"], limit=5))
    assert len(found) == 1
    assert found[0].agent_ref == "agents/research-agent"

    best = await discovery.route_best(ns, ["billing"])
    assert best and best.agent_ref == "agents/billing-agent"


def test_dynamic_planner_research_goal():
    planner = HeuristicWorkflowPlanner()
    ir = planner.plan(
        PlanRequest(
            goal="Research market data across competitors",
            available_agents=["agents/a", "agents/b", "agents/c"],
        )
    )
    assert ir.source == "planner"
    assert ir.planner_backend == "heuristic"
    assert any(s.type == "parallel" for s in ir.steps) or len(ir.steps) >= 2
    facade = DynamicWorkflowPlanner()
    spec = facade.ir_to_workflow_spec(ir)
    assert len(spec.steps) >= 1


@pytest.mark.asyncio
async def test_llm_planner_research_goal():
    from ai_platform.model_router.providers import build_default_providers
    from ai_platform.model_router.router import ModelRouter

    router = ModelRouter(providers=build_default_providers())
    planner = DynamicWorkflowPlanner(model_router=router, default_mode="llm")
    ir = await planner.plan_async(
        PlanRequest(
            goal="Research market data across competitors",
            available_agents=["agents/a", "agents/b", "agents/c"],
            planner_mode="llm",
        )
    )
    assert ir.planner_backend == "llm"
    assert ir.name == "llm-research-plan"
    assert any(s.type == "parallel" for s in ir.steps)


@pytest.mark.asyncio
async def test_dynamic_workflow_simple_execute():
    db = tempfile.mktemp(suffix=".db")
    await _migrate(db)
    engine = DynamicWorkflowEngine(db)
    await engine.migrate()

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
        "Prompt:p": {"kind": "Prompt", "name": "p", "spec": {"template": "Goal: {{ message }}"}},
        "ModelRoute:m": {
            "kind": "ModelRoute",
            "name": "m",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        },
    }
    result = await engine.plan_and_run(
        "ns",
        "org",
        PlanRequest(goal="Help with invoice", available_agents=["agents/support-agent"]),
        bundle,
    )
    assert result.workflow_id
    assert result.ir.steps
    assert result.status in ("completed", "failed", "waiting_approval")
