"""任务去重：空泛续推、相似分级、创建 API 拦截。"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, Task, User, UserRole
from app.services.init_service import init_rbac
from app.services.task_dedupe import (
    HARD_SIMILARITY,
    SOFT_SIMILARITY,
    check_task_duplicate,
    classify_similar_level,
    is_vague_continuation,
    score_task_similarity,
)


def test_is_vague_continuation():
    assert is_vague_continuation("继续推进") is True
    assert is_vague_continuation("持续跟进") is True
    assert is_vague_continuation("继续推进零信任终端试点联调") is False
    assert is_vague_continuation("天网接入冲刺") is False


def test_score_and_levels():
    assert score_task_similarity("天网接入冲刺", "", "天网接入冲刺", "") >= HARD_SIMILARITY
    soft = score_task_similarity("天网接入冲刺", "", "天网接入推进", "")
    assert soft >= SOFT_SIMILARITY
    assert classify_similar_level([{"score": 0.9}]) == "hard"
    assert classify_similar_level([{"score": 0.6}]) == "soft"
    assert classify_similar_level([]) == "none"


async def _ensure_user(username: str = "t_dedupe_a") -> User:
    async with async_session() as db:
        await init_rbac(db)
        u = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
        if not u:
            u = User(
                username=username,
                name="去重测试甲",
                hashed_password=hash_password("TestPass2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(u)
            await db.flush()
        else:
            u.hashed_password = hash_password("TestPass2026")
            u.must_change_password = False
            u.is_active = True
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()
        await db.refresh(u)
        return u


@pytest.mark.asyncio
async def test_check_duplicate_finds_open_task():
    user = await _ensure_user()
    uniq = uuid.uuid4().hex[:8]
    title = f"去重样例任务-{uniq}"
    async with async_session() as db:
        project = Project(
            name=f"去重测试项目-{uniq}",
            project_type="software",
            level="normal",
            owner_id=user.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user.id, member_role="lead"))
        db.add(
            Task(
                project_id=project.id,
                assignee_id=user.id,
                created_by_id=user.id,
                assignment_type="self",
                title=title,
                goal="",
                status="not_started",
                priority="p3",
                acceptance_status="accepted",
            )
        )
        await db.commit()
        pid = project.id

        dup = await check_task_duplicate(
            db, assignee_id=user.id, title=title, goal="", project_id=pid
        )
        assert dup["vague"] is False
        assert dup["level"] == "hard"
        assert dup["similar"]
        assert dup["similar"][0]["title"] == title


@pytest.mark.asyncio
async def test_create_task_vague_and_force():
    user = await _ensure_user("t_dedupe_b")
    uniq = uuid.uuid4().hex[:8]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "t_dedupe_b", "password": "TestPass2026"},
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        async with async_session() as db:
            project = Project(
                name=f"去重测试项目B-{uniq}",
                project_type="software",
                level="normal",
                owner_id=user.id,
            )
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, member_role="lead"))
            await db.commit()
            pid = project.id

        vague = await client.post(
            "/api/tasks",
            headers=headers,
            json={"project_id": pid, "title": "继续推进", "goal": "", "assignment_type": "self"},
        )
        assert vague.status_code == 400, vague.text

        title = f"唯一任务-{uniq}"
        ok = await client.post(
            "/api/tasks",
            headers=headers,
            json={"project_id": pid, "title": title, "goal": "验收", "assignment_type": "self"},
        )
        assert ok.status_code == 200, ok.text

        conflict = await client.post(
            "/api/tasks",
            headers=headers,
            json={"project_id": pid, "title": title, "goal": "验收", "assignment_type": "self"},
        )
        assert conflict.status_code == 409, conflict.text
        detail = conflict.json()["detail"]
        assert detail["code"] == "similar_task"

        forced = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": pid,
                "title": title,
                "goal": "验收",
                "assignment_type": "self",
                "force_create": True,
            },
        )
        assert forced.status_code == 200, forced.text

        check = await client.post(
            "/api/tasks/check-similar",
            headers=headers,
            json={
                "title": "继续推进",
                "goal": "",
                "project_id": pid,
                "assignment_type": "self",
            },
        )
        assert check.status_code == 200
        assert check.json()["vague"] is True
