"""Model route metrics collection for auto-tuning (SQLite or Postgres)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ai_platform.core.ids import new_id
from ai_platform.core.models import ModelRouteMetric
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "004_phase4.sql"


class MetricsCollector:
    def __init__(
        self, db_path: str | None = None, sql: SqlBackend | None = None
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        # no-op or sqlite-only script; centralized migrate_aux_stores handles full migrate
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
                latency_ms=r["latency_ms"],
                success=bool(r["success"]),
                cost_units=r["cost_units"],
            )
            for r in rows
        ]
