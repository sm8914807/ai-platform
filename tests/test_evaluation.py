"""Real evaluation judges + publish gate tests."""

import pytest

from ai_platform.core.models import EvaluationSuiteSpec, PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.evaluation.judges import KeywordMatchJudge, LlmJudge, normalize_output
from ai_platform.evaluation.runner import EvaluationRunner
from ai_platform.model_router.router import ModelRouter
from ai_platform.policy.engine import PolicyEngine
from ai_platform.publish.service import PublishGateError, PublishService
from ai_platform.registry.memory import InMemoryRegistryStore


@pytest.mark.asyncio
async def test_keyword_judge_scores_output_not_input():
    judge = KeywordMatchJudge()
    score, detail = await judge.score(
        case={
            "input": {"message": "help me"},
            "expected": {"contains": "billing"},
        },
        output={"content": "Here is your billing summary", "latency_ms": 10, "cost": 0, "tools_used": []},
        evaluator={"type": "keyword_match"},
    )
    assert score == 1.0
    assert "billing" in detail["matched"]


@pytest.mark.asyncio
async def test_llm_judge_returns_structured_score():
    judge = LlmJudge(model_router=ModelRouter())
    score, detail = await judge.score(
        case={
            "input": {"message": "billing invoice help"},
            "expected": {"contains": "billing"},
        },
        output={
            "content": "[mock] processed: billing invoice help",
            "latency_ms": 5,
            "cost": 0.001,
            "tools_used": [],
        },
        evaluator={"type": "llm_judge", "criteria": "quality", "metric": "quality"},
    )
    assert 0.0 <= score <= 1.0
    assert "rationale" in detail


@pytest.mark.asyncio
async def test_evaluation_runner_with_live_execute_and_gates():
    runner = EvaluationRunner(model_router=ModelRouter())
    suite = EvaluationSuiteSpec(
        dataset=[
            {
                "id": "c1",
                "input": {"message": "billing"},
                "expected": {"contains": "billing"},
            }
        ],
        evaluators=[
            {"type": "keyword_match"},
            {"type": "llm_judge", "criteria": "quality", "metric": "quality"},
            {"type": "latency", "maxP95Ms": 5000},
        ],
        gates={"minScore": 0.5, "failIf": "score < 0.5", "metrics": {"keyword": 0.5}},
    )

    async def execute_fn(inp):
        return {
            "type": "done",
            "data": {
                "content": f"Handled billing for {inp.get('message')}",
                "latencyMs": 12,
                "usage": {"total_tokens": 40},
            },
        }

    result = await runner.run_suite(suite, "agents/support-agent", "1.0.0", execute_fn)
    assert result.passed
    assert result.scores["keyword"] == 1.0
    assert "quality" in result.scores
    assert result.overall >= 0.5


@pytest.mark.asyncio
async def test_evaluation_gate_fails_on_low_quality():
    runner = EvaluationRunner(model_router=ModelRouter())
    suite = EvaluationSuiteSpec(
        dataset=[
            {
                "id": "c1",
                "input": {"message": "hello"},
                "expected": {"contains": "billing"},
            }
        ],
        evaluators=[{"type": "keyword_match"}],
        gates={"minScore": 0.9, "metrics": {"keyword": 0.9}},
    )

    async def execute_fn(_inp):
        return {"content": "unrelated answer", "latency_ms": 1, "cost": 0, "tools_used": []}

    result = await runner.run_suite(suite, "agents/support-agent", "1.0.0", execute_fn)
    assert not result.passed
    assert result.gate_reason


@pytest.mark.asyncio
async def test_publish_blocks_when_triggered_suite_fails():
    store = InMemoryRegistryStore()
    ns = await store.ensure_namespace("org/project", "development")
    runner = EvaluationRunner(model_router=ModelRouter())
    publish = PublishService(store, PolicyEngine(), runner)

    suite_doc = PlatformResource(
        kind=ResourceKind.EVALUATION_SUITE,
        metadata=ResourceMetadata(name="support-quality", namespace="org/project", version="1.0.0"),
        spec={
            "dataset": [
                {
                    "id": "c1",
                    "input": {"message": "x"},
                    "expected": {"contains": "ZZZ_MISSING"},
                }
            ],
            "evaluators": [{"type": "keyword_match"}],
            "triggers": [{"onPublish": ["agents/support-agent"]}],
            "gates": {"minScore": 0.95},
        },
    )
    await store.upsert_resource_version(ns, suite_doc)
    await store.publish(ns, ResourceKind.EVALUATION_SUITE, "support-quality", "1.0.0")

    agent = PlatformResource(
        kind=ResourceKind.AGENT,
        metadata=ResourceMetadata(name="support-agent", namespace="org/project", version="1.0.0"),
        spec={
            "role": "executor",
            "modelRef": "models/m",
            "promptRef": "prompts/p",
        },
    )
    await store.upsert_resource_version(ns, agent)

    published = await store.list_published(ns)
    bundle = {
        f"{v.kind}:{v.name}": {"kind": v.kind, "name": v.name, "spec": v.spec_json}
        for v in published
        if v.kind and v.name
    }
    bundle["Agent:support-agent"] = {
        "kind": "Agent",
        "name": "support-agent",
        "spec": agent.spec,
    }

    async def execute_fn(_inp):
        return {"content": "hello world", "latency_ms": 1, "cost": 0, "tools_used": []}

    with pytest.raises(PublishGateError) as exc:
        await publish.publish_with_gates(
            ns,
            "org/project",
            ResourceKind.AGENT,
            "support-agent",
            "1.0.0",
            bundle=bundle,
            execute_fn=execute_fn,
        )
    assert exc.value.reason == "evaluation_failed"


@pytest.mark.asyncio
async def test_normalize_execution_event():
    out = normalize_output(
        {
            "type": "done",
            "data": {"content": "ok", "latencyMs": 9, "usage": {"total_tokens": 10}},
        }
    )
    assert out["content"] == "ok"
    assert out["latency_ms"] == 9.0
