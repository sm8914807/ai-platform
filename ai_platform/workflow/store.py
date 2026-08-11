"""Workflow state persistence (SQLite or Postgres)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import WorkflowRunState
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION_002 = Path(__file__).parent.parent.parent / "migrations" / "002_phase2.sql"


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


class WorkflowStateStore:
    def __init__(
        self, db_path: str | None = None, sql: SqlBackend | None = None
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/workflows.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/workflows.db")

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite" and MIGRATION_002.exists():
            await self.sql.migrate_script(MIGRATION_002.read_text())

    async def create_run(
        self,
        workflow_version_id: str,
        org_id: str,
        namespace_id: str,
        input_data: dict[str, Any],
        workflow_ref: str,
    ) -> str:
        run_id = new_id("wfr")
        now = datetime.now(timezone.utc).isoformat()
        state = WorkflowRunState(
            run_id=run_id,
            workflow_ref=workflow_ref,
            status="running",
            input=input_data,
        )
        await self.sql.execute(
            "INSERT INTO workflow_runs (id, workflow_version_id, org_id, namespace_id, "
            "status, input_json, output_json, started_at, checkpoint_seq) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            run_id,
            workflow_version_id,
            org_id,
            namespace_id,
            state.status,
            json.dumps(input_data),
            "{}",
            now,
            0,
        )
        await self.sql.execute(
            "INSERT INTO workflow_checkpoints (id, workflow_run_id, seq, state_blob_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            new_id("wcp"),
            run_id,
            0,
            json.dumps(state.model_dump()),
            now,
        )
        return run_id

    async def save_checkpoint(self, state: WorkflowRunState) -> None:
        state.checkpoint_seq += 1
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE workflow_runs SET status = ?, output_json = ?, checkpoint_seq = ?, "
            "completed_at = ? WHERE id = ?",
            state.status,
            json.dumps(state.output),
            state.checkpoint_seq,
            now if state.status in ("completed", "failed") else None,
            state.run_id,
        )
        await self.sql.execute(
            "INSERT INTO workflow_checkpoints (id, workflow_run_id, seq, state_blob_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            new_id("wcp"),
            state.run_id,
            state.checkpoint_seq,
            json.dumps(state.model_dump()),
            now,
        )

    async def load_checkpoint(self, run_id: str) -> WorkflowRunState | None:
        row = await self.sql.fetchone(
            "SELECT state_blob_json FROM workflow_checkpoints "
            "WHERE workflow_run_id = ? ORDER BY seq DESC LIMIT 1",
            run_id,
        )
        if not row:
            return None
        return WorkflowRunState.model_validate(_as_dict(row["state_blob_json"]))

    async def record_step(
        self,
        run_id: str,
        step_id: str,
        status: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        error: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "INSERT INTO workflow_step_runs "
            "(id, workflow_run_id, step_id, status, attempt, input_json, output_json, "
            "started_at, completed_at, error_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            new_id("wfs"),
            run_id,
            step_id,
            status,
            attempt,
            json.dumps(input_data),
            json.dumps(output_data),
            now,
            now,
            json.dumps(error) if error else None,
        )
