"""Unified async SQL backend — SQLite (dev) or Postgres (SaaS).

Stores write SQL with ``?`` placeholders; Postgres rewrites them to ``$1..$n``.
JSON columns are stored as TEXT on SQLite and JSONB on Postgres (pass dict/list
or JSON strings — backend normalizes).
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ai_platform.db.backend import PgPool, database_url, is_postgres, normalize_postgres_url


def _rewrite_placeholders(sql: str) -> str:
    """Convert ``?`` placeholders to asyncpg ``$1, $2, ...``."""
    n = 0

    def repl(_: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return f"${n}"

    return re.sub(r"\?", repl, sql)


def _normalize_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    out: list[Any] = []
    for a in args:
        if isinstance(a, (dict, list)):
            out.append(json.dumps(a))
        else:
            out.append(a)
    return tuple(out)


class SqlBackend(ABC):
    kind: str

    @abstractmethod
    async def migrate_script(self, sql: str) -> None: ...

    @abstractmethod
    async def execute(self, sql: str, *args: Any) -> None: ...

    @abstractmethod
    async def fetchall(self, sql: str, *args: Any) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def fetchone(self, sql: str, *args: Any) -> dict[str, Any] | None: ...

    @abstractmethod
    async def close(self) -> None: ...


class SqliteBackend(SqlBackend):
    kind = "sqlite"

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def migrate_script(self, sql: str) -> None:
        import aiosqlite

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path, timeout=30.0)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=30000")
        await conn.executescript(sql)
        await conn.commit()
        await conn.close()

    async def execute(self, sql: str, *args: Any) -> None:
        import aiosqlite

        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(sql, _normalize_args(args))
        await conn.commit()
        await conn.close()

    async def fetchall(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        import aiosqlite

        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(sql, _normalize_args(args))
        await conn.close()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, *args: Any) -> dict[str, Any] | None:
        rows = await self.fetchall(sql, *args)
        return rows[0] if rows else None

    async def close(self) -> None:
        return None


class PostgresBackend(SqlBackend):
    kind = "postgres"

    def __init__(self, dsn: str) -> None:
        self.pool = PgPool(dsn)

    async def migrate_script(self, sql: str) -> None:
        await self.pool.executemany_script(sql)

    async def execute(self, sql: str, *args: Any) -> None:
        await self.pool.execute(_rewrite_placeholders(sql), *_normalize_args(args))

    async def fetchall(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(_rewrite_placeholders(sql), *_normalize_args(args))
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            for k, v in list(d.items()):
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat() if v.tzinfo else str(v)
            out.append(d)
        return out

    async def fetchone(self, sql: str, *args: Any) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(_rewrite_placeholders(sql), *_normalize_args(args))
        if not row:
            return None
        d = dict(row)
        for k, v in list(d.items()):
            if hasattr(v, "isoformat"):
                d[k] = v.isoformat() if getattr(v, "tzinfo", None) else str(v)
        return d

    async def close(self) -> None:
        await self.pool.close()


def create_sql_backend(
    db_path: str | None = None,
    database_url_override: str | None = None,
) -> SqlBackend:
    """Prefer Postgres DSN when set; otherwise SQLite at db_path."""
    dsn = database_url_override or database_url()
    if is_postgres(dsn):
        assert dsn is not None
        return PostgresBackend(normalize_postgres_url(dsn))
    path = db_path or ".platform/registry.db"
    return SqliteBackend(path)


ROOT = Path(__file__).parent.parent.parent
PG_AUX = ROOT / "migrations" / "postgres" / "002_aux.sql"
SQLITE_MIGRATIONS = [
    ROOT / "migrations" / "002_phase2.sql",
    ROOT / "migrations" / "003_phase3.sql",
    ROOT / "migrations" / "004_phase4.sql",
    ROOT / "migrations" / "005_differentiators.sql",
    ROOT / "migrations" / "006_messaging.sql",
    ROOT / "migrations" / "007_edge_telemetry.sql",
]

SECRETS_DDL = """
CREATE TABLE IF NOT EXISTS secrets (
  id TEXT PRIMARY KEY,
  namespace_id TEXT NOT NULL,
  name TEXT NOT NULL,
  ciphertext TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  rotated_at TEXT,
  UNIQUE(namespace_id, name)
);
"""

AMTP_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS amtp_schemas (
  id TEXT PRIMARY KEY,
  schema_id TEXT NOT NULL UNIQUE,
  version TEXT NOT NULL DEFAULT '1.0',
  definition_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS amtp_delivery_status (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL,
  recipient TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 0,
  error_json TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE(message_id, recipient)
);
CREATE TABLE IF NOT EXISTS amtp_agents (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  address TEXT NOT NULL,
  api_key_hash TEXT,
  delivery_mode TEXT NOT NULL DEFAULT 'pull',
  push_target TEXT,
  supported_schemas_json TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  UNIQUE(domain, address)
);
"""


async def migrate_aux_stores(backend: SqlBackend) -> None:
    """Apply aux-table migrations for the active backend."""
    if backend.kind == "postgres":
        await backend.migrate_script(PG_AUX.read_text())
    else:
        for path in SQLITE_MIGRATIONS:
            if path.exists():
                await backend.migrate_script(path.read_text())
        await backend.migrate_script(SECRETS_DDL)
        await backend.migrate_script(AMTP_DDL_SQLITE)
