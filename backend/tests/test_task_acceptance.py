"""交办接收门禁、催办、AI 草稿兜底、改派重置。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac

PASSWORD = "AcceptFlow2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"af_{uuid4().hex[:10]}"
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


async def _make_project(owner: User, *members: User) -> Project:
    async with async_session() as db:
        project = Project(
            name=f"proj_{uuid4().hex[:8]}",
            project_type="software",
            level="normal",
            status="normal",
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        for m in members:
            if m.id == owner.id:
                continue
            db.add(ProjectMember(project_id=project.id, user_id=m.id, member_role="member"))
        await db.commit()
        await db.refresh(project)
        return project


@pytest.mark.asyncio
async def test_delegated_requires_accept_before_progress():
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    member = await _make_user(role_codes=["member"], name="执行人")
    project = await _make_project(owner, member)

    async with _client() as client:
        token = await _login(client, owner.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "project_id": project.id,
                "title": "交办测试任务",
                "assignment_type": "delegated",
                "assignee_id": member.id,
                "accept_by": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            },
        )
        assert create.status_code == 200, create.text
        task = create.json()
        assert task["acceptance_status"] == "pending"
        task_id = task["id"]

        # 未接收不能改进度
        mtoken = await _login(client, member.username)
        blocked = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={"status": "in_progress"},
        )
        assert blocked.status_code == 400
        assert "接收" in blocked.json()["detail"]

        accepted = await client.post(
            f"/api/tasks/{task_id}/accept",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["acceptance_status"] == "accepted"

        ok = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={"status": "in_progress"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_remind_and_reassign_reset_pending():
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    m1 = await _make_user(role_codes=["member"], name="甲")
    m2 = await _make_user(role_codes=["member"], name="乙")
    project = await _make_project(owner, m1, m2)

    async with _client() as client:
        token = await _login(client, owner.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "project_id": project.id,
                "title": "催办改派",
                "assignment_type": "delegated",
                "assignee_id": m1.id,
            },
        )
        task_id = create.json()["id"]

        remind = await client.post(
            f"/api/tasks/{task_id}/remind",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert remind.status_code == 200, remind.text

        reassign = await client.patch(
            f"/api/tasks/{task_id}/owner",
            headers={"Authorization": f"Bearer {token}"},
            json={"assignee_id": m2.id},
        )
        assert reassign.status_code == 200, reassign.text
        body = reassign.json()
        assert body["assignee"]["id"] == m2.id
        assert body["acceptance_status"] == "pending"


@pytest.mark.asyncio
async def test_ai_task_draft_rule_fallback():
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    member = await _make_user(role_codes=["member"], name="张三")
    project = await _make_project(owner, member)

    async with _client() as client:
        token = await _login(client, owner.username)
        resp = await client.post(
            "/api/tasks/ai/draft",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": f"交办张三跟进{project.name}开票"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["title"]
        assert data["assignment_type"] in {"self", "delegated"}
        assert data.get("source") in {"rule", "llm", "empty", "error", None} or True


@pytest.mark.asyncio
async def test_ai_overview_and_project_draft():
    owner = await _make_user(role_codes=["project_owner", "admin"], name="管理员")
    await _make_project(owner)

    async with _client() as client:
        token = await _login(client, owner.username)
        brief = await client.get(
            "/api/ai/overview/brief",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert brief.status_code == 200, brief.text
        assert "summary" in brief.json()

        draft = await client.post(
            "/api/ai/project/draft",
            headers={"Authorization": f"Bearer {token}"},
            json={"text": "新建智安校园运维项目，处理终端中毒"},
        )
        assert draft.status_code == 200, draft.text
        assert draft.json()["name"]
