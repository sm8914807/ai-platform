"""Identity store for SSO and SCIM (SQLite or Postgres via SqlBackend)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ai_platform.core.ids import new_id
from ai_platform.core.models import IdentityUser, ScimUserPayload
from ai_platform.db.sql import SqlBackend, create_sql_backend


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value) if value else []
    return list(value)


def _row_user(r: dict[str, Any]) -> IdentityUser:
    return IdentityUser(
        id=r["id"],
        org_id=r["org_id"],
        email=r["email"],
        display_name=r.get("display_name"),
        external_id=r.get("external_id"),
        teams=_as_list(r.get("teams_json")),
        active=bool(r.get("active", True)),
    )


class IdentityStore:
    """Durable user/team store shared by SSO and SCIM."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        sql: SqlBackend | None = None,
    ) -> None:
        self.sql = sql or create_sql_backend(db_path=db_path or ".platform/registry.db")
        self.db_path = db_path or getattr(self.sql, "db_path", ".platform/registry.db")

    async def create_user(
        self,
        org_id: str,
        email: str,
        display_name: str | None = None,
        external_id: str | None = None,
        teams: list[str] | None = None,
    ) -> IdentityUser:
        user_id = new_id("user")
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "INSERT INTO identity_users "
            "(id, org_id, email, display_name, external_id, teams_json, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            user_id,
            org_id,
            email,
            display_name,
            external_id,
            json.dumps(teams or []),
            True,
            now,
            now,
        )
        return IdentityUser(
            id=user_id,
            org_id=org_id,
            email=email,
            display_name=display_name,
            external_id=external_id,
            teams=teams or [],
        )

    async def get_user_by_email(self, org_id: str, email: str) -> IdentityUser | None:
        row = await self.sql.fetchone(
            "SELECT * FROM identity_users WHERE org_id = ? AND email = ?",
            org_id,
            email,
        )
        return _row_user(row) if row else None

    async def list_users(self, org_id: str) -> list[IdentityUser]:
        rows = await self.sql.fetchall(
            "SELECT * FROM identity_users WHERE org_id = ?",
            org_id,
        )
        return [_row_user(r) for r in rows]

    async def deactivate_user(self, user_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self.sql.execute(
            "UPDATE identity_users SET active = ?, updated_at = ? WHERE id = ?",
            False,
            now,
            user_id,
        )

    async def create_team(self, org_id: str, name: str) -> str:
        team_id = f"team:{name}"
        now = datetime.now(timezone.utc).isoformat()
        if self.sql.kind == "postgres":
            await self.sql.execute(
                "INSERT INTO identity_teams (id, org_id, name, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                team_id,
                org_id,
                name,
                now,
            )
        else:
            await self.sql.execute(
                "INSERT OR IGNORE INTO identity_teams (id, org_id, name, created_at) VALUES (?, ?, ?, ?)",
                team_id,
                org_id,
                name,
                now,
            )
        return team_id


class ScimService:
    def __init__(self, store: IdentityStore) -> None:
        self.store = store

    async def create_user(self, org_id: str, payload: ScimUserPayload) -> dict[str, Any]:
        email = payload.userName
        if payload.emails:
            email = payload.emails[0].get("value", email)
        display = payload.name.get("formatted") or payload.name.get("givenName", email)
        user = await self.store.create_user(
            org_id, email, display, payload.externalId
        )
        return self._to_scim(user)

    async def get_user(self, org_id: str, user_id: str) -> dict[str, Any] | None:
        users = await self.store.list_users(org_id)
        for u in users:
            if u.id == user_id:
                return self._to_scim(u)
        return None

    async def list_users(self, org_id: str) -> dict[str, Any]:
        users = await self.store.list_users(org_id)
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(users),
            "Resources": [self._to_scim(u) for u in users],
        }

    async def delete_user(self, user_id: str) -> None:
        await self.store.deactivate_user(user_id)

    def _to_scim(self, user: IdentityUser) -> dict[str, Any]:
        return {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": user.id,
            "userName": user.email,
            "name": {"formatted": user.display_name or user.email},
            "emails": [{"value": user.email, "primary": True}],
            "active": user.active,
            "externalId": user.external_id,
        }
