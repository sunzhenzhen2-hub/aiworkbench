"""任务目标/结果 AI 润色：规则兜底与接口鉴权。"""

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
from app.services.task_ai import _rule_polish_goal, _rule_polish_result

PASSWORD = "PolishFlow2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"pl_{uuid4().hex[:10]}"
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
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _make_project(owner: User) -> Project:
    async with async_session() as db:
        project = Project(
            name=f"pl_proj_{uuid4().hex[:8]}",
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


def test_rule_polish_goal_empty():
    out = _rule_polish_goal(title="联调接口", text="")
    assert "联调接口" in out
    assert "验收" in out


def test_rule_polish_result_short():
    out = _rule_polish_result(title="联调接口", goal="自测通过", text="好了")
    assert "好了" in out


@pytest.mark.asyncio
async def test_ai_polish_goal_endpoint():
    member = await _make_user(role_codes=["member"], name="润色人")
    project = await _make_project(member)

    async with _client() as client:
        token = await _login(client, member.username)
        headers = {"Authorization": f"Bearer {token}"}
        create = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": project.id,
                "title": "核对登录跳转",
                "assignment_type": "self",
                "goal": "测一下",
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]

        polish = await client.post(
            f"/api/tasks/{task_id}/ai/polish",
            headers=headers,
            json={"field": "goal", "text": "测一下"},
        )
        assert polish.status_code == 200, polish.text
        body = polish.json()
        assert body["field"] == "goal"
        assert body["text"]
        assert body["source"] in {"llm", "rule"}

        bad = await client.post(
            f"/api/tasks/{task_id}/ai/polish",
            headers=headers,
            json={"field": "title"},
        )
        assert bad.status_code == 400
