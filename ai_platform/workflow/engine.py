"""Durable workflow engine with checkpoints."""

import asyncio
from typing import Any, AsyncIterator

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import ExecutionEvent, ToolSpec, WorkflowRunState, WorkflowSpec, WorkflowStep
from ai_platform.db.sql import SqlBackend
from ai_platform.governor.engine import ToolGovernor, quota_for_tool
from ai_platform.tool_host.host import ToolHost
from ai_platform.workflow.store import WorkflowStateStore


class WorkflowEngine:
    def __init__(
        self,
        agent_engine: AgentEngine | None = None,
        tool_host: ToolHost | None = None,
        state_store: WorkflowStateStore | None = None,
        db_path: str = ".platform/workflows.db",
        sql: SqlBackend | None = None,
        governor: ToolGovernor | None = None,
    ) -> None:
        self.agent_engine = agent_engine or AgentEngine()
        self.tool_host = tool_host or ToolHost()
        self.state_store = state_store or WorkflowStateStore(db_path=db_path, sql=sql)
        self.governor = governor or self.agent_engine.governor
        self._pending_approvals: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        await self.state_store.migrate()

    def _resolve(self, bundle: dict[str, dict], ref: str) -> dict[str, Any] | None:
        parts = ref.split("/", 1)
        if len(parts) != 2:
            return None
        plural, name = parts
        kind_map = {
            "agents": "Agent",
            "tools": "Tool",
            "workflows": "Workflow",
            "approval-flows": "ApprovalFlow",
        }
        kind = kind_map.get(plural)
        if not kind:
            return None
        return bundle.get(f"{kind}:{name}")

    def _eval_when(self, when: str | None, state: WorkflowRunState) -> bool:
        if not when:
            return True
        # Simple expression: $.steps.approve.status == approved
        if "approved" in when and state.steps.get("approve", {}).get("status") == "approved":
            return True
        if "==" not in when:
            return True
        return False

    async def run(
        self,
        bundle: dict[str, dict],
        workflow_ref: str,
        input_data: dict[str, Any],
        org_id: str,
        namespace_id: str,
        workflow_version_id: str = "local",
        stream: bool = False,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> AsyncIterator[ExecutionEvent] | WorkflowRunState:
        workflow_doc = self._resolve(bundle, workflow_ref)
        if not workflow_doc:
            raise ValueError(f"Workflow not found: {workflow_ref}")

        spec = WorkflowSpec.model_validate(workflow_doc["spec"])
        run_id = await self.state_store.create_run(
            workflow_version_id, org_id, namespace_id, input_data, workflow_ref
        )
        state = WorkflowRunState(
            run_id=run_id,
            workflow_ref=workflow_ref,
            status="running",
            input=input_data,
        )

        async def _execute() -> AsyncIterator[ExecutionEvent]:
            yield ExecutionEvent(
                type="token",
                data={"text": f"workflow started: {workflow_ref}\n"},
                execution_id=run_id,
            )

            for step in spec.steps:
                if not self._eval_when(step.when, state):
                    continue

                state.current_step_id = step.id
                yield ExecutionEvent(
                    type="token",
                    data={"text": f"step {step.id} ({step.type})\n"},
                    execution_id=run_id,
                )

                try:
                    output = await self._run_step(
                        bundle,
                        step,
                        state,
                        org_id,
                        namespace_id,
                        principal=principal,
                        environment=environment,
                        policy_engine=policy_engine,
                    )
                    state.steps[step.id] = output
                    await self.state_store.record_step(
                        run_id, step.id, "completed", state.input, output
                    )
                    await self.state_store.save_checkpoint(state)
                except WorkflowPaused as paused:
                    state.status = "waiting_approval"
                    pending = {
                        "step_id": step.id,
                        "approval_ref": paused.approval_ref,
                        "reason": paused.reason,
                    }
                    self._pending_approvals[run_id] = pending
                    state.pending_approval = pending
                    await self.state_store.save_checkpoint(state)
                    yield ExecutionEvent(
                        type="approval_required",
                        data={
                            "stepId": step.id,
                            "approvalRef": paused.approval_ref,
                            "reason": paused.reason,
                            **paused.data,
                        },
                        execution_id=run_id,
                    )
                    return
                except Exception as e:
                    state.status = "failed"
                    state.output = {"error": str(e), "step": step.id}
                    await self.state_store.record_step(
                        run_id, step.id, "failed", state.input, {}, {"message": str(e)}
                    )
                    await self.state_store.save_checkpoint(state)
                    yield ExecutionEvent(type="error", data={"message": str(e)}, execution_id=run_id)
                    return

            state.status = "completed"
            state.output = {"steps": state.steps}
            await self.state_store.save_checkpoint(state)
            yield ExecutionEvent(type="done", data=state.output, execution_id=run_id)

        if stream:
            return _execute()

        async for _ in _execute():
            pass
        return state

    async def _run_step(
        self,
        bundle: dict[str, dict],
        step: WorkflowStep,
        state: WorkflowRunState,
        org_id: str,
        namespace_id: str,
        skip_quota: bool = False,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> dict[str, Any]:
        step_input = {**state.input, **state.steps}
        if skip_quota:
            step_input = {**step_input, "governor_override": True}

        if step.type == "agent" and step.ref:
            result = await self.agent_engine.execute(
                bundle,
                step.ref,
                step_input,
                stream=False,
                org_id=org_id,
                namespace_id=namespace_id,
                principal=principal,
                environment=environment,
                policy_engine=policy_engine,
            )
            if isinstance(result, ExecutionEvent) and result.type == "done":
                return {"status": "ok", "output": result.data}
            if isinstance(result, ExecutionEvent) and result.type == "error":
                raise RuntimeError(result.data.get("message", "agent failed"))
            if isinstance(result, ExecutionEvent) and result.type == "approval_required":
                raise WorkflowPaused(
                    result.data.get("approvalRef", "approval-flows/rate-limit"),
                    reason=str(result.data.get("reason", "approval_required")),
                    data=result.data,
                )
            return {"status": "ok", "output": result.data if isinstance(result, ExecutionEvent) else {}}

        if step.type == "tool" and step.ref:
            tool_doc = self._resolve(bundle, step.ref)
            if not tool_doc:
                raise ValueError(f"Tool not found: {step.ref}")
            tool_spec = ToolSpec.model_validate(tool_doc["spec"])
            if policy_engine is not None:
                from ai_platform.core.models import PolicyContext

                decision = policy_engine.evaluate(
                    PolicyContext(
                        principal=principal,
                        action="tool:invoke",
                        resource=step.ref,
                        environment=environment,
                        org_id=org_id,
                    )
                )
                if not decision.allowed:
                    raise RuntimeError(
                        f"policy denied: {decision.reason}"
                        + (f" ({decision.matched_rule})" if decision.matched_rule else "")
                    )
            rate_limit, _require_approval = quota_for_tool(bundle, step.ref)
            quota = rate_limit or tool_spec.rate_limit
            if not skip_quota:
                decision = await self.governor.check(
                    tool_ref=step.ref,
                    rate_limit=quota,
                    org_id=org_id,
                    namespace_id=namespace_id,
                )
                if not decision.allowed:
                    payload = self.governor.approval_payload(
                        decision,
                        tool_name=tool_spec.manifest.name,
                        tool_ref=step.ref,
                    )
                    raise WorkflowPaused(
                        payload["approvalRef"],
                        reason=str(payload["reason"]),
                        data=payload,
                    )
            result = await self.tool_host.invoke(tool_spec, step_input)
            return {"status": "ok", "output": result.output}

        if step.type == "parallel":
            branches = step.branches or []
            results = await asyncio.gather(
                *[
                    self._run_branch(
                        bundle,
                        branch,
                        state,
                        org_id,
                        namespace_id,
                        principal=principal,
                        environment=environment,
                        policy_engine=policy_engine,
                    )
                    for branch in branches
                ]
            )
            return {"status": "ok", "branches": results}

        if step.type == "humanApproval":
            approval_ref = step.ref or "approval-flows/default"
            raise WorkflowPaused(approval_ref)

        return {"status": "skipped"}

    async def _run_branch(
        self,
        bundle: dict[str, dict],
        branch: dict[str, Any],
        state: WorkflowRunState,
        org_id: str,
        namespace_id: str,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> dict[str, Any]:
        branch_step = WorkflowStep(
            id=branch.get("id", new_id("branch")),
            type=branch["type"],
            ref=branch.get("ref"),
        )
        return await self._run_step(
            bundle,
            branch_step,
            state,
            org_id,
            namespace_id,
            principal=principal,
            environment=environment,
            policy_engine=policy_engine,
        )

    async def approve(self, run_id: str, decision: str = "approved") -> WorkflowRunState:
        state = await self.state_store.load_checkpoint(run_id)
        if not state:
            raise ValueError(f"Run not found: {run_id}")
        pending = self._resolve_pending(run_id, state)
        if pending:
            if pending.get("reason") == "rate_limit_exceeded":
                pending = {**pending, "decision": decision}
                self._pending_approvals[run_id] = pending
                state.pending_approval = pending
            else:
                state.steps[pending["step_id"]] = {"status": decision}
                self._pending_approvals.pop(run_id, None)
                state.pending_approval = None
        elif state.current_step_id:
            state.steps[state.current_step_id] = {"status": decision}
            state.pending_approval = None
        state.status = "running" if decision == "approved" else "failed"
        if decision != "approved":
            state.output = {**state.output, "decision": decision, "rejected": True}
        await self.state_store.save_checkpoint(state)
        return state

    async def resume(
        self,
        run_id: str,
        bundle: dict[str, dict],
        org_id: str,
        namespace_id: str,
    ) -> WorkflowRunState:
        state = await self.state_store.load_checkpoint(run_id)
        if not state:
            raise ValueError(f"Run not found: {run_id}")

        workflow_doc = self._resolve(bundle, state.workflow_ref)
        if not workflow_doc:
            raise ValueError(f"Workflow not found: {state.workflow_ref}")
        spec = WorkflowSpec.model_validate(workflow_doc["spec"])

        started = False
        for step in spec.steps:
            if step.id in state.steps and step.type == "humanApproval":
                # Decision already recorded by approve(); do not re-pause.
                started = True
                continue
            if step.id in state.steps and step.type != "humanApproval":
                if not started:
                    continue
            if not self._eval_when(step.when, state):
                continue
            if step.id in state.steps and state.steps[step.id].get("status") == "ok":
                continue

            state.current_step_id = step.id
            try:
                pending = self._resolve_pending(run_id, state)
                skip_quota = bool(
                    pending
                    and pending.get("step_id") == step.id
                    and pending.get("reason") == "rate_limit_exceeded"
                    and pending.get("decision") == "approved"
                )
                output = await self._run_step(
                    bundle, step, state, org_id, namespace_id, skip_quota=skip_quota
                )
                state.steps[step.id] = output
                if skip_quota:
                    self._pending_approvals.pop(run_id, None)
                    state.pending_approval = None
                started = True
                await self.state_store.record_step(
                    run_id, step.id, "completed", state.input, output
                )
                await self.state_store.save_checkpoint(state)
            except WorkflowPaused as paused:
                state.status = "waiting_approval"
                pending = {
                    "step_id": step.id,
                    "approval_ref": paused.approval_ref,
                    "reason": paused.reason,
                }
                self._pending_approvals[run_id] = pending
                state.pending_approval = pending
                await self.state_store.save_checkpoint(state)
                return state

        state.status = "completed"
        state.output = {"steps": state.steps}
        state.pending_approval = None
        await self.state_store.save_checkpoint(state)
        return state

    def _resolve_pending(self, run_id: str, state: WorkflowRunState) -> dict[str, Any] | None:
        pending = self._pending_approvals.get(run_id) or state.pending_approval
        if pending:
            return pending
        if state.status == "waiting_approval" and state.current_step_id:
            return {
                "step_id": state.current_step_id,
                "approval_ref": "approval-flows/default",
                "reason": "approval_required",
            }
        return None

    async def list_inbox(
        self,
        *,
        namespace_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        runs = await self.state_store.list_runs(
            status="waiting_approval", namespace_id=namespace_id, limit=limit
        )
        for item in runs:
            run_id = item["runId"]
            mem = self._pending_approvals.get(run_id)
            if mem and not item.get("pendingApproval"):
                item["pendingApproval"] = mem
            elif item.get("pendingApproval") and run_id not in self._pending_approvals:
                self._pending_approvals[run_id] = item["pendingApproval"]
        return runs

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        state = await self.state_store.load_checkpoint(run_id)
        if not state:
            return None
        pending = self._resolve_pending(run_id, state)
        return {
            "runId": state.run_id,
            "workflowRef": state.workflow_ref,
            "status": state.status,
            "currentStepId": state.current_step_id,
            "input": state.input,
            "output": state.output,
            "steps": state.steps,
            "checkpointSeq": state.checkpoint_seq,
            "pendingApproval": pending,
        }


class WorkflowPaused(Exception):
    def __init__(
        self,
        approval_ref: str,
        reason: str = "approval_required",
        data: dict[str, Any] | None = None,
    ) -> None:
        self.approval_ref = approval_ref
        self.reason = reason
        self.data = data or {}
        super().__init__(f"approval required: {approval_ref}")
