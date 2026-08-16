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

    async def list_runs(
        self,
        *,
        status: str | None = None,
        namespace_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if namespace_id:
            clauses.append("namespace_id = ?")
            params.append(namespace_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))
        rows = await self.sql.fetchall(
            f"SELECT id, workflow_version_id, org_id, namespace_id, status, "
            f"input_json, output_json, started_at, completed_at, checkpoint_seq "
            f"FROM workflow_runs {where} ORDER BY started_at DESC LIMIT ?",
            *params,
        )
        items: list[dict[str, Any]] = []
        for row in rows:
            state = await self.load_checkpoint(row["id"])
            items.append(
                {
                    "runId": row["id"],
                    "workflowVersionId": row["workflow_version_id"],
                    "orgId": row["org_id"],
                    "namespaceId": row["namespace_id"],
                    "status": row["status"],
                    "input": _as_dict(row["input_json"]),
                    "output": _as_dict(row["output_json"]),
                    "startedAt": row["started_at"],
                    "completedAt": row.get("completed_at"),
                    "checkpointSeq": row["checkpoint_seq"],
                    "workflowRef": state.workflow_ref if state else None,
                    "currentStepId": state.current_step_id if state else None,
                    "steps": state.steps if state else {},
                    "pendingApproval": (
                        state.pending_approval
                        if state and state.pending_approval
                        else None
                    ),
                }
            )
        return items

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
