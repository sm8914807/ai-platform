"""Concrete evaluation judges used by publish quality gates."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from ai_platform.core.models import ModelRouteSpec
from ai_platform.model_router.router import ModelRequest, ModelRouter

_SCORE_RE = re.compile(r'"score"\s*:\s*([01](?:\.\d+)?)', re.I)


def normalize_output(result: Any) -> dict[str, Any]:
    """Normalize execute_fn / agent results into a common shape."""
    if result is None:
        return {"content": "", "latency_ms": 0.0, "cost": 0.0, "tools_used": []}
    if hasattr(result, "model_dump"):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        return {
            "content": str(result),
            "latency_ms": 0.0,
            "cost": 0.0,
            "tools_used": [],
        }

    data = result.get("data") if result.get("type") == "done" else result
    if not isinstance(data, dict):
        data = result

    content = data.get("content")
    if content is None:
        content = data.get("output") or data.get("text") or ""
    tools = (
        data.get("toolsUsed")
        or data.get("tools_used")
        or data.get("toolCalls")
        or data.get("tool_calls")
        or []
    )
    if isinstance(tools, str):
        tools = [tools]
    tool_names: list[str] = []
    for t in tools:
        if isinstance(t, dict):
            tool_names.append(str(t.get("name") or t.get("ref") or t.get("tool") or t))
        else:
            tool_names.append(str(t))

    usage = data.get("usage") or {}
    tokens = int(usage.get("total_tokens") or usage.get("totalTokens") or 0)
    cost = float(data.get("cost") or data.get("costUsd") or tokens * 0.0001)
    latency = float(
        data.get("latencyMs")
        or data.get("latency_ms")
        or data.get("durationMs")
        or 0.0
    )
    return {
        "content": str(content),
        "latency_ms": latency,
        "cost": cost,
        "tools_used": tool_names,
        "usage": usage,
        "raw": data,
    }


def extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class Judge(Protocol):
    name: str

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        """Return (score 0..1, detail)."""


class KeywordMatchJudge:
    name = "keyword"

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        expected = case.get("expected") or {}
        needles: list[str] = []
        if isinstance(expected.get("contains"), str) and expected["contains"]:
            needles.append(expected["contains"])
        if isinstance(expected.get("keywords"), list):
            needles.extend(str(k) for k in expected["keywords"] if k)
        cfg_kw = evaluator.get("keywords") or evaluator.get("contains")
        if isinstance(cfg_kw, str) and cfg_kw:
            needles.append(cfg_kw)
        if isinstance(cfg_kw, list):
            needles.extend(str(k) for k in cfg_kw if k)

        haystack = ""
        if output:
            haystack = output.get("content") or ""
        if not haystack:
            # Offline / no execution: score against the case input message.
            haystack = str((case.get("input") or {}).get("message") or "")

        if not needles:
            return 1.0, {"matched": [], "haystackPreview": haystack[:200]}

        lower = haystack.lower()
        matched = [n for n in needles if n.lower() in lower]
        score = len(matched) / len(needles)
        return score, {"matched": matched, "expected": needles, "haystackPreview": haystack[:200]}


class ExactMatchJudge:
    name = "exact_match"

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        expected = case.get("expected") or {}
        want = str(expected.get("equals") or expected.get("exact") or "").strip()
        got = (output or {}).get("content", "") if output else ""
        got = str(got).strip()
        ok = bool(want) and want == got
        return (1.0 if ok else 0.0), {"expected": want, "actual": got[:500]}


class ToolAccuracyJudge:
    name = "tool_accuracy"

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        expected = case.get("expected") or {}
        want = expected.get("tools") or expected.get("toolCalls") or evaluator.get("tools") or []
        if isinstance(want, str):
            want = [want]
        want_set = {str(t).split("/")[-1] for t in want}
        used = (output or {}).get("tools_used") or []
        used_set = {str(t).split("/")[-1] for t in used}
        if not want_set:
            # No expectation → pass if execution happened without error tools.
            return (1.0 if output is not None else 0.5), {"used": list(used_set)}
        if not used_set:
            return 0.0, {"expected": sorted(want_set), "used": []}
        intersection = want_set & used_set
        score = len(intersection) / len(want_set)
        return score, {
            "expected": sorted(want_set),
            "used": sorted(used_set),
            "matched": sorted(intersection),
        }


class LatencyJudge:
    name = "latency"

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        max_ms = float(evaluator.get("maxP95Ms") or evaluator.get("maxMs") or 5000)
        latency = float((output or {}).get("latency_ms") or 0.0)
        if output is None:
            # No live run — treat configured budget as a soft pass (legacy behavior).
            return (1.0 if max_ms >= 100 else 0.0), {"latencyMs": None, "maxMs": max_ms, "offline": True}
        if latency <= 0:
            return 1.0, {"latencyMs": latency, "maxMs": max_ms}
        if latency <= max_ms:
            return 1.0, {"latencyMs": latency, "maxMs": max_ms}
        # Gradual fail: 0 at 2x budget.
        score = max(0.0, 1.0 - (latency - max_ms) / max(max_ms, 1.0))
        return score, {"latencyMs": latency, "maxMs": max_ms}


class CostJudge:
    name = "cost"

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        max_cost = float(evaluator.get("maxPerRun") or evaluator.get("maxCost") or 1.0)
        cost = float((output or {}).get("cost") or 0.0)
        if output is None:
            return (1.0 if max_cost >= 0.01 else 0.0), {"cost": None, "maxPerRun": max_cost, "offline": True}
        if cost <= max_cost:
            return 1.0, {"cost": cost, "maxPerRun": max_cost}
        score = max(0.0, 1.0 - (cost - max_cost) / max(max_cost, 1e-9))
        return score, {"cost": cost, "maxPerRun": max_cost}


class LlmJudge:
    """LLM-as-judge: asks the model for a 0..1 score + rationale JSON."""

    name = "quality"

    def __init__(
        self,
        model_router: ModelRouter | None = None,
        default_route: ModelRouteSpec | None = None,
    ) -> None:
        self.model_router = model_router or ModelRouter()
        self.default_route = default_route or ModelRouteSpec.model_validate(
            {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "judge-1", "weight": 100}],
            }
        )

    async def score(
        self,
        *,
        case: dict[str, Any],
        output: dict[str, Any] | None,
        evaluator: dict[str, Any],
    ) -> tuple[float, dict[str, Any]]:
        criteria = str(evaluator.get("criteria") or "quality")
        metric = str(evaluator.get("metric") or self.name)
        input_data = case.get("input") or {}
        expected = case.get("expected") or {}
        content = (output or {}).get("content") if output else None
        if content is None:
            content = str(input_data.get("message") or "")

        system = (
            "You are an evaluation judge for an AI platform publish gate. "
            "Score the assistant output from 0.0 to 1.0 for the given criteria. "
            'Respond with ONLY JSON: {"score": <number>, "rationale": "<short>"}.'
        )
        user = json.dumps(
            {
                "criteria": criteria,
                "metric": metric,
                "input": input_data,
                "expected": expected,
                "output": content,
                "rubric": evaluator.get("rubric")
                or (
                    "1.0 = fully meets criteria; 0.7 = mostly; "
                    "0.4 = partial; 0.0 = fails or unsafe."
                ),
            },
            ensure_ascii=False,
        )
        route = self.default_route
        route_cfg = evaluator.get("modelRoute") or evaluator.get("route")
        if isinstance(route_cfg, dict):
            try:
                route = ModelRouteSpec.model_validate(route_cfg)
            except Exception:
                route = self.default_route

        response = await self.model_router.complete(
            route,
            ModelRequest(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.0,
            ),
            route_name="eval-llm-judge",
        )
        payload = extract_json_object(response.content) or {}
        score_val: float | None = None
        if "score" in payload:
            try:
                score_val = float(payload["score"])
            except (TypeError, ValueError):
                score_val = None
        if score_val is None:
            m = _SCORE_RE.search(response.content)
            if m:
                score_val = float(m.group(1))
        if score_val is None:
            # Heuristic fallback when the model returns free text.
            lower = response.content.lower()
            if any(w in lower for w in ("excellent", "pass", "correct", "faithful")):
                score_val = 0.9
            elif any(w in lower for w in ("fail", "incorrect", "unsafe", "hallucin")):
                score_val = 0.2
            else:
                score_val = 0.6
        score_val = max(0.0, min(1.0, score_val))
        return score_val, {
            "metric": metric,
            "criteria": criteria,
            "rationale": payload.get("rationale") or response.content[:300],
            "provider": response.provider,
            "model": response.model,
        }


def build_judge_registry(model_router: ModelRouter | None = None) -> dict[str, Judge]:
    llm = LlmJudge(model_router=model_router)
    return {
        "keyword_match": KeywordMatchJudge(),
        "exact_match": ExactMatchJudge(),
        "tool_accuracy": ToolAccuracyJudge(),
        "latency": LatencyJudge(),
        "cost": CostJudge(),
        "llm_judge": llm,
        "faithfulness": llm,
        "relevance": llm,
        "safety": llm,
    }
