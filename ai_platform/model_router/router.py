"""Model routing with fallback."""

from dataclasses import dataclass
from typing import Any

from ai_platform.core.models import ModelRouteSpec


@dataclass
class ModelRequest:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    temperature: float = 0.7
    max_tokens: int | None = None


@dataclass
class ModelResponse:
    content: str
    provider: str
    model: str
    usage: dict[str, int]
    latency_ms: float


class ModelProvider:
    """Base model provider adapter."""

    name: str

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError


class MockModelProvider(ModelProvider):
    """Deterministic mock for Phase 1 tests and local dev."""

    name = "mock"

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        blob = " ".join(str(m.get("content", "")) for m in request.messages)
        lower = blob.lower()
        # Produce a valid DynamicWorkflowIR so the LLM planner path is exercisable offline.
        if "workflow planner" in lower or "plan a workflow" in lower:
            agents = ["agents/support-agent"]
            tools: list[str] = []
            try:
                import json as _json

                goal = "goal"
                agents = ["agents/support-agent"]
                tools: list[str] = []
                user_msg = ""
                for m in reversed(request.messages):
                    if m.get("role") == "user":
                        user_msg = str(m.get("content") or "")
                        break
                start = user_msg.find("{")
                end = user_msg.rfind("}")
                if start >= 0 and end > start:
                    payload = _json.loads(user_msg[start : end + 1])
                    if isinstance(payload, dict):
                        goal = str(payload.get("goal") or "goal")
                        if isinstance(payload.get("available_agents"), list) and payload["available_agents"]:
                            agents = [str(a) for a in payload["available_agents"]]
                        if isinstance(payload.get("available_tools"), list):
                            tools = [str(t) for t in payload["available_tools"]]
            except Exception:
                goal = "goal"
                agents = ["agents/support-agent"]
                tools = []
            goal_l = goal.lower()
            if any(k in goal_l for k in ("research", "analyze", "compare", "market")):
                plan = {
                    "name": "llm-research-plan",
                    "description": goal,
                    "steps": [
                        {
                            "id": "parallel-research",
                            "type": "parallel",
                            "description": "Parallel research",
                            "branches": [
                                {"id": f"research-{i}", "type": "agent", "ref": a}
                                for i, a in enumerate(agents[:3])
                            ],
                        },
                        {
                            "id": "synthesize",
                            "type": "agent",
                            "ref": agents[0],
                            "description": "Synthesize findings",
                        },
                    ],
                }
            elif any(k in goal_l for k in ("approve", "onboard", "provision", "refund")):
                plan = {
                    "name": "llm-approval-plan",
                    "description": goal,
                    "steps": [
                        {"id": "enrich", "type": "agent", "ref": agents[0]},
                        {
                            "id": "approve",
                            "type": "humanApproval",
                            "ref": "approval-flows/manager-signoff",
                        },
                        {
                            "id": "complete",
                            "type": "agent",
                            "ref": agents[0],
                            "when": "$.steps.approve.status == approved",
                        },
                    ],
                }
            else:
                steps = [
                    {
                        "id": "execute",
                        "type": "agent",
                        "ref": agents[0],
                        "description": goal,
                    }
                ]
                if tools:
                    steps.append(
                        {"id": "tool-step", "type": "tool", "ref": tools[0], "description": "Tool follow-up"}
                    )
                plan = {"name": "llm-simple-plan", "description": goal, "steps": steps}
            import json as _json

            return ModelResponse(
                content=_json.dumps(plan),
                provider=self.name,
                model=model,
                usage={"prompt_tokens": 40, "completion_tokens": 80, "total_tokens": 120},
                latency_ms=8.0,
            )

        # Evaluation LLM-judge path: return structured scores offline.
        if "evaluation judge" in lower or "score the assistant output" in lower:
            import json as _json

            criteria = "quality"
            expected_contains = ""
            output_text = ""
            user_msg = ""
            for m in reversed(request.messages):
                if m.get("role") == "user":
                    user_msg = str(m.get("content") or "")
                    break
            try:
                start = user_msg.find("{")
                end = user_msg.rfind("}")
                if start >= 0 and end > start:
                    payload = _json.loads(user_msg[start : end + 1])
                    if isinstance(payload, dict):
                        criteria = str(payload.get("criteria") or criteria)
                        expected = payload.get("expected") or {}
                        if isinstance(expected, dict):
                            expected_contains = str(expected.get("contains") or "")
                        output_text = str(payload.get("output") or "")
            except Exception:
                pass
            score = 0.85
            rationale = f"Mock judge ({criteria}): acceptable"
            out_l = output_text.lower()
            exp_l = expected_contains.lower()
            if exp_l and exp_l in out_l:
                score = 0.95
                rationale = f"Output contains expected '{expected_contains}'"
            elif exp_l and exp_l not in out_l:
                score = 0.25
                rationale = f"Missing expected '{expected_contains}'"
            if any(w in out_l for w in ("unsafe", "hack", "exploit")):
                score = min(score, 0.1)
                rationale = "Unsafe content detected"
            if criteria in {"faithfulness", "relevance", "safety"} and score >= 0.5:
                score = max(score, 0.8)
            import json as _json2

            return ModelResponse(
                content=_json2.dumps({"score": score, "rationale": rationale}),
                provider=self.name,
                model=model,
                usage={"prompt_tokens": 30, "completion_tokens": 40, "total_tokens": 70},
                latency_ms=6.0,
            )

        last = request.messages[-1].get("content", "") if request.messages else ""
        return ModelResponse(
            content=f"[mock:{model}] processed: {last}",
            provider=self.name,
            model=model,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            latency_ms=5.0,
        )


class ModelRouter:
    """Routes requests across providers per ModelRoute spec."""

    def __init__(
        self,
        providers: dict[str, ModelProvider] | None = None,
        metrics_collector: Any | None = None,
        route_name: str | None = None,
        namespace_id: str | None = None,
    ) -> None:
        self._providers = providers or {"mock": MockModelProvider()}
        self._cache: dict[str, ModelResponse] = {}
        self._metrics = metrics_collector
        self._route_name = route_name
        self._namespace_id = namespace_id

    async def complete(
        self,
        route_spec: ModelRouteSpec,
        request: ModelRequest,
        *,
        route_name: str | None = None,
        namespace_id: str | None = None,
    ) -> ModelResponse:
        candidates = sorted(
            route_spec.candidates,
            key=lambda c: (not c.fallback, -c.weight),
        )
        cache_key = None
        if route_spec.caching.get("enabled"):
            cache_key = str(hash((tuple(request.messages), route_spec.strategy)))
            if cache_key in self._cache:
                return self._cache[cache_key]

        metric_route = route_name or self._route_name
        metric_ns = namespace_id or self._namespace_id
        last_error: Exception | None = None
        for candidate in candidates:
            provider = self._providers.get(candidate.provider)
            if not provider:
                continue
            try:
                response = await provider.complete(candidate.model, request)
                if cache_key:
                    self._cache[cache_key] = response
                if self._metrics and metric_route and metric_ns:
                    await self._metrics.record(
                        metric_route,
                        metric_ns,
                        response.provider,
                        response.model,
                        response.latency_ms,
                        True,
                        response.usage.get("total_tokens", 0) * 0.0001,
                    )
                return response
            except Exception as e:
                last_error = e
                if self._metrics and metric_route and metric_ns:
                    await self._metrics.record(
                        metric_route,
                        metric_ns,
                        candidate.provider,
                        candidate.model,
                        0.0,
                        False,
                    )
                if not candidate.fallback:
                    continue
        if last_error:
            raise last_error
        raise RuntimeError("No model candidates available")
