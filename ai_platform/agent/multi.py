"""Multi-agent collaboration patterns."""

from typing import Any

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.ids import new_id
from ai_platform.core.models import AgentSpec, CollaborationSpec, ExecutionEvent, MultiAgentResult


class MultiAgentEngine:
    """Compiled multi-agent profiles — not a LangGraph clone."""

    PATTERNS = {
        "planner_executor_reviewer",
        "hierarchical",
        "supervisor_workers",
        "peer_round_robin",
    }

    def __init__(self, agent_engine: AgentEngine | None = None) -> None:
        self.agent_engine = agent_engine or AgentEngine()

    async def run(
        self,
        bundle: dict[str, dict],
        root_agent_ref: str,
        input_data: dict[str, Any],
        collaboration: CollaborationSpec | None = None,
        session_id: str | None = None,
    ) -> MultiAgentResult:
        agent_doc = self._resolve(bundle, root_agent_ref)
        if not agent_doc:
            raise ValueError(f"Agent not found: {root_agent_ref}")

        spec = AgentSpec.model_validate(agent_doc["spec"])
        collab = collaboration or self._collab_from_spec(spec, bundle)

        if collab.pattern == "planner_executor_reviewer":
            return await self._planner_executor_reviewer(
                bundle, collab, input_data, session_id
            )
        if collab.pattern in ("hierarchical", "supervisor_workers"):
            return await self._supervisor_workers(
                bundle, collab, root_agent_ref, input_data, session_id
            )
        if collab.pattern == "peer_round_robin":
            return await self._peer_round_robin(bundle, collab, input_data, session_id)

        # Single agent fallback
        result = await self.agent_engine.execute(
            bundle, root_agent_ref, input_data, stream=False, session_id=session_id
        )
        content = result.data if isinstance(result, ExecutionEvent) else {}
        return MultiAgentResult(
            pattern="single",
            iterations=1,
            steps=[{"role": "executor", "output": content}],
            final_output=content,
        )

    def _collab_from_spec(
        self, spec: AgentSpec, bundle: dict[str, dict]
    ) -> CollaborationSpec:
        for doc in bundle.values():
            if doc.get("kind") == "Agent" and doc.get("spec", {}).get("collaboration"):
                return CollaborationSpec.model_validate(doc["spec"]["collaboration"])
        if spec.supervisor_ref:
            return CollaborationSpec(
                pattern="supervisor_workers",
                agents={"supervisor": spec.supervisor_ref},
            )
        return CollaborationSpec(pattern="planner_executor_reviewer")

    async def _run_agent(
        self,
        bundle: dict[str, dict],
        ref: str,
        input_data: dict[str, Any],
        session_id: str | None,
        role: str,
    ) -> dict[str, Any]:
        result = await self.agent_engine.execute(
            bundle, ref, input_data, stream=False, session_id=session_id
        )
        output = result.data if isinstance(result, ExecutionEvent) else {}
        return {"role": role, "ref": ref, "output": output}

    async def _planner_executor_reviewer(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        input_data: dict[str, Any],
        session_id: str | None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        shared = dict(input_data)

        planner_ref = collab.agents.get("planner", "agents/planner-agent")
        executor_ref = collab.agents.get("executor", "agents/executor-agent")
        reviewer_ref = collab.agents.get("reviewer", "agents/reviewer-agent")

        for i in range(collab.max_iterations):
            plan = await self._run_agent(bundle, planner_ref, shared, session_id, "planner")
            steps.append(plan)
            shared["plan"] = plan["output"].get("content", str(plan["output"]))

            exec_result = await self._run_agent(bundle, executor_ref, shared, session_id, "executor")
            steps.append(exec_result)
            shared["draft"] = exec_result["output"].get("content", str(exec_result["output"]))

            review = await self._run_agent(bundle, reviewer_ref, shared, session_id, "reviewer")
            steps.append(review)
            approved = "approve" in str(review["output"]).lower() or review["output"].get("approved", False)
            if approved:
                return MultiAgentResult(
                    pattern=collab.pattern,
                    iterations=i + 1,
                    steps=steps,
                    final_output=exec_result["output"],
                )

        return MultiAgentResult(
            pattern=collab.pattern,
            iterations=collab.max_iterations,
            steps=steps,
            final_output=steps[-1]["output"] if steps else {},
        )

    async def _supervisor_workers(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        root_ref: str,
        input_data: dict[str, Any],
        session_id: str | None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        supervisor_ref = collab.agents.get("supervisor", root_ref)
        worker_refs = [
            v for k, v in collab.agents.items() if k.startswith("worker") or k == "worker"
        ]
        if not worker_refs:
            worker_refs = [root_ref]

        route = await self._run_agent(bundle, supervisor_ref, input_data, session_id, "supervisor")
        steps.append(route)
        route_text = str(route["output"].get("content", route["output"]))

        for idx, worker_ref in enumerate(worker_refs):
            worker_input = {**input_data, "task": route_text, "workerIndex": idx}
            worker = await self._run_agent(bundle, worker_ref, worker_input, session_id, f"worker-{idx}")
            steps.append(worker)

        return MultiAgentResult(
            pattern=collab.pattern,
            iterations=1,
            steps=steps,
            final_output=steps[-1]["output"] if steps else {},
        )

    async def _peer_round_robin(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        input_data: dict[str, Any],
        session_id: str | None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        peers = list(collab.agents.values()) or ["agents/peer-agent"]
        shared = dict(input_data)

        for i in range(collab.max_iterations):
            ref = peers[i % len(peers)]
            step = await self._run_agent(bundle, ref, shared, session_id, f"peer-{i}")
            steps.append(step)
            shared["previous"] = step["output"].get("content", str(step["output"]))

        return MultiAgentResult(
            pattern=collab.pattern,
            iterations=collab.max_iterations,
            steps=steps,
            final_output=steps[-1]["output"] if steps else {},
        )

    def _resolve(self, bundle: dict[str, dict], ref: str) -> dict[str, Any] | None:
        parts = ref.split("/", 1)
        if len(parts) != 2:
            return None
        plural, name = parts
        kind_map = {"agents": "Agent"}
        kind = kind_map.get(plural)
        if not kind:
            return None
        return bundle.get(f"{kind}:{name}")
