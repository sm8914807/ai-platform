"""Publish pipeline with policy + eval gates."""

from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import AuditEvent, PolicyContext, ResourceKind
from ai_platform.evaluation.runner import EvaluationRunner
from ai_platform.policy.engine import PolicyEngine


class PublishGateError(Exception):
    def __init__(self, reason: str, details: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.details = details or {}
        super().__init__(reason)


class PublishService:
    def __init__(
        self,
        registry: Any,
        policy_engine: PolicyEngine | None = None,
        eval_runner: EvaluationRunner | None = None,
    ) -> None:
        self.registry = registry
        self.policy_engine = policy_engine or PolicyEngine()
        self.eval_runner = eval_runner or EvaluationRunner()

    async def publish_with_gates(
        self,
        namespace_id: str,
        namespace_path: str,
        kind: ResourceKind,
        name: str,
        version: str,
        principal: str = "anonymous",
        environment: str = "development",
        bundle: dict[str, dict] | None = None,
        execute_fn: Any | None = None,
        eval_suite_ref: str | None = None,
    ) -> dict[str, Any]:
        resource_ref = f"{kind.value}/{name}"

        decision = self.policy_engine.evaluate(
            PolicyContext(
                principal=principal,
                action="resource:publish",
                resource=resource_ref,
                environment=environment,
                org_id=namespace_path.split("/")[0],
            )
        )
        if not decision.allowed:
            raise PublishGateError("policy_denied", {"reason": decision.reason})

        if bundle and eval_suite_ref:
            suite = self.eval_runner.load_suite_from_bundle(bundle, eval_suite_ref)
            if suite:
                result = await self.eval_runner.run_suite(
                    suite, resource_ref, version, execute_fn
                )
                if not result.passed:
                    raise PublishGateError(
                        "evaluation_failed",
                        {"scores": result.scores, "details": result.details},
                    )

        await self.registry.publish(namespace_id, kind, name, version)

        audit = AuditEvent(
            id=new_id("audit"),
            org_id=namespace_path.split("/")[0],
            actor_id=principal,
            action="resource.published",
            resource_ref=resource_ref,
            payload={"version": version, "gates": "passed"},
            created_at=datetime.now(timezone.utc),
        )
        await self.registry.append_audit(audit)

        return {"published": True, "version": version, "gates": "passed"}
