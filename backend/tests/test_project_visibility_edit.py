"""项目管理：成员可见全部项目；仅负责人/管理员可改。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac

PASSWORD = "ProjVis2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"pv_{uuid4().hex[:10]}"
    async with async_session() as db:
        await init_rbac(db)
        user = User(
            username=username,
            name=name or username,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
        )
        db.add(user)
        await db.flush()
        roles = (await db.execute(select(Role).where(Role.code.in_(role_codes)))).scalars().all()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        await db.refresh(user)
        return user


async def _login(client: AsyncClient, username: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _make_project(owner: User, *, name: str | None = None, project_type: str = "software") -> Project:
    async with async_session() as db:
        project = Project(
            name=name or f"proj_{uuid4().hex[:8]}",
            project_type=project_type,
            level="normal",
            status="normal",
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        await db.commit()
        await db.refresh(project)
        return project


@pytest.mark.asyncio
async def test_member_can_list_all_projects():
    owner = await _make_user(role_codes=["project_owner"], name="负责人甲")
    stranger = await _make_user(role_codes=["member"], name="路人成员")
    project = await _make_project(owner, name=f"可见项目_{uuid4().hex[:6]}")

    async with _client() as client:
        token = await _login(client, stranger.username)
        resp = await client.get(
            "/api/projects",
            headers={"Authorization": f"Bearer {token}"},
            params={"page_size": 100, "search": project.name},
        )
        assert resp.status_code == 200, resp.text
        ids = {p["id"] for p in resp.json()["items"]}
        assert project.id in ids


@pytest.mark.asyncio
async def test_member_cannot_edit_others_project():
    owner = await _make_user(role_codes=["project_owner"], name="负责人乙")
    member = await _make_user(role_codes=["member"], name="普通成员")
    project = await _make_project(owner)

    async with _client() as client:
        token = await _login(client, member.username)
        resp = await client.patch(
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "不该成功"},
        )
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_owner_and_admin_can_edit_project():
    owner = await _make_user(role_codes=["project_owner"], name="负责人丙")
    admin = await _make_user(role_codes=["admin"], name="管理员")
    project = await _make_project(owner, name="原名项目")

    async with _client() as client:
        otoken = await _login(client, owner.username)
        owned = await client.patch(
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {otoken}"},
            json={"name": "负责人改名", "project_type": "integration"},
        )
        assert owned.status_code == 200, owned.text
        assert owned.json()["name"] == "负责人改名"
        assert owned.json()["project_type"] == "integration"

        atoken = await _login(client, admin.username)
        admined = await client.patch(
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {atoken}"},
            json={"name": "管理员改名", "project_type": "fusion"},
        )
        assert admined.status_code == 200, admined.text
        assert admined.json()["name"] == "管理员改名"
        assert admined.json()["project_type"] == "fusion"


@pytest.mark.asyncio
async def test_meta_types_three_only():
    user = await _make_user(role_codes=["member"])
    async with _client() as client:
        token = await _login(client, user.username)
        resp = await client.get(
            "/api/projects/meta/types",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        values = {t["value"] for t in resp.json()["types"]}
        assert values == {"software", "integration", "fusion"}
