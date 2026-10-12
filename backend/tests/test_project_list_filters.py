"""项目列表：状态 / 负责人筛选。"""

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

PASSWORD = "ProjFilt2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"pf_{uuid4().hex[:10]}"
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


async def _make_project(
    owner: User,
    *,
    name: str | None = None,
    status: str = "normal",
    level: str = "normal",
) -> Project:
    async with async_session() as db:
        project = Project(
            name=name or f"pf_proj_{uuid4().hex[:8]}",
            project_type="software",
            level=level,
            status=status,
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        await db.commit()
        await db.refresh(project)
        return project


@pytest.mark.asyncio
async def test_list_projects_filter_by_status_and_owner():
    owner_a = await _make_user(role_codes=["project_owner"], name="筛选负责人A")
    owner_b = await _make_user(role_codes=["project_owner"], name="筛选负责人B")
    admin = await _make_user(role_codes=["admin"], name="筛选管理员")

    blocked = await _make_project(owner_a, name=f"pf_proj_blocked_{uuid4().hex[:6]}", status="blocked")
    await _make_project(owner_a, name=f"pf_proj_normal_{uuid4().hex[:6]}", status="normal")
    await _make_project(owner_b, name=f"pf_proj_other_{uuid4().hex[:6]}", status="blocked")

    async with _client() as client:
        token = await _login(client, admin.username)
        headers = {"Authorization": f"Bearer {token}"}

        by_status = await client.get(
            "/api/projects",
            headers=headers,
            params={"status": "blocked", "page_size": 100},
        )
        assert by_status.status_code == 200, by_status.text
        status_ids = {p["id"] for p in by_status.json()["items"]}
        assert blocked.id in status_ids
        assert all(p["status"] == "blocked" for p in by_status.json()["items"] if p["id"] in status_ids)

        by_owner = await client.get(
            "/api/projects",
            headers=headers,
            params={"owner_id": owner_a.id, "status": "blocked"},
        )
        assert by_owner.status_code == 200, by_owner.text
        rows = by_owner.json()["items"]
        assert {p["id"] for p in rows} == {blocked.id}
        assert rows[0]["owner"]["id"] == owner_a.id


@pytest.mark.asyncio
async def test_list_projects_pagination():
    """项目列表分页：默认每页 10，可改 page_size。"""
    owner = await _make_user(role_codes=["project_owner"], name="分页项目负责人")
    admin = await _make_user(role_codes=["admin"], name="分页项目管理员")
    created_ids: list[int] = []
    for i in range(12):
        p = await _make_project(owner, name=f"pf_proj_page_{i}_{uuid4().hex[:4]}")
        created_ids.append(p.id)

    async with _client() as client:
        token = await _login(client, admin.username)
        headers = {"Authorization": f"Bearer {token}"}

        default = await client.get(
            "/api/projects",
            headers=headers,
            params={"search": "pf_proj_page_"},
        )
        assert default.status_code == 200, default.text
        body = default.json()
        assert body["page"] == 1
        assert body["page_size"] == 10
        assert body["total"] >= 12
        assert len(body["items"]) == 10

        page2 = await client.get(
            "/api/projects",
            headers=headers,
            params={"search": "pf_proj_page_", "page": 2, "page_size": 10},
        )
        assert page2.status_code == 200, page2.text
        p2 = page2.json()
        assert p2["page"] == 2
        assert len(p2["items"]) >= 2
        assert {t["id"] for t in body["items"]}.isdisjoint({t["id"] for t in p2["items"]})

        big = await client.get(
            "/api/projects",
            headers=headers,
            params={"search": "pf_proj_page_", "page_size": 50},
        )
        assert big.status_code == 200, big.text
        assert big.json()["page_size"] == 50
        assert {t["id"] for t in big.json()["items"]} >= set(created_ids)
