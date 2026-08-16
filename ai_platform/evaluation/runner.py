"""Evaluation runner and publish gates with real judges."""

from __future__ import annotations

import re
import time
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import EvaluationSuiteSpec
from ai_platform.evaluation.judges import build_judge_registry, normalize_output
from ai_platform.model_router.router import ModelRouter

_CMP_RE = re.compile(
    r"^(?P<metric>[a-zA-Z_][\w]*)\s*(?P<op><=|>=|<|>|==)\s*(?P<value>-?\d+(?:\.\d+)?)$"
)


class EvaluationResult:
    def __init__(
        self,
        run_id: str,
        passed: bool,
        scores: dict[str, float],
        details: list[dict[str, Any]],
        *,
        overall: float | None = None,
        gate_reason: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.passed = passed
        self.scores = scores
        self.details = details
        self.overall = overall if overall is not None else (
            sum(scores.values()) / max(len(scores), 1) if scores else 0.0
        )
        self.gate_reason = gate_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "runId": self.run_id,
            "passed": self.passed,
            "scores": self.scores,
            "overall": self.overall,
            "gateReason": self.gate_reason,
            "details": self.details,
        }


class EvaluationRunner:
    """Runs EvaluationSuite datasets through typed judges for publish gates."""

    def __init__(self, model_router: ModelRouter | None = None) -> None:
        self.model_router = model_router or ModelRouter()
        self._judges = build_judge_registry(self.model_router)
        self._history: list[dict[str, Any]] = []

    def register_judge(self, eval_type: str, judge: Any) -> None:
        self._judges[eval_type] = judge

    async def run_suite(
        self,
        suite: EvaluationSuiteSpec,
        target_ref: str,
        target_version: str,
        execute_fn: Any | None = None,
    ) -> EvaluationResult:
        run_id = new_id("eval")
        dataset = suite.dataset
        details: list[dict[str, Any]] = []
        score_totals: dict[str, float] = {}
        score_counts: dict[str, int] = {}

        for case in dataset:
            case_id = str(case.get("id") or new_id("case"))
            input_data = case.get("input") or {}
            if not isinstance(input_data, dict):
                input_data = {"message": str(input_data)}

            output: dict[str, Any] | None = None
            exec_error: str | None = None
            if execute_fn:
                started = time.perf_counter()
                try:
                    raw = await execute_fn(input_data)
                    output = normalize_output(raw)
                    if output["latency_ms"] <= 0:
                        output["latency_ms"] = (time.perf_counter() - started) * 1000
                except Exception as exc:  # noqa: BLE001 — case isolation
                    exec_error = str(exc)
                    output = {
                        "content": "",
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "cost": 0.0,
                        "tools_used": [],
                        "error": exec_error,
                    }

            case_scores: dict[str, float] = {}
            case_judge_details: dict[str, Any] = {}
            for evaluator in suite.evaluators:
                eval_type = str(evaluator.get("type") or "")
                judge = self._judges.get(eval_type)
                if not judge:
                    continue
                metric_name = str(
                    evaluator.get("metric")
                    or getattr(judge, "name", eval_type)
                    or eval_type
                )
                # Faithfulness / relevance / safety share LlmJudge but keep distinct metrics.
                if eval_type in {"faithfulness", "relevance", "safety"} and "metric" not in evaluator:
                    metric_name = eval_type
                    evaluator = {**evaluator, "criteria": eval_type, "metric": eval_type}

                score, detail = await judge.score(
                    case=case, output=output, evaluator=evaluator
                )
                if exec_error and eval_type in {"llm_judge", "faithfulness", "relevance", "keyword_match", "exact_match", "tool_accuracy"}:
                    score = min(score, 0.0)
                    detail = {**detail, "executionError": exec_error}
                case_scores[metric_name] = score
                case_judge_details[metric_name] = detail
                score_totals[metric_name] = score_totals.get(metric_name, 0.0) + score
                score_counts[metric_name] = score_counts.get(metric_name, 0) + 1

            if execute_fn and "execution" not in case_scores:
                expected = case.get("expected") or {}
                if expected.get("contains") and output:
                    content = str(output.get("content") or "")
                    case_scores["execution"] = (
                        1.0 if str(expected["contains"]).lower() in content.lower() else 0.3
                    )
                    score_totals["execution"] = score_totals.get("execution", 0.0) + case_scores["execution"]
                    score_counts["execution"] = score_counts.get("execution", 0) + 1
                elif exec_error:
                    case_scores["execution"] = 0.0
                    score_totals["execution"] = score_totals.get("execution", 0.0) + 0.0
                    score_counts["execution"] = score_counts.get("execution", 0) + 1

            details.append(
                {
                    "caseId": case_id,
                    "scores": case_scores,
                    "judges": case_judge_details,
                    "outputPreview": (output or {}).get("content", "")[:300] if output else None,
                    "error": exec_error,
                }
            )

        scores: dict[str, float] = {}
        for metric, total in score_totals.items():
            scores[metric] = total / max(score_counts.get(metric, 1), 1)

        overall = sum(scores.values()) / max(len(scores), 1) if scores else 0.0
        passed, reason = self._check_gates(suite.gates, scores, overall)
        result = EvaluationResult(
            run_id, passed, scores, details, overall=overall, gate_reason=reason
        )
        self._history.append(
            {
                **result.to_dict(),
                "targetRef": target_ref,
                "targetVersion": target_version,
            }
        )
        if len(self._history) > 100:
            self._history = self._history[-100:]
        return result

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return list(reversed(self._history[-limit:]))

    def _check_gates(
        self,
        gates: dict[str, Any],
        scores: dict[str, float],
        overall: float,
    ) -> tuple[bool, str | None]:
        if not gates:
            return True, None

        metrics_cfg = gates.get("metrics") or gates.get("thresholds") or {}
        if isinstance(metrics_cfg, dict):
            for metric, threshold in metrics_cfg.items():
                try:
                    need = float(threshold)
                except (TypeError, ValueError):
                    continue
                got = scores.get(str(metric))
                if got is None:
                    return False, f"missing_metric:{metric}"
                if got < need:
                    return False, f"{metric} < {need} (got {got:.3f})"

        fail_if = str(gates.get("failIf") or "").strip()
        if fail_if:
            expr = fail_if
            # Alias: bare "score" means overall average.
            if expr.lower().startswith("score "):
                expr = "overall " + expr[6:]
            m = _CMP_RE.match(expr.replace("score", "overall"))
            if m:
                metric = m.group("metric")
                op = m.group("op")
                value = float(m.group("value"))
                left = overall if metric == "overall" else scores.get(metric)
                if left is None:
                    return False, f"missing_metric:{metric}"
                failed = {
                    "<": left < value,
                    "<=": left <= value,
                    ">": left > value,
                    ">=": left >= value,
                    "==": left == value,
                }.get(op, False)
                # failIf means "fail when expression is true"
                if failed:
                    return False, f"failIf: {fail_if} (got {left:.3f})"
            elif "score <" in fail_if:
                try:
                    threshold = float(fail_if.split("<", 1)[1].strip())
                    if overall < threshold:
                        return False, f"failIf: {fail_if} (got {overall:.3f})"
                except ValueError:
                    pass

        min_score = gates.get("minScore")
        if min_score is not None:
            need = float(min_score)
            if overall < need:
                return False, f"minScore {need} (got {overall:.3f})"

        return True, None

    def load_suite_from_bundle(
        self, bundle: dict[str, dict], suite_ref: str
    ) -> EvaluationSuiteSpec | None:
        parts = suite_ref.split("/", 1)
        if len(parts) != 2:
            return None
        kind_or_name, name = parts[0], parts[1]
        if kind_or_name.lower() in {"evaluationsuites", "evaluationsuite", "evals"}:
            doc = bundle.get(f"EvaluationSuite:{name}")
        else:
            doc = bundle.get(f"EvaluationSuite:{name}") or bundle.get(f"EvaluationSuite:{suite_ref}")
        if not doc:
            # Allow bare name: evaluationsuites/foo OR just foo via evaluationsuites/foo
            doc = bundle.get(f"EvaluationSuite:{parts[-1]}")
        if not doc:
            return None
        return EvaluationSuiteSpec.model_validate(doc["spec"])

    def find_suites_for_target(
        self, bundle: dict[str, dict], target_ref: str
    ) -> list[tuple[str, EvaluationSuiteSpec]]:
        """Return suites whose triggers.onPublish includes target_ref."""
        found: list[tuple[str, EvaluationSuiteSpec]] = []
        for key, doc in bundle.items():
            if not key.startswith("EvaluationSuite:"):
                continue
            name = key.split(":", 1)[1]
            try:
                suite = EvaluationSuiteSpec.model_validate(doc.get("spec") or {})
            except Exception:
                continue
            for trigger in suite.triggers:
                on_pub = trigger.get("onPublish") or trigger.get("on_publish") or []
                if isinstance(on_pub, str):
                    on_pub = [on_pub]
                if target_ref in on_pub or "*" in on_pub:
                    found.append((f"evaluationsuites/{name}", suite))
                    break
        return found
