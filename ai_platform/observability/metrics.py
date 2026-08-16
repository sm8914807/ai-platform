"""Model route metrics collection for auto-tuning and ops dashboards."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import ModelRouteMetric
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "004_phase4.sql"


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = min(len(sorted_vals) - 1, max(0, int(round((p / 100.0) * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).lower() in {"1", "true", "t", "yes"}


class MetricsCollector:
    def __init__(
        self, db_path: str | None = None, sql: SqlBackend | None = None
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())

    async def record(
        self,
        route_name: str,
        namespace_id: str,
        provider: str,
        model: str,
        latency_ms: float,
        success: bool,
        cost_units: float = 0.0,
    ) -> None:
        await self.sql.execute(
            "INSERT INTO model_route_metrics "
            "(id, route_name, namespace_id, provider, model, latency_ms, success, cost_units, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            new_id("mrm"),
            route_name,
            namespace_id,
            provider,
            model,
            latency_ms,
            success,
            cost_units,
            datetime.now(timezone.utc).isoformat(),
        )

    async def aggregate(
        self, route_name: str, namespace_id: str, window: int = 100
    ) -> list[ModelRouteMetric]:
        rows = await self.sql.fetchall(
            "SELECT provider, model, latency_ms, success, cost_units FROM model_route_metrics "
            "WHERE route_name = ? AND namespace_id = ? "
            "ORDER BY recorded_at DESC LIMIT ?",
            route_name,
            namespace_id,
            window,
        )
        return [
            ModelRouteMetric(
                route_name=route_name,
                provider=r["provider"],
                model=r["model"],
                latency_ms=float(r["latency_ms"] or 0),
                success=_bool(r["success"]),
                cost_units=float(r["cost_units"] or 0),
            )
            for r in rows
        ]

    async def recent(
        self, namespace_id: str, *, route_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if route_name:
            rows = await self.sql.fetchall(
                "SELECT route_name, provider, model, latency_ms, success, cost_units, recorded_at "
                "FROM model_route_metrics WHERE namespace_id = ? AND route_name = ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                namespace_id,
                route_name,
                limit,
            )
        else:
            rows = await self.sql.fetchall(
                "SELECT route_name, provider, model, latency_ms, success, cost_units, recorded_at "
                "FROM model_route_metrics WHERE namespace_id = ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                namespace_id,
                limit,
            )
        return [
            {
                "routeName": r["route_name"],
                "provider": r["provider"],
                "model": r["model"],
                "latencyMs": float(r["latency_ms"] or 0),
                "success": _bool(r["success"]),
                "costUnits": float(r["cost_units"] or 0),
                "recordedAt": r["recorded_at"],
            }
            for r in rows
        ]

    def _stats_from_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "requests": 0,
                "successes": 0,
                "failures": 0,
                "successRate": 0.0,
                "avgLatencyMs": 0.0,
                "p50LatencyMs": 0.0,
                "p95LatencyMs": 0.0,
                "totalCostUnits": 0.0,
            }
        latencies = sorted(float(r["latency_ms"] or 0) for r in rows)
        successes = sum(1 for r in rows if _bool(r["success"]))
        failures = len(rows) - successes
        total_cost = sum(float(r["cost_units"] or 0) for r in rows)
        return {
            "requests": len(rows),
            "successes": successes,
            "failures": failures,
            "successRate": round(successes / len(rows), 4),
            "avgLatencyMs": round(sum(latencies) / len(latencies), 2),
            "p50LatencyMs": round(_percentile(latencies, 50), 2),
            "p95LatencyMs": round(_percentile(latencies, 95), 2),
            "totalCostUnits": round(total_cost, 6),
        }

    async def summarize_namespace(
        self, namespace_id: str, window: int = 500
    ) -> dict[str, Any]:
        rows = await self.sql.fetchall(
            "SELECT route_name, provider, model, latency_ms, success, cost_units, recorded_at "
            "FROM model_route_metrics WHERE namespace_id = ? "
            "ORDER BY recorded_at DESC LIMIT ?",
            namespace_id,
            window,
        )
        by_route: dict[str, list[dict[str, Any]]] = {}
        by_candidate: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by_route.setdefault(r["route_name"], []).append(r)
            key = f"{r['provider']}:{r['model']}"
            by_candidate.setdefault(key, []).append(r)

        routes = [
            {"routeName": name, **self._stats_from_rows(items)}
            for name, items in sorted(by_route.items())
        ]
        candidates = [
            {
                "provider": key.split(":", 1)[0],
                "model": key.split(":", 1)[1] if ":" in key else key,
                "key": key,
                **self._stats_from_rows(items),
            }
            for key, items in sorted(by_candidate.items())
        ]
        return {
            "namespaceId": namespace_id,
            "window": window,
            "sampleCount": len(rows),
            "overview": self._stats_from_rows(rows),
            "routes": routes,
            "candidates": candidates,
        }

    async def summarize_route(
        self, route_name: str, namespace_id: str, window: int = 200
    ) -> dict[str, Any]:
        rows = await self.sql.fetchall(
            "SELECT provider, model, latency_ms, success, cost_units, recorded_at "
            "FROM model_route_metrics WHERE route_name = ? AND namespace_id = ? "
            "ORDER BY recorded_at DESC LIMIT ?",
            route_name,
            namespace_id,
            window,
        )
        by_candidate: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            key = f"{r['provider']}:{r['model']}"
            by_candidate.setdefault(key, []).append(r)
        return {
            "routeName": route_name,
            "namespaceId": namespace_id,
            "window": window,
            "overview": self._stats_from_rows(rows),
            "candidates": [
                {
                    "provider": key.split(":", 1)[0],
                    "model": key.split(":", 1)[1] if ":" in key else key,
                    "key": key,
                    **self._stats_from_rows(items),
                }
                for key, items in sorted(by_candidate.items())
            ],
            "recent": [
                {
                    "provider": r["provider"],
                    "model": r["model"],
                    "latencyMs": float(r["latency_ms"] or 0),
                    "success": _bool(r["success"]),
                    "costUnits": float(r["cost_units"] or 0),
                    "recordedAt": r["recorded_at"],
                }
                for r in rows[:25]
            ],
        }

    async def prometheus_text(self, namespace_id: str | None = None, window: int = 1000) -> str:
        if namespace_id:
            rows = await self.sql.fetchall(
                "SELECT route_name, provider, model, latency_ms, success, cost_units "
                "FROM model_route_metrics WHERE namespace_id = ? "
                "ORDER BY recorded_at DESC LIMIT ?",
                namespace_id,
                window,
            )
        else:
            rows = await self.sql.fetchall(
                "SELECT route_name, provider, model, latency_ms, success, cost_units "
                "FROM model_route_metrics ORDER BY recorded_at DESC LIMIT ?",
                window,
            )
        buckets: dict[tuple[str, str, str], dict[str, float]] = {}
        for r in rows:
            key = (str(r["route_name"]), str(r["provider"]), str(r["model"]))
            b = buckets.setdefault(key, {"n": 0, "ok": 0, "lat": 0.0, "cost": 0.0})
            b["n"] += 1
            b["ok"] += 1 if _bool(r["success"]) else 0
            b["lat"] += float(r["latency_ms"] or 0)
            b["cost"] += float(r["cost_units"] or 0)

        lines = [
            "# HELP platform_model_route_requests_total Model route request count (rolling window)",
            "# TYPE platform_model_route_requests_total counter",
        ]
        for (route, provider, model), b in sorted(buckets.items()):
            labels = f'route="{route}",provider="{provider}",model="{model}"'
            lines.append(f"platform_model_route_requests_total{{{labels}}} {int(b['n'])}")
        lines.append(
            "# HELP platform_model_route_success_total Successful model route completions"
        )
        lines.append("# TYPE platform_model_route_success_total counter")
        for (route, provider, model), b in sorted(buckets.items()):
            labels = f'route="{route}",provider="{provider}",model="{model}"'
            lines.append(f"platform_model_route_success_total{{{labels}}} {int(b['ok'])}")
        lines.append(
            "# HELP platform_model_route_latency_ms_sum Sum of model route latency in milliseconds"
        )
        lines.append("# TYPE platform_model_route_latency_ms_sum counter")
        for (route, provider, model), b in sorted(buckets.items()):
            labels = f'route="{route}",provider="{provider}",model="{model}"'
            lines.append(f"platform_model_route_latency_ms_sum{{{labels}}} {b['lat']:.3f}")
        lines.append(
            "# HELP platform_model_route_cost_units_sum Approximate cost units for model calls"
        )
        lines.append("# TYPE platform_model_route_cost_units_sum counter")
        for (route, provider, model), b in sorted(buckets.items()):
            labels = f'route="{route}",provider="{provider}",model="{model}"'
            lines.append(f"platform_model_route_cost_units_sum{{{labels}}} {b['cost']:.6f}")
        lines.append("")
        return "\n".join(lines)
