"""Multi-agent collaboration patterns."""

from __future__ import annotations

import time
from typing import Any

from ai_platform.agent.engine import AgentEngine
from ai_platform.core.models import AgentSpec, CollaborationSpec, ExecutionEvent, MultiAgentResult


ROLE_SETS: dict[str, list[str]] = {
    "planner_executor_reviewer": ["planner", "executor", "reviewer"],
    "hierarchical": ["supervisor", "worker"],
    "supervisor_workers": ["supervisor", "worker"],
    "peer_round_robin": ["peer"],
}


class MultiAgentEngine:
    """Compiled multi-agent profiles — not a LangGraph clone."""

    PATTERNS = set(ROLE_SETS.keys())

    def __init__(self, agent_engine: AgentEngine | None = None) -> None:
        self.agent_engine = agent_engine or AgentEngine()

    async def run(
        self,
        bundle: dict[str, dict],
        root_agent_ref: str,
        input_data: dict[str, Any],
        collaboration: CollaborationSpec | None = None,
        session_id: str | None = None,
        org_id: str = "default",
        namespace_id: str = "local",
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> MultiAgentResult:
        agent_doc = self._resolve(bundle, root_agent_ref)
        if not agent_doc:
            return MultiAgentResult(
                pattern="error",
                iterations=0,
                status="failed",
                errors=[
                    {
                        "code": "agent_not_found",
                        "message": f"Root agent not found: {root_agent_ref}",
                        "diagnosis": "Publish the agent or pick a different root ref.",
                        "ref": root_agent_ref,
                    }
                ],
                final_output={},
            )

        spec = AgentSpec.model_validate(agent_doc["spec"])
        collab = collaboration or self._collab_from_spec(spec, agent_doc, bundle)
        wiring = self._resolved_wiring(collab, root_agent_ref)

        if collab.pattern == "planner_executor_reviewer":
            return await self._planner_executor_reviewer(
                bundle, collab, wiring, input_data, session_id, org_id, namespace_id,
                principal, environment, policy_engine,
            )
        if collab.pattern in ("hierarchical", "supervisor_workers"):
            return await self._supervisor_workers(
                bundle, collab, wiring, root_agent_ref, input_data, session_id, org_id, namespace_id,
                principal, environment, policy_engine,
            )
        if collab.pattern == "peer_round_robin":
            return await self._peer_round_robin(
                bundle, collab, wiring, input_data, session_id, org_id, namespace_id,
                principal, environment, policy_engine,
            )

        result = await self.agent_engine.execute(
            bundle,
            root_agent_ref,
            input_data,
            stream=False,
            session_id=session_id,
            org_id=org_id,
            namespace_id=namespace_id,
            principal=principal,
            environment=environment,
            policy_engine=policy_engine,
        )
        content = result.data if isinstance(result, ExecutionEvent) else {}
        status = "failed" if isinstance(result, ExecutionEvent) and result.type == "error" else "completed"
        errors = []
        if status == "failed":
            errors.append(
                {
                    "code": "single_agent_error",
                    "message": str(content.get("message", content)),
                    "diagnosis": "Single-agent fallback failed; check model route and prompt refs.",
                    "ref": root_agent_ref,
                }
            )
        return MultiAgentResult(
            pattern="single",
            iterations=1,
            steps=[
                {
                    "role": "executor",
                    "ref": root_agent_ref,
                    "status": "ok" if status == "completed" else "error",
                    "output": content,
                }
            ],
            final_output=content if isinstance(content, dict) else {"content": content},
            status=status,  # type: ignore[arg-type]
            errors=errors,
            wiring=wiring,
        )

    def _collab_from_spec(
        self, spec: AgentSpec, agent_doc: dict[str, Any], bundle: dict[str, dict]
    ) -> CollaborationSpec:
        raw = agent_doc.get("spec", {}).get("collaboration")
        if raw:
            return CollaborationSpec.model_validate(raw)
        for doc in bundle.values():
            if doc.get("kind") == "Agent" and doc.get("spec", {}).get("collaboration"):
                return CollaborationSpec.model_validate(doc["spec"]["collaboration"])
        if spec.supervisor_ref:
            return CollaborationSpec(
                pattern="supervisor_workers",
                agents={"supervisor": spec.supervisor_ref},
            )
        return CollaborationSpec(pattern="planner_executor_reviewer")

    def _resolved_wiring(self, collab: CollaborationSpec, root_ref: str) -> dict[str, str]:
        wiring = dict(collab.agents)
        defaults = {
            "planner": "agents/planner-agent",
            "executor": "agents/executor-agent",
            "reviewer": "agents/reviewer-agent",
            "supervisor": root_ref,
            "worker": root_ref,
            "peer": "agents/peer-agent",
        }
        for role in ROLE_SETS.get(collab.pattern, []):
            if role not in wiring:
                if role == "worker":
                    workers = [v for k, v in wiring.items() if k.startswith("worker")]
                    if not workers:
                        wiring["worker"] = defaults["worker"]
                else:
                    wiring[role] = defaults.get(role, root_ref)
        return wiring

    async def _run_agent(
        self,
        bundle: dict[str, dict],
        ref: str,
        input_data: dict[str, Any],
        session_id: str | None,
        role: str,
        org_id: str,
        namespace_id: str,
        turn: int,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        if not self._resolve(bundle, ref):
            return {
                "turn": turn,
                "role": role,
                "ref": ref,
                "status": "missing",
                "latencyMs": 0.0,
                "output": {},
                "error": f"Agent not in published bundle: {ref}",
                "diagnosis": (
                    f"Role '{role}' points to {ref}, which is not published in this namespace. "
                    "Wire a published agent or publish that resource."
                ),
            }
        try:
            result = await self.agent_engine.execute(
                bundle,
                ref,
                input_data,
                stream=False,
                session_id=session_id,
                org_id=org_id,
                namespace_id=namespace_id,
                principal=principal,
                environment=environment,
                policy_engine=policy_engine,
            )
            latency = (time.perf_counter() - started) * 1000
            if isinstance(result, ExecutionEvent) and result.type == "error":
                message = str(result.data.get("message", result.data))
                return {
                    "turn": turn,
                    "role": role,
                    "ref": ref,
                    "status": "error",
                    "latencyMs": round(latency, 2),
                    "output": result.data if isinstance(result.data, dict) else {"data": result.data},
                    "error": message,
                    "diagnosis": self._diagnose_agent_error(message, result.data),
                }
            if isinstance(result, ExecutionEvent) and result.type == "approval_required":
                return {
                    "turn": turn,
                    "role": role,
                    "ref": ref,
                    "status": "paused",
                    "latencyMs": round(latency, 2),
                    "output": result.data if isinstance(result.data, dict) else {},
                    "error": "approval_required",
                    "diagnosis": (
                        "Tool/governor paused this role for approval. "
                        "Use HITL inbox or rate-limit override to continue."
                    ),
                }
            output = result.data if isinstance(result, ExecutionEvent) else {}
            return {
                "turn": turn,
                "role": role,
                "ref": ref,
                "status": "ok",
                "latencyMs": round(latency, 2),
                "output": output if isinstance(output, dict) else {"content": output},
                "preview": self._preview(output),
            }
        except Exception as exc:  # noqa: BLE001 — surface to Studio timeline
            latency = (time.perf_counter() - started) * 1000
            return {
                "turn": turn,
                "role": role,
                "ref": ref,
                "status": "error",
                "latencyMs": round(latency, 2),
                "output": {},
                "error": str(exc),
                "diagnosis": self._diagnose_agent_error(str(exc), {}),
            }

    def _preview(self, output: Any, limit: int = 160) -> str:
        if isinstance(output, dict):
            text = str(output.get("content", output))
        else:
            text = str(output)
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def _diagnose_agent_error(self, message: str, data: Any) -> str:
        lower = message.lower()
        if "policy denied" in lower or (isinstance(data, dict) and data.get("reason")):
            reason = data.get("reason") if isinstance(data, dict) else None
            return (
                f"Policy blocked this agent ({reason or message}). "
                "Check published Policy rules for agent:run / tool:invoke."
            )
        if "guardrail" in lower or "injection" in lower:
            return "A guardrail blocked input/output. Inspect agent guardrails refs and config.action."
        if "not found" in lower:
            return "Missing prompt, model route, or tool in the published bundle."
        if "rate_limit" in lower or "quota" in lower:
            return "Governor quota exceeded. Approve in HITL or raise the tool rate limit."
        return "Inspect model route, prompt template, and tool bindings for this role."

    def _collect_errors(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        for step in steps:
            if step.get("status") in {"error", "missing", "paused"}:
                errors.append(
                    {
                        "code": step.get("status"),
                        "message": step.get("error") or step.get("status"),
                        "diagnosis": step.get("diagnosis"),
                        "role": step.get("role"),
                        "ref": step.get("ref"),
                        "turn": step.get("turn"),
                    }
                )
        return errors

    def _finalize(
        self,
        pattern: str,
        iterations: int,
        steps: list[dict[str, Any]],
        final_output: dict[str, Any],
        wiring: dict[str, str],
        *,
        stop_status: str | None = None,
    ) -> MultiAgentResult:
        errors = self._collect_errors(steps)
        if stop_status:
            status = stop_status
        elif any(s.get("status") == "missing" for s in steps):
            status = "failed"
        elif any(s.get("status") == "error" for s in steps):
            status = "failed"
        elif any(s.get("status") == "paused" for s in steps):
            status = "partial"
        else:
            status = "completed"
        return MultiAgentResult(
            pattern=pattern,
            iterations=iterations,
            steps=steps,
            final_output=final_output,
            status=status,  # type: ignore[arg-type]
            errors=errors,
            wiring=wiring,
        )

    async def _planner_executor_reviewer(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        wiring: dict[str, str],
        input_data: dict[str, Any],
        session_id: str | None,
        org_id: str,
        namespace_id: str,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        shared = dict(input_data)
        planner_ref = wiring.get("planner", "agents/planner-agent")
        executor_ref = wiring.get("executor", "agents/executor-agent")
        reviewer_ref = wiring.get("reviewer", "agents/reviewer-agent")
        turn = 0

        for i in range(collab.max_iterations):
            turn += 1
            plan = await self._run_agent(
                bundle, planner_ref, shared, session_id, "planner", org_id, namespace_id, turn,
                principal, environment, policy_engine,
            )
            steps.append(plan)
            if plan["status"] != "ok":
                return self._finalize(
                    collab.pattern, i + 1, steps, {}, wiring, stop_status="failed"
                )
            shared["plan"] = plan["output"].get("content", str(plan["output"]))

            turn += 1
            exec_result = await self._run_agent(
                bundle, executor_ref, shared, session_id, "executor", org_id, namespace_id, turn,
                principal, environment, policy_engine,
            )
            steps.append(exec_result)
            if exec_result["status"] != "ok":
                return self._finalize(
                    collab.pattern, i + 1, steps, {}, wiring, stop_status="failed"
                )
            shared["draft"] = exec_result["output"].get(
                "content", str(exec_result["output"])
            )

            turn += 1
            review = await self._run_agent(
                bundle, reviewer_ref, shared, session_id, "reviewer", org_id, namespace_id, turn,
                principal, environment, policy_engine,
            )
            steps.append(review)
            if review["status"] != "ok":
                return self._finalize(
                    collab.pattern, i + 1, steps, exec_result["output"], wiring, stop_status="failed"
                )
            approved = "approve" in str(review["output"]).lower() or review["output"].get(
                "approved", False
            )
            if approved:
                return self._finalize(
                    collab.pattern, i + 1, steps, exec_result["output"], wiring
                )

        return self._finalize(
            collab.pattern,
            collab.max_iterations,
            steps,
            steps[-1]["output"] if steps else {},
            wiring,
            stop_status="partial",
        )

    async def _supervisor_workers(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        wiring: dict[str, str],
        root_ref: str,
        input_data: dict[str, Any],
        session_id: str | None,
        org_id: str,
        namespace_id: str,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        supervisor_ref = wiring.get("supervisor", root_ref)
        worker_refs = [
            v for k, v in collab.agents.items() if k.startswith("worker") or k == "worker"
        ]
        if not worker_refs:
            worker_refs = [wiring.get("worker", root_ref)]

        turn = 1
        route = await self._run_agent(
            bundle, supervisor_ref, input_data, session_id, "supervisor", org_id, namespace_id, turn,
            principal, environment, policy_engine,
        )
        steps.append(route)
        if route["status"] != "ok":
            return self._finalize(collab.pattern, 1, steps, {}, wiring, stop_status="failed")
        route_text = str(route["output"].get("content", route["output"]))

        for idx, worker_ref in enumerate(worker_refs):
            turn += 1
            worker_input = {**input_data, "task": route_text, "workerIndex": idx}
            worker = await self._run_agent(
                bundle,
                worker_ref,
                worker_input,
                session_id,
                f"worker-{idx}",
                org_id,
                namespace_id,
                turn,
                principal,
                environment,
                policy_engine,
            )
            steps.append(worker)
            if worker["status"] != "ok":
                return self._finalize(
                    collab.pattern, 1, steps, {}, wiring, stop_status="failed"
                )

        return self._finalize(
            collab.pattern,
            1,
            steps,
            steps[-1]["output"] if steps else {},
            wiring,
        )

    async def _peer_round_robin(
        self,
        bundle: dict[str, dict],
        collab: CollaborationSpec,
        wiring: dict[str, str],
        input_data: dict[str, Any],
        session_id: str | None,
        org_id: str,
        namespace_id: str,
        principal: str = "anonymous",
        environment: str = "development",
        policy_engine: Any | None = None,
    ) -> MultiAgentResult:
        steps: list[dict[str, Any]] = []
        peers = [v for k, v in collab.agents.items()] or [wiring.get("peer", "agents/peer-agent")]
        shared = dict(input_data)

        for i in range(collab.max_iterations):
            ref = peers[i % len(peers)]
            step = await self._run_agent(
                bundle, ref, shared, session_id, f"peer-{i}", org_id, namespace_id, i + 1,
                principal, environment, policy_engine,
            )
            steps.append(step)
            if step["status"] != "ok":
                return self._finalize(
                    collab.pattern, i + 1, steps, {}, wiring, stop_status="failed"
                )
            shared["previous"] = step["output"].get("content", str(step["output"]))

        return self._finalize(
            collab.pattern,
            collab.max_iterations,
            steps,
            steps[-1]["output"] if steps else {},
            wiring,
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
