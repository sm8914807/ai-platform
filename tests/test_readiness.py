"""Production readiness engine + API — deploy decision, not another dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.readiness.engine import ProductionReadinessEngine, ReadinessReport


def _thin_bundle() -> dict:
    return {
        "Agent:thin-agent": {
            "name": "thin-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/thin-route",
                "promptRef": "prompts/thin-prompt",
            },
        },
        "ModelRoute:thin-route": {
            "name": "thin-route",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "x"}],
            },
        },
        "Prompt:thin-prompt": {"name": "thin-prompt", "spec": {"template": "hi {{ message }}"}},
    }


def _ready_bundle() -> dict:
    dataset = [
        {"id": f"c{i}", "input": {"message": "billing"}, "expected": {"contains": "billing"}}
        for i in range(8)
    ]
    return {
        "Agent:refund-agent": {
            "name": "refund-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/ready-route",
                "promptRef": "prompts/ready-prompt",
                "toolboxRef": "toolboxes/ready-box",
                "guardrails": ["guardrails/injection-block", "guardrails/pii-mask"],
                "policies": ["policies/ready-policy"],
                "owner": "team:payments",
            },
        },
        "ModelRoute:ready-route": {
            "name": "ready-route",
            "spec": {
                "strategy": "costOptimized",
                "candidates": [
                    {
                        "provider": "mock",
                        "model": "primary",
                        "weight": 80,
                        "maxLatencyMs": 4000,
                    },
                    {"provider": "mock", "model": "fallback", "weight": 20, "fallback": True},
                ],
            },
        },
        "Prompt:ready-prompt": {
            "name": "ready-prompt",
            "spec": {"template": "You are a refund agent. {{ message }}"},
        },
        "Toolbox:ready-box": {
            "name": "ready-box",
            "spec": {
                "tools": [
                    {"ref": "tools/lookup", "permissions": ["read"]},
                    {
                        "ref": "tools/issue-refund",
                        "permissions": ["refund"],
                        "requireApproval": True,
                    },
                ]
            },
        },
        "Guardrail:injection-block": {
            "name": "injection-block",
            "spec": {"type": "injection_detect", "config": {"action": "block"}},
        },
        "Guardrail:pii-mask": {
            "name": "pii-mask",
            "spec": {"type": "pii_mask", "config": {}},
        },
        "Policy:ready-policy": {
            "name": "ready-policy",
            "spec": {
                "rules": [
                    {
                        "effect": "allow",
                        "principals": ["*"],
                        "actions": ["agent:run", "resource:publish"],
                        "resources": ["*"],
                    }
                ]
            },
        },
        "EvaluationSuite:refund-quality": {
            "name": "refund-quality",
            "spec": {
                "dataset": dataset,
                "evaluators": [
                    {"type": "keyword_match"},
                    {"type": "tool_accuracy"},
                    {"type": "faithfulness"},
                    {"type": "cost", "maxPerRun": 1.0},
                    {"type": "latency", "maxP95Ms": 5000},
                ],
                "triggers": [{"onPublish": ["agents/refund-agent"]}],
                "gates": {"minScore": 0.5},
            },
        },
        "Environment:production": {
            "name": "production",
            "spec": {
                "requireApproval": True,
                "approvers": ["team:platform-admins"],
                "bundlePolicy": "signed-only",
            },
        },
    }


def _ready_eval_runs() -> list[dict]:
    return [
        {
            "runId": "eval-1",
            "targetRef": "agents/refund-agent",
            "passed": True,
            "overall": 0.94,
            "scores": {"keyword": 1.0, "cost": 0.9, "tool_accuracy": 0.98},
        }
    ]


def _ready_metrics() -> dict:
    return {
        "overview": {
            "requests": 24,
            "successRate": 0.99,
            "p50LatencyMs": 180,
            "p95LatencyMs": 420,
            "totalCostUnits": 0.48,
        }
    }


def test_unpublished_agent_is_zero_not_ready():
    engine = ProductionReadinessEngine()
    report = engine.assess(agent_ref="agents/ghost", bundle={}, published=False)
    assert report.overall == 0
    assert report.decision == "not_ready"
    assert any("not in published bundle" in b for b in report.blockers)


def test_thin_agent_blocked_without_injection_protection():
    engine = ProductionReadinessEngine()
    report = engine.assess(
        agent_ref="agents/thin-agent",
        bundle=_thin_bundle(),
        auth_required=True,
        dev_login_enabled=False,
    )
    assert report.decision == "not_ready"
    assert report.overall < 70
    ids = {c.id: c for d in report.dimensions for c in d.checks}
    assert ids["sec.injection_block"].blocking
    assert "injection" in ids["sec.injection_block"].detail.lower()


def test_ready_agent_can_be_safe_to_deploy():
    engine = ProductionReadinessEngine()
    report = engine.assess(
        agent_ref="agents/refund-agent",
        bundle=_ready_bundle(),
        version="1.0.0",
        eval_runs=_ready_eval_runs(),
        route_metrics=_ready_metrics(),
        published=True,
        bundle_hash="abc123def4567890",
        has_publish_audit=True,
        auth_required=True,
        dev_login_enabled=False,
    )
    assert report.overall >= 80
    assert report.decision == "safe_to_deploy"
    assert report.blockers == []
    by_name = {d.name: d.score for d in report.dimensions}
    assert by_name["security"] >= 85
    assert by_name["quality"] >= 85


def test_ready_scores_higher_than_thin():
    engine = ProductionReadinessEngine()
    thin = engine.assess(
        agent_ref="agents/thin-agent",
        bundle=_thin_bundle(),
        auth_required=True,
        dev_login_enabled=False,
    )
    ready = engine.assess(
        agent_ref="agents/refund-agent",
        bundle=_ready_bundle(),
        eval_runs=_ready_eval_runs(),
        route_metrics=_ready_metrics(),
        published=True,
        bundle_hash="abc123",
        has_publish_audit=True,
        auth_required=True,
        dev_login_enabled=False,
    )
    assert ready.overall > thin.overall


def test_alert_only_injection_is_a_blocker():
    engine = ProductionReadinessEngine()
    bundle = _ready_bundle()
    bundle["Guardrail:injection-block"]["spec"]["config"]["action"] = "alert"
    report = engine.assess(
        agent_ref="agents/refund-agent",
        bundle=bundle,
        eval_runs=_ready_eval_runs(),
        published=True,
        auth_required=True,
        dev_login_enabled=False,
    )
    assert report.decision == "not_ready"
    assert any("not block" in b.lower() or "alert" in b.lower() for b in report.blockers)


def test_score_drop_marks_degraded_watch():
    engine = ProductionReadinessEngine()
    engine.assess(
        agent_ref="agents/refund-agent",
        bundle=_ready_bundle(),
        eval_runs=_ready_eval_runs(),
        route_metrics=_ready_metrics(),
        published=True,
        bundle_hash="abc123",
        has_publish_audit=True,
        auth_required=True,
        dev_login_enabled=False,
    )
    engine._last["agents/refund-agent"] = ReadinessReport(
        agent_ref="agents/refund-agent",
        overall=99,
        decision="safe_to_deploy",
        decision_label="SAFE TO DEPLOY",
        dimensions=engine._last["agents/refund-agent"].dimensions,
    )
    worse = _ready_bundle()
    worse["Guardrail:injection-block"]["spec"]["config"]["action"] = "alert"
    worse["ModelRoute:ready-route"]["spec"]["strategy"] = "latency"
    report = engine.assess(
        agent_ref="agents/refund-agent",
        bundle=worse,
        eval_runs=[],
        published=True,
        auth_required=True,
        dev_login_enabled=True,
    )
    assert report.previous_overall == 99
    assert report.drift is not None
    assert report.drift["degraded"] is True
    assert report.decision in {"watch", "not_ready"}


@pytest.fixture
async def authed_client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "ready.db"), auth_required=True)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            login = await ac.post(
                "/v1/auth/login",
                json={"email": "ops@example.com", "orgId": "default-org", "displayName": "Ops"},
            )
            assert login.status_code == 200, login.text
            ac.headers["Authorization"] = f"Bearer {login.json()['accessToken']}"
            yield ac


async def _put_publish(client: AsyncClient, ns: str, kind: str, name: str, spec: dict) -> None:
    body = {
        "api_version": "platform.ai/v1",
        "kind": kind,
        "metadata": {"name": name, "version": "1.0.0", "namespace": ns},
        "spec": spec,
    }
    r = await client.put(f"/v1/{ns}/{kind}/{name}/versions/1.0.0", json=body)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/v1/{ns}/{kind}/{name}/publish",
        json={"version": "1.0.0", "principal": "ops@example.com"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_readiness_api_unpublished_404(authed_client: AsyncClient):
    ns = "default-org/default-project"
    missing = await authed_client.get(f"/v1/{ns}/readiness/does-not-exist")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_readiness_api_inventory_and_production_check(authed_client: AsyncClient):
    ns = "default-org/default-project"
    thin = _thin_bundle()
    await _put_publish(authed_client, ns, "Prompt", "thin-prompt", thin["Prompt:thin-prompt"]["spec"])
    await _put_publish(authed_client, ns, "ModelRoute", "thin-route", thin["ModelRoute:thin-route"]["spec"])
    await _put_publish(authed_client, ns, "Agent", "thin-agent", thin["Agent:thin-agent"]["spec"])

    ready = _ready_bundle()
    order = [
        ("Prompt", "ready-prompt"),
        ("ModelRoute", "ready-route"),
        ("Guardrail", "injection-block"),
        ("Guardrail", "pii-mask"),
        ("Policy", "ready-policy"),
        ("Toolbox", "ready-box"),
        ("Environment", "production"),
        ("Agent", "refund-agent"),
        ("EvaluationSuite", "refund-quality"),
    ]
    for kind, name in order:
        await _put_publish(authed_client, ns, kind, name, ready[f"{kind}:{name}"]["spec"])

    inventory = await authed_client.get(f"/v1/{ns}/readiness")
    assert inventory.status_code == 200, inventory.text
    body = inventory.json()
    assert body["count"] >= 2
    by_ref = {a["agentRef"]: a for a in body["agents"]}
    assert by_ref["agents/thin-agent"]["decision"] == "not_ready"
    assert any("injection" in b.lower() for b in by_ref["agents/thin-agent"]["blockers"])
    assert by_ref["agents/refund-agent"]["overall"] > by_ref["agents/thin-agent"]["overall"]

    one = await authed_client.get(f"/v1/{ns}/readiness/refund-agent")
    assert one.status_code == 200, one.text
    assert one.json()["agentRef"] == "agents/refund-agent"
    assert "decisionLabel" in one.json()

    checked = await authed_client.post(f"/v1/{ns}/readiness/refund-agent/check")
    assert checked.status_code == 200, checked.text
    assert checked.json()["decision"] in {"safe_to_deploy", "watch", "not_ready"}
