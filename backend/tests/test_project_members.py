"""项目成员：项目经理按姓名，其余同事默认可当项目支撑。"""

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

PASSWORD = "RoleAssign2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, name: str, role_codes: list[str], job_title: str = "") -> User:
    username = f"pr_{uuid4().hex[:10]}"
    async with async_session() as db:
        await init_rbac(db)
        user = User(
            username=username,
            name=name,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
            is_active=True,
            job_title=job_title,
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


async def _project(owner: User) -> Project:
    async with async_session() as db:
        project = Project(
            name=f"role_{uuid4().hex[:6]}",
            project_type="software",
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
async def test_support_is_team_member_except_managers_and_admin():
    editor = await _make_user(name="部门负责人甲", role_codes=["dept_head"])
    manager = await _make_user(name="许金斌", role_codes=["member"])
    support = await _make_user(name="俞烨涛", role_codes=["member"], job_title="任意岗位")
    other_manager = await _make_user(name="周永祺", role_codes=["member"])
    excluded = await _make_user(name="孙振振", role_codes=["member"], job_title="技术经理")
    project = await _project(manager)

    async with _client() as client:
        token = await _login(client, editor.username)
        headers = {"Authorization": f"Bearer {token}"}

        # 项目经理不能当支撑
        bad_manager = await client.put(
            f"/api/projects/{project.id}/members",
            headers=headers,
            json={
                "lead_user_id": manager.id,
                "support_user_id": other_manager.id,
                "assistant_user_ids": [],
            },
        )
        assert bad_manager.status_code == 400, bad_manager.text

        bad_name = await client.put(
            f"/api/projects/{project.id}/members",
            headers=headers,
            json={
                "lead_user_id": manager.id,
                "support_user_id": excluded.id,
                "assistant_user_ids": [],
            },
        )
        assert bad_name.status_code == 400, bad_name.text

        ok = await client.put(
            f"/api/projects/{project.id}/members",
            headers=headers,
            json={
                "lead_user_id": manager.id,
                "support_user_id": support.id,
                "assistant_user_ids": [],
            },
        )
        assert ok.status_code == 200, ok.text
        roles = {m["user"]["id"]: m["member_role"] for m in ok.json()}
        assert roles[manager.id] == "lead"
        assert roles[support.id] == "support"


@pytest.mark.asyncio
async def test_create_project_assigns_roles_and_notifies(monkeypatch):
    notices: list[tuple[int, str]] = []

    async def _fake_notice(db, person, *, role_label, project_name, project_id, actor_name):
        notices.append((person.id, role_label))
        return True

    monkeypatch.setattr("app.api.projects.notify_project_role", _fake_notice)

    editor = await _make_user(name="部门负责人乙", role_codes=["dept_head"])
    manager = await _make_user(name="周永祺", role_codes=["member"])
    support = await _make_user(name="钱波", role_codes=["member"], job_title="")
    excluded = await _make_user(name="孙振振", role_codes=["member"], job_title="")

    async with _client() as client:
        token = await _login(client, editor.username)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "name": f"新建角色_{uuid4().hex[:6]}",
            "project_type": "software",
            "level": "normal",
            "lead_user_id": manager.id,
            "support_user_id": excluded.id,
        }
        bad = await client.post("/api/projects", headers=headers, json=payload)
        assert bad.status_code == 400, bad.text

        payload["support_user_id"] = support.id
        ok = await client.post("/api/projects", headers=headers, json=payload)
        assert ok.status_code == 200, ok.text
        body = ok.json()
        # 归属人=创建人；创建人是普通同事时也会自动挂为支撑
        assert body["owner"]["id"] == editor.id

        members = await client.get(f"/api/projects/{body['id']}/members", headers=headers)
        assert members.status_code == 200, members.text
        roles = {m["user"]["id"]: m["member_role"] for m in members.json()}
        assert roles[manager.id] == "lead"
        assert roles[support.id] == "support"
        assert roles[editor.id] == "support"

    assert (manager.id, "项目经理") in notices
    assert (support.id, "项目支撑") in notices
    assert (editor.id, "项目支撑") in notices


@pytest.mark.asyncio
async def test_create_project_support_creator_defaults_self(monkeypatch):
    notices: list[tuple[int, str]] = []

    async def _fake_notice(db, person, *, role_label, project_name, project_id, actor_name):
        notices.append((person.id, role_label))
        return True

    monkeypatch.setattr("app.api.projects.notify_project_role", _fake_notice)

    creator = await _make_user(name="创建支撑甲", role_codes=["dept_head"])
    peer = await _make_user(name="另一支撑", role_codes=["member"])

    async with _client() as client:
        token = await _login(client, creator.username)
        headers = {"Authorization": f"Bearer {token}"}
        # 不传 support：后端应按创建人自动设为项目支撑
        ok = await client.post(
            "/api/projects",
            headers=headers,
            json={
                "name": f"无经理_{uuid4().hex[:6]}",
                "project_type": "software",
                "level": "normal",
                "lead_user_id": None,
                "support_user_ids": [],
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["owner"]["id"] == creator.id

        members = await client.get(f"/api/projects/{body['id']}/members", headers=headers)
        roles = {m["user"]["id"]: m["member_role"] for m in members.json()}
        assert "lead" not in roles.values()
        assert roles[creator.id] == "support"

        # 也可再加同伴
        ok2 = await client.post(
            "/api/projects",
            headers=headers,
            json={
                "name": f"双支撑_{uuid4().hex[:6]}",
                "project_type": "software",
                "level": "normal",
                "lead_user_id": None,
                "support_user_ids": [peer.id],
            },
        )
        assert ok2.status_code == 200, ok2.text
        mid = ok2.json()["id"]
        members2 = await client.get(f"/api/projects/{mid}/members", headers=headers)
        roles2 = {m["user"]["id"]: m["member_role"] for m in members2.json()}
        assert roles2[creator.id] == "support"
        assert roles2[peer.id] == "support"

    assert (creator.id, "项目支撑") in notices


@pytest.mark.asyncio
async def test_create_project_manager_creator_defaults_self_as_lead(monkeypatch):
    notices: list[tuple[int, str]] = []

    async def _fake_notice(db, person, *, role_label, project_name, project_id, actor_name):
        notices.append((person.id, role_label))
        return True

    monkeypatch.setattr("app.api.projects.notify_project_role", _fake_notice)

    manager = await _make_user(name="许金斌", role_codes=["dept_head"])
    support = await _make_user(name="徐初", role_codes=["member"])

    async with _client() as client:
        token = await _login(client, manager.username)
        headers = {"Authorization": f"Bearer {token}"}
        ok = await client.post(
            "/api/projects",
            headers=headers,
            json={
                "name": f"经理自建_{uuid4().hex[:6]}",
                "project_type": "software",
                "level": "normal",
                "lead_user_id": None,
                "support_user_ids": [support.id],
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["owner"]["id"] == manager.id

        members = await client.get(f"/api/projects/{body['id']}/members", headers=headers)
        roles = {m["user"]["id"]: m["member_role"] for m in members.json()}
        assert roles[manager.id] == "lead"
        assert roles[support.id] == "support"

    assert (manager.id, "项目经理") in notices
    assert (support.id, "项目支撑") in notices
