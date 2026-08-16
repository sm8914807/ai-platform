"""Dynamic workflow mode — LLM planner generates IR at runtime, then executes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import ModelRouteSpec, WorkflowSpec, WorkflowStep
from ai_platform.db.sql import SqlBackend, create_sql_backend
from ai_platform.model_router.router import ModelRequest, ModelRouter
from ai_platform.workflow.engine import WorkflowEngine

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "005_differentiators.sql"

PLANNER_SYSTEM = """You are a workflow planner for an enterprise AI platform.
Given a user goal and available agents/tools, output ONLY valid JSON (no markdown)
matching this schema:
{
  "name": "short-kebab-name",
  "description": "one sentence",
  "steps": [
    {
      "id": "step-id",
      "type": "agent" | "tool" | "parallel" | "humanApproval",
      "ref": "agents/name or tools/name or approval-flows/name",
      "when": null or "$.steps.approve.status == approved",
      "description": "what this step does",
      "capabilities": ["optional"],
      "branches": [{"id":"...", "type":"agent", "ref":"agents/..."}]
    }
  ]
}
Rules:
- Prefer available_agents / available_tools refs exactly when possible.
- Use humanApproval when the goal implies approval, refund, onboard, provision, or high risk.
- Use parallel only when independent research/compare slices help; put child steps in branches.
- Keep 1–6 steps. Never invent tool refs that are not listed unless type is humanApproval.
- Output JSON only."""


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object from model output (raw or fenced)."""
    raw = text.strip()
    if not raw:
        return None
    # Strip markdown fences if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


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
    planner_backend: Literal["llm", "heuristic"] | None = Field(
        default=None, alias="plannerBackend"
    )

    model_config = {"populate_by_name": True}


class PlanRequest(BaseModel):
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    available_agents: list[str] = Field(default_factory=list)
    available_tools: list[str] = Field(default_factory=list)
    planner_mode: Literal["auto", "llm", "heuristic"] = Field(
        default="auto", alias="plannerMode"
    )
    model_ref: str | None = Field(default=None, alias="modelRef")

    model_config = {"populate_by_name": True}


class DynamicWorkflowResult(BaseModel):
    workflow_id: str
    ir: DynamicWorkflowIR
    status: str
    steps_output: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)


class HeuristicWorkflowPlanner:
    """Deterministic keyword planner (fallback + tests)."""

    def plan(
        self, request: PlanRequest, discovery_hits: list[str] | None = None
    ) -> DynamicWorkflowIR:
        goal = request.goal.lower()
        steps: list[DynamicStepIR] = []
        agents = request.available_agents or (discovery_hits or ["agents/support-agent"])
        tools = request.available_tools

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
                            {"id": s.id, "type": s.type, "ref": s.ref} for s in steps
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
        elif any(k in goal for k in ("approve", "onboard", "provision", "refund")):
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
            planner_backend="heuristic",
        )


class DynamicWorkflowPlanner:
    """Turns a natural-language goal into a DynamicWorkflowIR.

    Prefers an LLM plan via ModelRouter; falls back to HeuristicWorkflowPlanner.
    """

    def __init__(
        self,
        model_router: ModelRouter | None = None,
        *,
        default_route: ModelRouteSpec | None = None,
        default_mode: Literal["auto", "llm", "heuristic"] = "auto",
    ) -> None:
        self.model_router = model_router
        self.default_route = default_route or ModelRouteSpec(
            candidates=[{"provider": "mock", "model": "mock-planner", "weight": 100}]
        )
        self.default_mode = default_mode
        self.heuristic = HeuristicWorkflowPlanner()

    def plan(
        self, request: PlanRequest, discovery_hits: list[str] | None = None
    ) -> DynamicWorkflowIR:
        """Sync entry used by tests — heuristic only."""
        return self.heuristic.plan(request, discovery_hits)

    async def plan_async(
        self,
        request: PlanRequest,
        discovery_hits: list[str] | None = None,
        *,
        route_spec: ModelRouteSpec | None = None,
        namespace_id: str | None = None,
    ) -> DynamicWorkflowIR:
        mode = request.planner_mode or self.default_mode
        if mode == "heuristic" or (mode == "auto" and self.model_router is None):
            return self.heuristic.plan(request, discovery_hits)
        if mode == "llm" and self.model_router is None:
            return self.heuristic.plan(request, discovery_hits)

        try:
            ir = await self._plan_with_llm(
                request,
                discovery_hits,
                route_spec=route_spec or self.default_route,
                namespace_id=namespace_id,
            )
            if ir is not None:
                return ir
        except Exception:
            pass
        fallback = self.heuristic.plan(request, discovery_hits)
        fallback.planner_backend = "heuristic"
        return fallback

    async def _plan_with_llm(
        self,
        request: PlanRequest,
        discovery_hits: list[str] | None,
        *,
        route_spec: ModelRouteSpec,
        namespace_id: str | None,
    ) -> DynamicWorkflowIR | None:
        assert self.model_router is not None
        agents = request.available_agents or (discovery_hits or ["agents/support-agent"])
        tools = request.available_tools
        user_payload = {
            "goal": request.goal,
            "constraints": request.constraints,
            "available_agents": agents,
            "available_tools": tools,
        }
        response = await self.model_router.complete(
            route_spec,
            ModelRequest(
                messages=[
                    {"role": "system", "content": PLANNER_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            "Plan a workflow for this request as JSON:\n"
                            + json.dumps(user_payload, indent=2)
                        ),
                    },
                ],
                temperature=0.2,
            ),
            route_name=request.model_ref or "models/planner",
            namespace_id=namespace_id,
        )
        # Prefer LLM JSON; mock planner returns structured JSON for offline runs.
        data = _extract_json_object(response.content)
        if not data:
            return None
        return self._normalize_ir(data, request, agents, tools)

    def _normalize_ir(
        self,
        data: dict[str, Any],
        request: PlanRequest,
        agents: list[str],
        tools: list[str],
    ) -> DynamicWorkflowIR:
        allowed_agents = set(agents)
        allowed_tools = set(tools)
        raw_steps = data.get("steps") if isinstance(data.get("steps"), list) else []
        steps: list[DynamicStepIR] = []
        for i, raw in enumerate(raw_steps[:8]):
            if not isinstance(raw, dict):
                continue
            step_type = str(raw.get("type") or "agent")
            if step_type not in {"agent", "tool", "parallel", "humanApproval", "condition"}:
                step_type = "agent"
            ref = raw.get("ref")
            if isinstance(ref, str):
                ref = self._normalize_ref(ref, step_type)
                if step_type == "agent" and allowed_agents and ref not in allowed_agents:
                    # Map unknown agent refs onto the first available agent.
                    ref = agents[0]
                if step_type == "tool" and allowed_tools and ref not in allowed_tools:
                    ref = tools[0] if tools else ref
            elif step_type == "agent":
                ref = agents[0]
            step_id = str(raw.get("id") or f"step-{i+1}")
            branches = raw.get("branches") if isinstance(raw.get("branches"), list) else []
            clean_branches: list[dict[str, Any]] = []
            for b in branches:
                if not isinstance(b, dict):
                    continue
                b_ref = b.get("ref")
                if isinstance(b_ref, str):
                    b_ref = self._normalize_ref(b_ref, str(b.get("type") or "agent"))
                clean_branches.append(
                    {
                        "id": b.get("id") or f"{step_id}-b",
                        "type": b.get("type") or "agent",
                        "ref": b_ref,
                    }
                )
            steps.append(
                DynamicStepIR(
                    id=step_id,
                    type=step_type,  # type: ignore[arg-type]
                    ref=ref,
                    when=raw.get("when"),
                    description=raw.get("description") or request.goal,
                    capabilities=list(raw.get("capabilities") or []),
                    branches=clean_branches,
                )
            )
        if not steps:
            raise ValueError("LLM planner returned no steps")
        name = str(data.get("name") or f"dyn-{new_id('plan')[:8]}")
        name = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-")[:64] or f"dyn-{new_id('plan')[:8]}"
        return DynamicWorkflowIR(
            name=name,
            description=str(data.get("description") or request.goal),
            steps=steps,
            source="planner",
            planner_backend="llm",
        )

    def _normalize_ref(self, ref: str, step_type: str) -> str:
        ref = ref.strip()
        if "/" in ref:
            return ref
        if step_type == "tool":
            return f"tools/{ref}"
        if step_type == "humanApproval":
            return f"approval-flows/{ref}"
        return f"agents/{ref}"

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
        model_router: ModelRouter | None = None,
        planner_mode: Literal["auto", "llm", "heuristic"] = "auto",
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")
        self.agent_engine = agent_engine or AgentEngine()
        self.workflow_engine = workflow_engine or WorkflowEngine(
            agent_engine=self.agent_engine, db_path=self.db_path, sql=self.sql
        )
        router = model_router or getattr(self.agent_engine, "model_router", None)
        self.planner = planner or DynamicWorkflowPlanner(
            model_router=router, default_mode=planner_mode
        )

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())

    def _route_from_bundle(
        self, bundle: dict[str, dict], model_ref: str | None
    ) -> ModelRouteSpec | None:
        ref = model_ref or "models/gpt-4o-routed"
        parts = ref.split("/", 1)
        name = parts[1] if len(parts) == 2 else parts[0]
        doc = bundle.get(f"ModelRoute:{name}")
        if not doc:
            # Any published model route
            for key, value in bundle.items():
                if key.startswith("ModelRoute:"):
                    doc = value
                    break
        if not doc:
            return None
        return ModelRouteSpec.model_validate(doc["spec"])

    async def plan_and_run(
        self,
        namespace_id: str,
        org_id: str,
        request: PlanRequest,
        bundle: dict[str, dict],
        discovery_hits: list[str] | None = None,
    ) -> DynamicWorkflowResult:
        route_spec = self._route_from_bundle(bundle, request.model_ref)
        ir = await self.planner.plan_async(
            request,
            discovery_hits,
            route_spec=route_spec,
            namespace_id=namespace_id,
        )
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
            json.dumps(
                {
                    "goal": input_data.get("goal"),
                    "plannerBackend": ir.planner_backend,
                }
            ),
            json.dumps(ir.model_dump(by_alias=True)),
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
