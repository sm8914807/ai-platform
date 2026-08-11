"""Durable workflow engine with checkpoints."""

import asyncio
from typing import Any, AsyncIterator

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import ExecutionEvent, WorkflowRunState, WorkflowSpec, WorkflowStep
from ai_platform.db.sql import SqlBackend
from ai_platform.tool_host.host import ToolHost
from ai_platform.core.models import ToolSpec
from ai_platform.workflow.store import WorkflowStateStore


class WorkflowEngine:
    def __init__(
        self,
        agent_engine: AgentEngine | None = None,
        tool_host: ToolHost | None = None,
        state_store: WorkflowStateStore | None = None,
        db_path: str = ".platform/workflows.db",
        sql: SqlBackend | None = None,
    ) -> None:
        self.agent_engine = agent_engine or AgentEngine()
        self.tool_host = tool_host or ToolHost()
        self.state_store = state_store or WorkflowStateStore(db_path=db_path, sql=sql)
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
                    output = await self._run_step(bundle, step, state)
                    state.steps[step.id] = output
                    await self.state_store.record_step(
                        run_id, step.id, "completed", state.input, output
                    )
                    await self.state_store.save_checkpoint(state)
                except WorkflowPaused as paused:
                    state.status = "waiting_approval"
                    self._pending_approvals[run_id] = {
                        "step_id": step.id,
                        "approval_ref": paused.approval_ref,
                    }
                    await self.state_store.save_checkpoint(state)
                    yield ExecutionEvent(
                        type="approval_required",
                        data={"stepId": step.id, "approvalRef": paused.approval_ref},
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
        self, bundle: dict[str, dict], step: WorkflowStep, state: WorkflowRunState
    ) -> dict[str, Any]:
        step_input = {**state.input, **state.steps}

        if step.type == "agent" and step.ref:
            result = await self.agent_engine.execute(
                bundle, step.ref, step_input, stream=False
            )
            if isinstance(result, ExecutionEvent) and result.type == "done":
                return {"status": "ok", "output": result.data}
            if isinstance(result, ExecutionEvent) and result.type == "error":
                raise RuntimeError(result.data.get("message", "agent failed"))
            return {"status": "ok", "output": result.data if isinstance(result, ExecutionEvent) else {}}

        if step.type == "tool" and step.ref:
            tool_doc = self._resolve(bundle, step.ref)
            if not tool_doc:
                raise ValueError(f"Tool not found: {step.ref}")
            tool_spec = ToolSpec.model_validate(tool_doc["spec"])
            result = await self.tool_host.invoke(tool_spec, step_input)
            return {"status": "ok", "output": result.output}

        if step.type == "parallel":
            branches = step.branches or []
            results = await asyncio.gather(
                *[
                    self._run_branch(bundle, branch, state)
                    for branch in branches
                ]
            )
            return {"status": "ok", "branches": results}

        if step.type == "humanApproval":
            approval_ref = step.ref or "approval-flows/default"
            raise WorkflowPaused(approval_ref)

        return {"status": "skipped"}

    async def _run_branch(
        self, bundle: dict[str, dict], branch: dict[str, Any], state: WorkflowRunState
    ) -> dict[str, Any]:
        branch_step = WorkflowStep(
            id=branch.get("id", new_id("branch")),
            type=branch["type"],
            ref=branch.get("ref"),
        )
        return await self._run_step(bundle, branch_step, state)

    async def approve(self, run_id: str, decision: str = "approved") -> WorkflowRunState:
        state = await self.state_store.load_checkpoint(run_id)
        if not state:
            raise ValueError(f"Run not found: {run_id}")
        pending = self._pending_approvals.get(run_id)
        if pending:
            state.steps[pending["step_id"]] = {"status": decision}
            del self._pending_approvals[run_id]
        state.status = "running"
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
            if step.id in state.steps and step.type != "humanApproval":
                if not started:
                    continue
            if not self._eval_when(step.when, state):
                continue
            if step.id in state.steps and state.steps[step.id].get("status") == "ok":
                continue

            state.current_step_id = step.id
            try:
                output = await self._run_step(bundle, step, state)
                state.steps[step.id] = output
                await self.state_store.record_step(
                    run_id, step.id, "completed", state.input, output
                )
                await self.state_store.save_checkpoint(state)
            except WorkflowPaused as paused:
                state.status = "waiting_approval"
                self._pending_approvals[run_id] = {
                    "step_id": step.id,
                    "approval_ref": paused.approval_ref,
                }
                await self.state_store.save_checkpoint(state)
                return state

        state.status = "completed"
        state.output = {"steps": state.steps}
        await self.state_store.save_checkpoint(state)
        return state


class WorkflowPaused(Exception):
    def __init__(self, approval_ref: str) -> None:
        self.approval_ref = approval_ref
        super().__init__(f"approval required: {approval_ref}")
