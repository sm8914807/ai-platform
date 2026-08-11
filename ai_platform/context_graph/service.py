"""Context Graph — organizational decision memory (SQLite or Postgres)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ai_platform.core.ids import new_id
from ai_platform.db.sql import SqlBackend, create_sql_backend

MIGRATION = Path(__file__).parent.parent.parent / "migrations" / "005_differentiators.sql"


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else {}
    return dict(value)


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else []
    return list(value)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class TraceEntity(BaseModel):
    type: str
    id: str


class DecisionTrace(BaseModel):
    id: str
    namespace_id: str
    workflow_id: str | None = None
    agent_ref: str
    trace_type: Literal["decision", "observation", "handoff", "approval"] = "decision"
    entities: list[TraceEntity] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "workflow", "domain", "public"] = "workflow"
    payload: dict[str, Any] = Field(default_factory=dict)
    outcome: str | None = None
    created_at: datetime


class TraceLink(BaseModel):
    id: str
    from_trace_id: str
    to_trace_id: str
    link_type: Literal[
        "based_on_precedent", "supersedes", "led_to", "approved_by", "related"
    ]
    created_at: datetime


class CreateTraceRequest(BaseModel):
    agent_ref: str
    workflow_id: str | None = None
    trace_type: Literal["decision", "observation", "handoff", "approval"] = "decision"
    entities: list[TraceEntity] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "workflow", "domain", "public"] = "workflow"
    payload: dict[str, Any] = Field(default_factory=dict)
    outcome: str | None = None


class PrecedentQuery(BaseModel):
    tags: list[str] = Field(default_factory=list)
    entities: list[TraceEntity] = Field(default_factory=list)
    agent_ref: str | None = None
    workflow_id: str | None = None
    limit: int = 10


class ContextGraphService:
    def __init__(
        self, db_path: str | None = None, sql: SqlBackend | None = None
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def migrate(self) -> None:
        if self.sql.kind == "sqlite" and MIGRATION.exists():
            await self.sql.migrate_script(MIGRATION.read_text())

    async def create_trace(
        self, namespace_id: str, req: CreateTraceRequest
    ) -> DecisionTrace:
        trace_id = new_id("trace")
        now = datetime.now(timezone.utc)
        await self.sql.execute(
            "INSERT INTO decision_traces "
            "(id, namespace_id, workflow_id, agent_ref, trace_type, entities_json, "
            "tags_json, visibility, payload_json, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            trace_id,
            namespace_id,
            req.workflow_id,
            req.agent_ref,
            req.trace_type,
            json.dumps([e.model_dump() for e in req.entities]),
            json.dumps(req.tags),
            req.visibility,
            json.dumps(req.payload),
            req.outcome,
            now.isoformat(),
        )
        return DecisionTrace(
            id=trace_id,
            namespace_id=namespace_id,
            workflow_id=req.workflow_id,
            agent_ref=req.agent_ref,
            trace_type=req.trace_type,
            entities=req.entities,
            tags=req.tags,
            visibility=req.visibility,
            payload=req.payload,
            outcome=req.outcome,
            created_at=now,
        )

    async def get_trace(self, trace_id: str) -> DecisionTrace | None:
        row = await self.sql.fetchone(
            "SELECT * FROM decision_traces WHERE id = ?", trace_id
        )
        return self._row_to_trace(row) if row else None

    async def link_traces(
        self,
        from_trace_id: str,
        to_trace_id: str,
        link_type: str = "based_on_precedent",
    ) -> TraceLink:
        link_id = new_id("tlink")
        now = datetime.now(timezone.utc)
        existing = await self.sql.fetchone(
            "SELECT id FROM decision_trace_links WHERE from_trace_id = ? AND to_trace_id = ? "
            "AND link_type = ?",
            from_trace_id,
            to_trace_id,
            link_type,
        )
        if not existing:
            await self.sql.execute(
                "INSERT INTO decision_trace_links "
                "(id, from_trace_id, to_trace_id, link_type, created_at) VALUES (?, ?, ?, ?, ?)",
                link_id,
                from_trace_id,
                to_trace_id,
                link_type,
                now.isoformat(),
            )
        else:
            link_id = existing["id"]
        return TraceLink(
            id=link_id,
            from_trace_id=from_trace_id,
            to_trace_id=to_trace_id,
            link_type=link_type,  # type: ignore[arg-type]
            created_at=now,
        )

    async def query_precedents(
        self, namespace_id: str, query: PrecedentQuery
    ) -> list[DecisionTrace]:
        rows = await self.sql.fetchall(
            "SELECT * FROM decision_traces WHERE namespace_id = ? ORDER BY created_at DESC LIMIT 200",
            namespace_id,
        )
        results: list[DecisionTrace] = []
        for row in rows:
            trace = self._row_to_trace(row)
            if query.agent_ref and trace.agent_ref != query.agent_ref:
                continue
            if query.workflow_id and trace.workflow_id != query.workflow_id:
                continue
            if query.tags and not set(query.tags).intersection(set(trace.tags)):
                continue
            if query.entities:
                entity_keys = {(e.type, e.id) for e in trace.entities}
                wanted = {(e.type, e.id) for e in query.entities}
                if not wanted.intersection(entity_keys):
                    continue
            results.append(trace)
            if len(results) >= query.limit:
                break
        return results

    async def get_linked(
        self, trace_id: str, direction: Literal["from", "to", "both"] = "both"
    ) -> list[dict[str, Any]]:
        links: list[dict[str, Any]] = []
        if direction in ("from", "both"):
            links.extend(
                await self.sql.fetchall(
                    "SELECT * FROM decision_trace_links WHERE from_trace_id = ?",
                    trace_id,
                )
            )
        if direction in ("to", "both"):
            links.extend(
                await self.sql.fetchall(
                    "SELECT * FROM decision_trace_links WHERE to_trace_id = ?",
                    trace_id,
                )
            )
        return links

    async def list_traces(
        self, namespace_id: str, limit: int = 50
    ) -> list[DecisionTrace]:
        rows = await self.sql.fetchall(
            "SELECT * FROM decision_traces WHERE namespace_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            namespace_id,
            limit,
        )
        return [self._row_to_trace(r) for r in rows]

    def _row_to_trace(self, row: dict[str, Any]) -> DecisionTrace:
        return DecisionTrace(
            id=row["id"],
            namespace_id=row["namespace_id"],
            workflow_id=row["workflow_id"],
            agent_ref=row["agent_ref"],
            trace_type=row["trace_type"],
            entities=[TraceEntity(**e) for e in _as_list(row["entities_json"])],
            tags=_as_list(row["tags_json"]),
            visibility=row["visibility"],
            payload=_as_dict(row["payload_json"]),
            outcome=row["outcome"],
            created_at=_parse_dt(row["created_at"]) or datetime.now(timezone.utc),
        )
