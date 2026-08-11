"""Identity store for SSO and SCIM."""

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from ai_platform.core.ids import new_id
from ai_platform.core.models import IdentityUser, ScimUserPayload


class IdentityStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

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
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT INTO identity_users "
            "(id, org_id, email, display_name, external_id, teams_json, active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                user_id,
                org_id,
                email,
                display_name,
                external_id,
                json.dumps(teams or []),
                now,
                now,
            ),
        )
        await conn.commit()
        await conn.close()
        return IdentityUser(
            id=user_id,
            org_id=org_id,
            email=email,
            display_name=display_name,
            external_id=external_id,
            teams=teams or [],
        )

    async def get_user_by_email(self, org_id: str, email: str) -> IdentityUser | None:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM identity_users WHERE org_id = ? AND email = ?", (org_id, email)
        )
        await conn.close()
        if not rows:
            return None
        r = rows[0]
        return IdentityUser(
            id=r["id"],
            org_id=r["org_id"],
            email=r["email"],
            display_name=r["display_name"],
            external_id=r["external_id"],
            teams=json.loads(r["teams_json"]),
            active=bool(r["active"]),
        )

    async def list_users(self, org_id: str) -> list[IdentityUser]:
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM identity_users WHERE org_id = ?", (org_id,)
        )
        await conn.close()
        users = []
        for r in rows:
            users.append(
                IdentityUser(
                    id=r["id"],
                    org_id=r["org_id"],
                    email=r["email"],
                    display_name=r["display_name"],
                    external_id=r["external_id"],
                    teams=json.loads(r["teams_json"]),
                    active=bool(r["active"]),
                )
            )
        return users

    async def deactivate_user(self, user_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "UPDATE identity_users SET active = 0, updated_at = ? WHERE id = ?", (now, user_id)
        )
        await conn.commit()
        await conn.close()

    async def create_team(self, org_id: str, name: str) -> str:
        team_id = f"team:{name}"
        now = datetime.now(timezone.utc).isoformat()
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute(
            "INSERT OR IGNORE INTO identity_teams (id, org_id, name, created_at) VALUES (?, ?, ?, ?)",
            (team_id, org_id, name, now),
        )
        await conn.commit()
        await conn.close()
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
