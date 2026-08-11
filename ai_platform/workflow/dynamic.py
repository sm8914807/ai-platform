"""Dynamic workflow mode — AI planner generates IR at runtime, then executes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import WorkflowSpec, WorkflowStep
from ai_platform.db.sql import SqlBackend, create_sql_backend
from ai_platform.workflow.engine import WorkflowEngine

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "005_differentiators.sql"


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


class DynamicStepIR(BaseModel):
    id: str
    type: Literal["agent", "tool", "parallel", "humanApproval", "condition"]
    ref: str | None = None
    when: str | None = None
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    branches: list[dict[str, Any]] = Field(default_factory=list)


class DynamicWorkflowIR(BaseModel):
    """Compiled intermediate representation for a runtime-generated workflow."""

    name: str
    description: str | None = None
    steps: list[DynamicStepIR]
    source: Literal["planner", "user", "template"] = "planner"


class PlanRequest(BaseModel):
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    available_agents: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)


class DynamicWorkflowResult(BaseModel):
    workflow_id: str
    ir: DynamicWorkflowIR
    status: str
    steps_output: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class DynamicWorkflowPlanner:
    """Turns a natural-language goal into a DynamicWorkflowIR.

    Phase 5 uses a deterministic heuristic planner. Swap for LLM planner via ModelRouter.
    """

    def plan(self, request: PlanRequest, discovery_hits: list[str] | None = None) -> DynamicWorkflowIR:
        goal = request.goal.lower()
        steps: list[DynamicStepIR] = []
        agents = request.available_agents or (discovery_hits or ["agents/support-agent"])
        tools = request.available_tools

        # Heuristic: research → synthesize → optional approval
        if any(k in goal for k in ("research", "analyze", "compare", "market")):
            for i, agent in enumerate(agents[:3]):
                steps.append(
                    DynamicStepIR(
                        id=f"research-{i}",
                        type="agent",
                        ref=agent,
                        description=f"Research slice for: {request.goal}",
                        capabilities=["research", "executor"],
                    )
                )
            if len(steps) > 1:
                steps = [
                    DynamicStepIR(
                        id="parallel-research",
                        type="parallel",
                        description="Parallel research",
                        branches=[
                            {"id": s.id, "type": s.type, "ref": s.ref}
                            for s in steps
                        ],
                    )
                ]
            steps.append(
                DynamicStepIR(
                    id="synthesize",
                    type="agent",
                    ref=agents[0],
                    description="Synthesize research results",
                    capabilities=["synthesis"],
                )
            )
        elif any(k in goal for k in ("approve", "onboard", "provision")):
            steps.append(
                DynamicStepIR(
                    id="enrich",
                    type="agent",
                    ref=agents[0],
                    description="Enrich input for onboarding",
                )
            )
            steps.append(
                DynamicStepIR(
                    id="approve",
                    type="humanApproval",
                    ref="approval-flows/manager-signoff",
                    description="Human approval gate",
                )
            )
            steps.append(
                DynamicStepIR(
                    id="complete",
                    type="agent",
                    ref=agents[0],
                    when="$.steps.approve.status == approved",
                    description="Complete after approval",
                )
            )
        else:
            # Default: single agent + optional tool
            steps.append(
                DynamicStepIR(
                    id="execute",
                    type="agent",
                    ref=agents[0],
                    description=request.goal,
                )
            )
            if tools:
                steps.append(
                    DynamicStepIR(
                        id="tool-step",
                        type="tool",
                        ref=tools[0],
                        description="Follow-up tool call",
                    )
                )

        return DynamicWorkflowIR(
            name=f"dyn-{new_id('plan')[:8]}",
            description=request.goal,
            steps=steps,
            source="planner",
        )

    def ir_to_workflow_spec(self, ir: DynamicWorkflowIR) -> WorkflowSpec:
        steps: list[WorkflowStep] = []
        for s in ir.steps:
            step_type = s.type if s.type != "condition" else "agent"
            steps.append(
                WorkflowStep(
                    id=s.id,
                    type=step_type,  # type: ignore[arg-type]
                    ref=s.ref,
                    when=s.when,
                    branches=s.branches,
                )
            )
        return WorkflowSpec(trigger={"type": "dynamic"}, steps=steps)


class DynamicWorkflowEngine:
    """Plan → IR → execute via WorkflowEngine."""

    def __init__(
        self,
        db_path: str | None = None,
        agent_engine: AgentEngine | None = None,
        workflow_engine: WorkflowEngine | None = None,
        planner: DynamicWorkflowPlanner | None = None,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")
        self.agent_engine = agent_engine or AgentEngine()
        self.workflow_engine = workflow_engine or WorkflowEngine(
            agent_engine=self.agent_engine, db_path=self.db_path, sql=self.sql
        )
        self.planner = planner or DynamicWorkflowPlanner()

    async def migrate(self) -> None:
        # no-op or sqlite-only script; centralized migrate_aux_stores handles full migrate
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())

    async def plan_and_run(
        self,
        namespace_id: str,
        org_id: str,
        request: PlanRequest,
        bundle: dict[str, dict],
        discovery_hits: list[str] | None = None,
    ) -> DynamicWorkflowResult:
        ir = self.planner.plan(request, discovery_hits)
        return await self.run_ir(
            namespace_id, org_id, ir, {"goal": request.goal, **request.constraints}, bundle
        )

    async def run_ir(
        self,
        namespace_id: str,
        org_id: str,
        ir: DynamicWorkflowIR,
        input_data: dict[str, Any],
        bundle: dict[str, dict],
    ) -> DynamicWorkflowResult:
        workflow_id = new_id("dynwf")
        now = datetime.now(timezone.utc)

        # Inject ephemeral Workflow resource into bundle for WorkflowEngine
        ephemeral_name = ir.name
        bundle = dict(bundle)
        spec = self.planner.ir_to_workflow_spec(ir)
        bundle[f"Workflow:{ephemeral_name}"] = {
            "kind": "Workflow",
            "name": ephemeral_name,
            "spec": {
                "trigger": spec.trigger,
                "steps": [s.model_dump(exclude_none=True) for s in spec.steps],
            },
        }

        await self.sql.execute(
            "INSERT INTO dynamic_workflows "
            "(id, namespace_id, plan_json, ir_json, status, input_json, created_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?)",
            workflow_id,
            namespace_id,
            json.dumps({"goal": input_data.get("goal")}),
            json.dumps(ir.model_dump()),
            json.dumps(input_data),
            now.isoformat(),
        )

        await self.workflow_engine.initialize()
        try:
            state = await self.workflow_engine.run(
                bundle,
                f"workflows/{ephemeral_name}",
                input_data,
                org_id=org_id,
                namespace_id=namespace_id,
                workflow_version_id=workflow_id,
                stream=False,
            )
            status = state.status if hasattr(state, "status") else "completed"
            steps_output = state.steps if hasattr(state, "steps") else {}
            output = state.output if hasattr(state, "output") else {}
        except Exception as e:
            status = "failed"
            steps_output = {}
            output = {"error": str(e)}

        completed = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE dynamic_workflows SET status = ?, output_json = ?, completed_at = ? WHERE id = ?",
            status,
            json.dumps(output),
            completed,
            workflow_id,
        )

        return DynamicWorkflowResult(
            workflow_id=workflow_id,
            ir=ir,
            status=status,
            steps_output=steps_output,
            output=output,
        )

    async def get(self, workflow_id: str) -> dict[str, Any] | None:
        row = await self.sql.fetchone(
            "SELECT * FROM dynamic_workflows WHERE id = ?",
            workflow_id,
        )
        if not row:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "ir": _as_dict(row["ir_json"]),
            "input": _as_dict(row["input_json"]),
            "output": _as_dict(row["output_json"]),
            "createdAt": row["created_at"],
            "completedAt": row["completed_at"],
        }
