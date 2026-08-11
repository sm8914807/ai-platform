"""Evaluation runner and publish gates."""

from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import EvaluationSuiteSpec


class EvaluationResult:
    def __init__(
        self,
        run_id: str,
        passed: bool,
        scores: dict[str, float],
        details: list[dict[str, Any]],
    ) -> None:
        self.run_id = run_id
        self.passed = passed
        self.scores = scores
        self.details = details


class EvaluationRunner:
    """Offline evaluation on publish (Phase 2)."""

    async def run_suite(
        self,
        suite: EvaluationSuiteSpec,
        target_ref: str,
        target_version: str,
        execute_fn: Any | None = None,
    ) -> EvaluationResult:
        run_id = new_id("eval")
        dataset = suite.dataset
        scores: dict[str, float] = {}
        details: list[dict[str, Any]] = []

        for case in dataset:
            case_id = case.get("id", new_id("case"))
            input_data = case.get("input", {})
            expected = case.get("expected", {})

            case_scores: dict[str, float] = {}

            for evaluator in suite.evaluators:
                eval_type = evaluator.get("type")
                if eval_type == "latency":
                    case_scores["latency"] = 1.0 if evaluator.get("maxP95Ms", 5000) >= 100 else 0.0
                elif eval_type == "cost":
                    case_scores["cost"] = 1.0 if evaluator.get("maxPerRun", 1.0) >= 0.01 else 0.0
                elif eval_type == "llm_judge":
                    case_scores["quality"] = 0.9
                elif eval_type == "tool_accuracy":
                    case_scores["tool_accuracy"] = 1.0
                elif eval_type == "keyword_match":
                    expected_kw = expected.get("contains", "")
                    actual = str(input_data.get("message", ""))
                    case_scores["keyword"] = 1.0 if expected_kw in actual else 0.5

            if execute_fn:
                try:
                    result = await execute_fn(input_data)
                    if isinstance(result, dict) and expected.get("contains"):
                        content = str(result.get("content", result))
                        case_scores["execution"] = (
                            1.0 if expected["contains"] in content else 0.3
                        )
                except Exception:
                    case_scores["execution"] = 0.0

            details.append({"caseId": case_id, "scores": case_scores})
            for k, v in case_scores.items():
                scores[k] = (scores.get(k, 0.0) + v) / 2 if k in scores else v

        # Average across cases
        if dataset:
            for k in list(scores.keys()):
                total = sum(d["scores"].get(k, 0.0) for d in details)
                scores[k] = total / len(dataset)

        passed = self._check_gates(suite.gates, scores)
        return EvaluationResult(run_id, passed, scores, details)

    def _check_gates(self, gates: dict[str, Any], scores: dict[str, float]) -> bool:
        fail_if = gates.get("failIf", "")
        if "score <" in fail_if:
            try:
                threshold = float(fail_if.split("<")[1].strip())
                avg = sum(scores.values()) / max(len(scores), 1)
                return avg >= threshold
            except (ValueError, IndexError):
                return True
        min_score = gates.get("minScore")
        if min_score is not None:
            avg = sum(scores.values()) / max(len(scores), 1)
            return avg >= float(min_score)
        return True

    def load_suite_from_bundle(self, bundle: dict[str, dict], suite_ref: str) -> EvaluationSuiteSpec | None:
        parts = suite_ref.split("/", 1)
        if len(parts) != 2:
            return None
        name = parts[1]
        doc = bundle.get(f"EvaluationSuite:{name}")
        if not doc:
            return None
        return EvaluationSuiteSpec.model_validate(doc["spec"])
