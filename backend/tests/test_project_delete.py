"""项目管理：管理员删除项目（密码校验）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import (
    ProgressReport,
    ProgressReportItem,
    Project,
    ProjectMember,
    Role,
    Task,
    User,
    UserRole,
)
from app.services.init_service import init_rbac

PASSWORD = "ProjDel2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"pd_{uuid4().hex[:10]}"
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


async def _make_project_with_task(owner: User) -> tuple[Project, Task, ProgressReportItem]:
    from datetime import date

    async with async_session() as db:
        project = Project(
            name=f"proj_del_{uuid4().hex[:8]}",
            project_type="software",
            level="normal",
            status="normal",
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        task = Task(
            project_id=project.id,
            title="待删任务",
            status="not_started",
            created_by_id=owner.id,
            assignee_id=owner.id,
        )
        db.add(task)
        await db.flush()
        report = ProgressReport(
            user_id=owner.id,
            period_type="week",
            period_start=date.today(),
            theme="删除联调",
            status="draft",
        )
        db.add(report)
        await db.flush()
        item = ProgressReportItem(
            report_id=report.id,
            project_id=project.id,
            linked_task_id=task.id,
            task_title="周报条目保留文字",
        )
        db.add(item)
        await db.commit()
        await db.refresh(project)
        await db.refresh(task)
        await db.refresh(item)
        return project, task, item


@pytest.mark.asyncio
async def test_member_cannot_delete_project():
    owner = await _make_user(role_codes=["project_owner"], name="负责人")
    member = await _make_user(role_codes=["member"], name="普通成员")
    project, _task, _item = await _make_project_with_task(owner)

    async with _client() as client:
        token = await _login(client, member.username)
        resp = await client.request(
            "DELETE",
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": PASSWORD},
        )
        assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_admin_wrong_password_rejected():
    owner = await _make_user(role_codes=["project_owner"], name="负责人乙")
    admin = await _make_user(role_codes=["admin"], name="管理员")
    project, _task, _item = await _make_project_with_task(owner)

    async with _client() as client:
        token = await _login(client, admin.username)
        resp = await client.request(
            "DELETE",
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": "WrongPass999"},
        )
        assert resp.status_code == 400, resp.text
        assert "密码" in resp.json()["detail"]

        # 项目仍在
        async with async_session() as db:
            still = (
                await db.execute(select(Project).where(Project.id == project.id))
            ).scalar_one_or_none()
            assert still is not None


@pytest.mark.asyncio
async def test_admin_can_delete_project_with_password():
    owner = await _make_user(role_codes=["project_owner"], name="负责人丙")
    admin = await _make_user(role_codes=["admin"], name="管理员丙")
    project, task, item = await _make_project_with_task(owner)
    project_id = project.id
    task_id = task.id
    item_id = item.id

    async with _client() as client:
        token = await _login(client, admin.username)
        resp = await client.request(
            "DELETE",
            f"/api/projects/{project_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": PASSWORD},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True

    async with async_session() as db:
        assert (
            await db.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(Task).where(Task.id == task_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
        ).scalars().first() is None
        kept = (
            await db.execute(select(ProgressReportItem).where(ProgressReportItem.id == item_id))
        ).scalar_one()
        assert kept.project_id is None
        assert kept.linked_task_id is None
        assert kept.task_title == "周报条目保留文字"


@pytest.mark.asyncio
async def test_dept_head_can_delete_project():
    owner = await _make_user(role_codes=["project_owner"], name="负责人丁")
    head = await _make_user(role_codes=["dept_head"], name="部门负责人")
    project, _task, _item = await _make_project_with_task(owner)

    async with _client() as client:
        token = await _login(client, head.username)
        resp = await client.request(
            "DELETE",
            f"/api/projects/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"password": PASSWORD},
        )
        assert resp.status_code == 200, resp.text
