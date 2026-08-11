"""AI SRE — auto-tune ModelRoute weights from observability feedback."""

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ai_platform.core.ids import new_id
from ai_platform.core.models import ModelCandidate, ModelRouteSpec, RouteTuningResult
from ai_platform.observability.metrics import MetricsCollector


class RouteTuner:
    """Adjust candidate weights based on latency, success rate, and cost."""

    def __init__(self, metrics: MetricsCollector, db_path: str) -> None:
        self.metrics = metrics
        self.db_path = db_path

    async def tune(
        self,
        route_name: str,
        namespace_id: str,
        route_spec: ModelRouteSpec,
        window: int = 100,
    ) -> RouteTuningResult:
        metrics = await self.metrics.aggregate(route_name, namespace_id, window)
        old_weights = {
            f"{c.provider}:{c.model}": c.weight for c in route_spec.candidates
        }

        if not metrics:
            return RouteTuningResult(
                route_name=route_name,
                old_weights=old_weights,
                new_weights=old_weights,
                reason="no metrics available",
                metrics_window=0,
            )

        stats: dict[str, dict[str, float]] = {}
        for m in metrics:
            key = f"{m.provider}:{m.model}"
            s = stats.setdefault(key, {"latency": 0.0, "success": 0.0, "cost": 0.0, "n": 0})
            s["latency"] += m.latency_ms
            s["success"] += 1.0 if m.success else 0.0
            s["cost"] += m.cost_units
            s["n"] += 1

        scores: dict[str, float] = {}
        for key, s in stats.items():
            n = max(s["n"], 1)
            avg_latency = s["latency"] / n
            success_rate = s["success"] / n
            avg_cost = s["cost"] / n
            # Higher score = better (low latency, high success, low cost)
            scores[key] = success_rate * 100 - avg_latency * 0.1 - avg_cost * 10

        if route_spec.strategy == "latencyOptimized":
            for key, s in stats.items():
                n = max(s["n"], 1)
                scores[key] = -s["latency"] / n

        if route_spec.strategy == "costOptimized":
            for key, s in stats.items():
                n = max(s["n"], 1)
                scores[key] = -s["cost"] / n

        if not scores:
            return RouteTuningResult(
                route_name=route_name,
                old_weights=old_weights,
                new_weights=old_weights,
                reason="no scores computed",
                metrics_window=len(metrics),
            )

        min_score = min(scores.values())
        shifted = {k: v - min_score + 1 for k, v in scores.items()}
        total = sum(shifted.values())
        new_weights_raw = {k: max(1, int(round(v / total * 100))) for k, v in shifted.items()}

        # Normalize to 100
        w_total = sum(new_weights_raw.values())
        new_weights = {k: int(round(v / w_total * 100)) for k, v in new_weights_raw.items()}

        run_id = new_id("tune")
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT INTO route_tuning_runs "
            "(id, route_name, namespace_id, old_weights_json, new_weights_json, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                route_name,
                namespace_id,
                json.dumps(old_weights),
                json.dumps(new_weights),
                f"auto-tune from {len(metrics)} samples",
                now,
            ),
        )
        await conn.commit()
        await conn.close()

        return RouteTuningResult(
            route_name=route_name,
            old_weights=old_weights,
            new_weights=new_weights,
            reason=f"auto-tune from {len(metrics)} samples",
            metrics_window=len(metrics),
        )

    def apply_weights(self, route_spec: ModelRouteSpec, new_weights: dict[str, int]) -> ModelRouteSpec:
        updated: list[ModelCandidate] = []
        for c in route_spec.candidates:
            key = f"{c.provider}:{c.model}"
            weight = new_weights.get(key, c.weight)
            updated.append(
                ModelCandidate(
                    provider=c.provider,
                    model=c.model,
                    weight=weight,
                    fallback=c.fallback,
                    max_latency_ms=c.max_latency_ms,
                )
            )
        return ModelRouteSpec(
            strategy=route_spec.strategy,
            candidates=updated,
            constraints=route_spec.constraints,
            caching=route_spec.caching,
        )

    async def tune_and_apply_resource(
        self,
        route_name: str,
        namespace_id: str,
        namespace_path: str,
        route_spec: ModelRouteSpec,
        registry: Any,
    ) -> RouteTuningResult:
        result = await self.tune(route_name, namespace_id, route_spec)
        if result.old_weights == result.new_weights:
            return result

        new_spec = self.apply_weights(route_spec, result.new_weights)
        from ai_platform.core.models import PlatformResource, ResourceKind, ResourceMetadata

        resource = PlatformResource(
            kind=ResourceKind.MODEL_ROUTE,
            metadata=ResourceMetadata(
                name=route_name.replace("models/", ""),
                namespace=namespace_path,
                version="1.0.1",
            ),
            spec={
                "strategy": new_spec.strategy,
                "candidates": [
                    {
                        "provider": c.provider,
                        "model": c.model,
                        "weight": c.weight,
                        "fallback": c.fallback,
                    }
                    for c in new_spec.candidates
                ],
                "constraints": new_spec.constraints,
                "caching": new_spec.caching,
            },
        )
        await registry.upsert_resource_version(
            namespace_id, resource, "route-tuner", "auto-tune weights"
        )
        return result
