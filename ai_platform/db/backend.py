"""Database backend selection — SQLite (dev) or Postgres (SaaS)."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse


def database_url() -> str | None:
    return os.getenv("PLATFORM_DATABASE_URL") or os.getenv("DATABASE_URL")


def is_postgres(url: str | None = None) -> bool:
    u = url or database_url()
    if not u:
        return False
    return u.startswith("postgres://") or u.startswith("postgresql://")


def normalize_postgres_url(url: str) -> str:
    # asyncpg prefers postgresql://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


class PgPool:
    """Thin asyncpg pool wrapper."""

    def __init__(self, dsn: str) -> None:
        self.dsn = normalize_postgres_url(dsn)
        self._pool: Any = None

    async def connect(self) -> Any:
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, sql: str, *args: Any) -> str:
        pool = await self.connect()
        async with pool.acquire() as conn:
            return await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[Any]:
        pool = await self.connect()
        async with pool.acquire() as conn:
            return await conn.fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        pool = await self.connect()
        async with pool.acquire() as conn:
            return await conn.fetchrow(sql, *args)

    async def executemany_script(self, sql: str) -> None:
        """Run multi-statement SQL (migrations)."""
        pool = await self.connect()
        async with pool.acquire() as conn:
            await conn.execute(sql)
