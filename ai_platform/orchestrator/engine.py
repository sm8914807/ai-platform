"""Top-level execution coordinator."""

from typing import Any, AsyncIterator

from ai_platform.agent.engine import AgentEngine
from ai_platform.agent.multi import MultiAgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import ExecutionEvent, ExecutionRequest, PolicyContext
from ai_platform.policy.engine import PolicyEngine
from ai_platform.telemetry.tracing import get_tracer
from ai_platform.workflow.engine import WorkflowEngine


class Orchestrator:
    def __init__(
        self,
        agent_engine: AgentEngine | None = None,
        workflow_engine: WorkflowEngine | None = None,
        policy_engine: PolicyEngine | None = None,
        multi_agent_engine: MultiAgentEngine | None = None,
    ) -> None:
        self.agent_engine = agent_engine or AgentEngine()
        self.workflow_engine = workflow_engine or WorkflowEngine(agent_engine=self.agent_engine)
        self.policy_engine = policy_engine or PolicyEngine()
        self.multi_agent_engine = multi_agent_engine or MultiAgentEngine(self.agent_engine)
        self._bundle_index: dict[str, dict[str, dict]] = {}
        self._tracer = get_tracer("orchestrator")

    def load_bundle(self, bundle_key: str, resources: list[dict[str, Any]]) -> None:
        index: dict[str, dict] = {}
        for r in resources:
            key = f"{r['kind']}:{r['name']}"
            index[key] = r
        self._bundle_index[bundle_key] = index
        self.policy_engine.load_from_bundle(index)

    def _has_collaboration(self, bundle: dict[str, dict], agent_ref: str) -> bool:
        parts = agent_ref.split("/", 1)
        if len(parts) != 2:
            return False
        doc = bundle.get(f"Agent:{parts[1]}")
        if not doc:
            return False
        spec = doc.get("spec", {})
        return bool(spec.get("collaboration") or spec.get("supervisorRef"))

    async def execute(
        self,
        bundle_key: str,
        request: ExecutionRequest,
        principal: str = "anonymous",
        environment: str = "development",
        org_id: str = "default-org",
        namespace_id: str = "local",
        multi_agent: bool = False,
        collaboration: Any | None = None,
    ) -> AsyncIterator[ExecutionEvent] | ExecutionEvent | Any:
        bundle = self._bundle_index.get(bundle_key, {})
        trace_id = request.trace_id or new_id("trace")

        decision = self.policy_engine.evaluate(
            PolicyContext(
                principal=principal,
                action="agent:run" if request.resource_ref.startswith("agents/") else (
                    "workflow:run" if request.resource_ref.startswith("workflows/") else "resource:run"
                ),
                resource=request.resource_ref,
                environment=environment,
                org_id=org_id,
            )
        )
        if not decision.allowed:
            event = ExecutionEvent(
                type="error",
                data={
                    "message": "policy denied",
                    "reason": decision.reason,
                    "matchedRule": decision.matched_rule,
                    "action": (
                        "agent:run"
                        if request.resource_ref.startswith("agents/")
                        else "workflow:run"
                        if request.resource_ref.startswith("workflows/")
                        else "resource:run"
                    ),
                    "diagnosis": (
                        "A published Policy denied this execution. "
                        "Allow the principal for this action/resource or remove the deny rule."
                    ),
                },
                execution_id=trace_id,
            )
            if request.stream:
                async def _deny():
                    yield event
                return _deny()
            return event

        with self._tracer.start_as_current_span("orchestrator.execute") as span:
            span.set_attribute("resource.ref", request.resource_ref)
            span.set_attribute("trace.id", trace_id)

            if request.resource_ref.startswith("agents/"):
                use_multi = multi_agent or self._has_collaboration(bundle, request.resource_ref)
                if use_multi:
                    from ai_platform.core.models import CollaborationSpec

                    override = None
                    if collaboration is not None:
                        override = (
                            collaboration
                            if isinstance(collaboration, CollaborationSpec)
                            else CollaborationSpec.model_validate(collaboration)
                        )
                    result = await self.multi_agent_engine.run(
                        bundle,
                        request.resource_ref,
                        request.input,
                        collaboration=override,
                        session_id=request.session_id,
                        org_id=org_id,
                        namespace_id=namespace_id,
                        principal=principal,
                        environment=environment,
                        policy_engine=self.policy_engine,
                    )
                    event = ExecutionEvent(
                        type="done" if result.status != "failed" else "error",
                        data={
                            "pattern": result.pattern,
                            "iterations": result.iterations,
                            "steps": result.steps,
                            "status": result.status,
                            "errors": result.errors,
                            "wiring": result.wiring,
                            "content": result.final_output.get("content", result.final_output),
                            "finalOutput": result.final_output,
                            "message": (
                                result.errors[0].get("message")
                                if result.status == "failed" and result.errors
                                else None
                            ),
                            "diagnosis": (
                                result.errors[0].get("diagnosis")
                                if result.status == "failed" and result.errors
                                else None
                            ),
                        },
                        execution_id=trace_id,
                    )
                    if request.stream:
                        async def _multi():
                            yield event
                        return _multi()
                    return event

                result = await self.agent_engine.execute(
                    bundle,
                    request.resource_ref,
                    request.input,
                    stream=request.stream,
                    session_id=request.session_id,
                    org_id=org_id,
                    namespace_id=namespace_id,
                    principal=principal,
                    environment=environment,
                    policy_engine=self.policy_engine,
                )
                if request.stream:
                    async def _wrap():
                        async for event in result:
                            event.execution_id = event.execution_id or trace_id
                            yield event
                    return _wrap()
                if isinstance(result, ExecutionEvent):
                    result.execution_id = result.execution_id or trace_id
                return result

            if request.resource_ref.startswith("workflows/"):
                await self.workflow_engine.initialize()
                result = await self.workflow_engine.run(
                    bundle,
                    request.resource_ref,
                    request.input,
                    org_id=org_id,
                    namespace_id=namespace_id,
                    stream=request.stream,
                    principal=principal,
                    environment=environment,
                    policy_engine=self.policy_engine,
                )
                if request.stream:
                    return result
                return result

            event = ExecutionEvent(
                type="error",
                data={"message": f"Unsupported resource ref: {request.resource_ref}"},
                execution_id=trace_id,
            )
            if request.stream:
                async def _err():
                    yield event
                return _err()
            return event
