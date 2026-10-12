"""一人一助手工具门禁：待办隔离、技能包、习惯排序、改派须确认。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from starlette.requests import Request

from app.api.dsh import embed_origin
from app.core.config import settings
from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import (
    ProgressReportItem,
    Project,
    ProjectMember,
    Role,
    Solution,
    Task,
    User,
    UserRole,
)
from app.services.init_service import init_rbac
from app.services.service_auth import SERVICE_DSH, build_signed_headers

PASSWORD = "AgentNative2026"
SECRET = "dsh-agent-test-secret"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"t_ag_{uuid4().hex[:10]}"
    async with async_session() as db:
        await init_rbac(db)
        user = User(
            username=username,
            name=name or username,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        roles = (await db.execute(select(Role).where(Role.code.in_(role_codes)))).scalars().all()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        await db.refresh(user)
        return user


async def _make_project(owner: User, *members: User) -> Project:
    async with async_session() as db:
        project = Project(
            name=f"proj_ag_{uuid4().hex[:8]}",
            project_type="software",
            level="normal",
            status="normal",
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        for member in members:
            if member.id == owner.id:
                continue
            db.add(ProjectMember(project_id=project.id, user_id=member.id, member_role="member"))
        await db.commit()
        await db.refresh(project)
        return project


async def _add_task(project_id: int, assignee_id: int, title: str, **kwargs) -> Task:
    async with async_session() as db:
        task = Task(
            project_id=project_id,
            assignee_id=assignee_id,
            created_by_id=assignee_id,
            assignment_type=kwargs.get("assignment_type", "self"),
            title=title,
            goal=kwargs.get("goal", "做成可验收结果"),
            due_date=kwargs.get("due_date"),
            status=kwargs.get("status", "not_started"),
            priority=kwargs.get("priority", "p3"),
            acceptance_status=kwargs.get("acceptance_status", "accepted"),
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        return task


async def _signed(client: AsyncClient, path: str, payload: dict, secret: str = SECRET):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(secret, SERVICE_DSH, "POST", path, body)
    return await client.post(path, content=body.encode("utf-8"), headers=headers)


@pytest.fixture
def agent_secret(monkeypatch):
    monkeypatch.setattr(settings, "dsh_to_wb_secret", SECRET)
    monkeypatch.setattr(settings, "dsh_service_allowlist", "127.0.0.1,::1")
    return SECRET


@pytest.mark.asyncio
async def test_my_todos_isolated_between_two_members(agent_secret):
    owner = await _make_user(role_codes=["project_owner"], name="负责人")
    alice = await _make_user(role_codes=["member"], name="成员甲")
    bob = await _make_user(role_codes=["member"], name="成员乙")
    project = await _make_project(owner, alice, bob)
    await _add_task(project.id, alice.id, "甲的待办-保密")
    await _add_task(project.id, bob.id, "乙的待办-保密")

    async with _client() as client:
        alice_resp = await _signed(
            client, "/api/dsh/tools/my-todos", {"wb_user_id": alice.id}
        )
        bob_resp = await _signed(
            client, "/api/dsh/tools/my-todos", {"wb_user_id": bob.id}
        )
    assert alice_resp.status_code == 200, alice_resp.text
    assert bob_resp.status_code == 200, bob_resp.text
    alice_titles = [t["title"] for t in alice_resp.json()["items"]]
    bob_titles = [t["title"] for t in bob_resp.json()["items"]]
    assert "甲的待办-保密" in alice_titles
    assert "乙的待办-保密" not in alice_titles
    assert "乙的待办-保密" in bob_titles
    assert "甲的待办-保密" not in bob_titles


@pytest.mark.asyncio
async def test_inactive_user_cannot_use_tools(agent_secret):
    owner = await _make_user(role_codes=["member"], name="停用前")
    async with async_session() as db:
        user = await db.get(User, owner.id)
        user.is_active = False
        await db.commit()
    async with _client() as client:
        resp = await _signed(client, "/api/dsh/tools/my-todos", {"wb_user_id": owner.id})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_member_skill_pack_excludes_manager_analyze(agent_secret):
    member = await _make_user(role_codes=["member"])
    admin = await _make_user(role_codes=["admin"])
    async with _client() as client:
        member_pack = await _signed(
            client, "/api/dsh/tools/skill-pack", {"wb_user_id": member.id}
        )
        admin_pack = await _signed(
            client, "/api/dsh/tools/skill-pack", {"wb_user_id": admin.id}
        )
    assert member_pack.status_code == 200
    assert admin_pack.status_code == 200
    member_ids = member_pack.json()["skill_ids"]
    admin_ids = admin_pack.json()["skill_ids"]
    assert "task" in member_ids
    assert "manager" not in member_ids
    assert "manager" in admin_ids


@pytest.mark.asyncio
async def test_rank_todos_puts_overdue_and_p0_first(agent_secret):
    owner = await _make_user(role_codes=["project_owner"])
    member = await _make_user(role_codes=["member"])
    project = await _make_project(owner, member)
    today = date.today()
    await _add_task(
        project.id, member.id, "普通明天", due_date=today + timedelta(days=1), priority="p3"
    )
    await _add_task(
        project.id, member.id, "重点未逾期", due_date=today + timedelta(days=2), priority="p0"
    )
    await _add_task(
        project.id, member.id, "已逾期", due_date=today - timedelta(days=1), priority="p3"
    )
    async with _client() as client:
        resp = await _signed(client, "/api/dsh/tools/rank-todos", {"wb_user_id": member.id})
    assert resp.status_code == 200, resp.text
    titles = [t["title"] for t in resp.json()["items"]]
    assert titles[0] == "已逾期"
    assert "重点未逾期" in titles
    assert titles.index("重点未逾期") < titles.index("普通明天")


@pytest.mark.asyncio
async def test_nudge_skips_quiet_hours_and_dedupes(agent_secret):
    owner = await _make_user(role_codes=["project_owner"])
    member = await _make_user(role_codes=["member"])
    project = await _make_project(owner, member)
    task = await _add_task(
        project.id,
        member.id,
        "待接收交办",
        assignment_type="delegated",
        acceptance_status="pending",
        due_date=date.today(),
    )
    async with _client() as client:
        prefs = await _signed(
            client,
            "/api/dsh/tools/prefs",
            {
                "wb_user_id": member.id,
                "quiet_start": "00:00:00",
                "quiet_end": "23:59:00",
                "digest_mode": "morning",
                "channel_inbox": True,
                "channel_dingtalk": False,
            },
        )
        assert prefs.status_code == 200, prefs.text

    payload = {
        "wb_user_id": member.id,
        "event": "assigned",
        "task_id": task.id,
        "now_time": "12:00:00",
    }
    async with _client() as client:
        first = await _signed(client, "/api/dsh/tools/nudge", payload)
        second = await _signed(client, "/api/dsh/tools/nudge", payload)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["sent"] is False
    assert body["reason"] == "quiet_hours"
    assert second.json()["reason"] in {"quiet_hours", "already_nudged"}


@pytest.mark.asyncio
async def test_reassign_draft_does_not_change_owner_until_confirm(agent_secret):
    owner = await _make_user(role_codes=["project_owner", "dept_head"], name="负责人")
    alice = await _make_user(role_codes=["member"], name="成员甲")
    bob = await _make_user(role_codes=["member"], name="成员乙")
    project = await _make_project(owner, alice, bob)
    task = await _add_task(project.id, alice.id, "待改派任务")

    async with _client() as client:
        denied = await _signed(
            client,
            "/api/dsh/tools/draft-reassign",
            {
                "wb_user_id": alice.id,
                "task_id": task.id,
                "new_assignee_id": bob.id,
                "reason": "负荷更低",
            },
        )
        assert denied.status_code == 403

        drafted = await _signed(
            client,
            "/api/dsh/tools/draft-reassign",
            {
                "wb_user_id": owner.id,
                "task_id": task.id,
                "new_assignee_id": bob.id,
                "reason": "负荷更低",
            },
        )
        assert drafted.status_code == 200, drafted.text
        draft_id = drafted.json()["draft_id"]

        async with async_session() as db:
            still = await db.get(Task, task.id)
            assert still.assignee_id == alice.id

        confirmed = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": owner.id, "draft_id": draft_id},
        )
        assert confirmed.status_code == 200, confirmed.text

    async with async_session() as db:
        done = await db.get(Task, task.id)
        assert done.assignee_id == bob.id


@pytest.mark.asyncio
async def test_draft_task_and_week_report_need_confirm(agent_secret):
    owner = await _make_user(role_codes=["project_owner"], name="负责人")
    member = await _make_user(role_codes=["member"], name="支撑甲")
    project = await _make_project(owner, member)
    due = (date.today() + timedelta(days=3)).isoformat()

    async with _client() as client:
        drafted = await _signed(
            client,
            "/api/dsh/tools/draft-task",
            {
                "wb_user_id": member.id,
                "project_id": project.id,
                "title": "助手起草任务",
                "due_date": due,
                "goal": "可见可验收",
                "priority": "p2",
            },
        )
        assert drafted.status_code == 200, drafted.text
        draft_id = drafted.json()["draft_id"]
        async with async_session() as db:
            before = (
                await db.execute(select(Task).where(Task.title == "助手起草任务"))
            ).scalar_one_or_none()
            assert before is None
        confirmed = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": member.id, "draft_id": draft_id},
        )
        assert confirmed.status_code == 200, confirmed.text
        task_id = confirmed.json()["task_id"]

        week = await _signed(
            client,
            "/api/dsh/tools/draft-week-report",
            {
                "wb_user_id": member.id,
                "items": [
                    {
                        "task_id": task_id,
                        "project_id": project.id,
                        "task_title": "助手起草任务",
                        "result_text": "已开工",
                        "next_goal": "下周验收",
                    }
                ],
            },
        )
        assert week.status_code == 200, week.text
        week_id = week.json()["draft_id"]
        week_ok = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": member.id, "draft_id": week_id},
        )
        assert week_ok.status_code == 200, week_ok.text

    async with async_session() as db:
        task = await db.get(Task, task_id)
        assert task is not None
        assert task.assignee_id == member.id
        assert task.source == "agent"
        item = (
            await db.execute(
                select(ProgressReportItem).where(ProgressReportItem.linked_task_id == task_id)
            )
        ).scalar_one_or_none()
        assert item is not None
        assert "已开工" in (item.result_text or "")


@pytest.mark.asyncio
async def test_draft_todo_defaults_to_daily_and_appears_in_my_todos(agent_secret):
    member = await _make_user(role_codes=["member"], name="待办写手")
    due = (date.today() + timedelta(days=2)).isoformat()

    async with _client() as client:
        drafted = await _signed(
            client,
            "/api/dsh/tools/draft-todo",
            {
                "wb_user_id": member.id,
                "items": [
                    {"title": "助手待办甲", "due_date": due, "goal": "验收甲"},
                    {"title": "助手待办乙", "due_date": due},
                ],
            },
        )
        assert drafted.status_code == 200, drafted.text
        body = drafted.json()
        assert body["kind"] == "todo"
        assert body["item_count"] == 2
        draft_id = body["draft_id"]

        async with async_session() as db:
            before = (
                await db.execute(select(Task).where(Task.title == "助手待办甲"))
            ).scalar_one_or_none()
            assert before is None

        confirmed = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": member.id, "draft_id": draft_id},
        )
        assert confirmed.status_code == 200, confirmed.text
        conf = confirmed.json()
        assert conf["ok"] is True
        assert conf["kind"] == "todo"
        assert conf["count"] == 2
        assert conf["project_name"] == "日常任务"

        todos = await _signed(
            client, "/api/dsh/tools/my-todos", {"wb_user_id": member.id}
        )
        assert todos.status_code == 200, todos.text
        titles = [t["title"] for t in todos.json()["items"]]
        assert "助手待办甲" in titles
        assert "助手待办乙" in titles


@pytest.mark.asyncio
async def test_draft_project_case_and_legacy_ai_gone(agent_secret, monkeypatch):
    member = await _make_user(role_codes=["member"], name="支撑乙")
    end = (date.today() + timedelta(days=30)).isoformat()
    async with _client() as client:
        proj_draft = await _signed(
            client,
            "/api/dsh/tools/draft-project",
            {
                "wb_user_id": member.id,
                "name": "proj_ag_native_demo",
                "project_type": "software",
                "end_date": end,
            },
        )
        assert proj_draft.status_code == 200, proj_draft.text
        proj_ok = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": member.id, "draft_id": proj_draft.json()["draft_id"]},
        )
        assert proj_ok.status_code == 200, proj_ok.text
        case_draft = await _signed(
            client,
            "/api/dsh/tools/draft-case",
            {
                "wb_user_id": member.id,
                "title": "开票卡点处理",
                "summary": "先核销再开票，避免重复申请。",
                "tags": ["开票"],
            },
        )
        assert case_draft.status_code == 200, case_draft.text
        case_ok = await _signed(
            client,
            "/api/dsh/tools/confirm-draft",
            {"wb_user_id": member.id, "draft_id": case_draft.json()["draft_id"]},
        )
        assert case_ok.status_code == 200, case_ok.text

        login = await client.post(
            "/api/auth/login", json={"username": member.username, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        monkeypatch.setattr(settings, "agent_native", True)
        gone = await client.post(
            "/api/tasks/ai/draft",
            json={"text": "跟进开票"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert gone.status_code == 410

    async with async_session() as db:
        project = (
            await db.execute(select(Project).where(Project.name == "proj_ag_native_demo"))
        ).scalar_one_or_none()
        sol = (
            await db.execute(select(Solution).where(Solution.title == "开票卡点处理"))
        ).scalar_one_or_none()
        assert project is not None
        assert sol is not None


def test_embed_origin_allows_http_on_agent_native(monkeypatch):
    monkeypatch.setattr(settings, "agent_native", True)
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/dsh/ticket",
            "raw_path": b"/api/dsh/ticket",
            "query_string": b"",
            "headers": [
                (b"host", b"30.251.255.142:8080"),
                (b"origin", b"http://30.251.255.142:8080"),
            ],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8080),
        }
    )
    assert embed_origin(request) == "http://30.251.255.142:8080"


@pytest.mark.asyncio
async def test_agent_native_ticket_uses_sso_code(monkeypatch):
    monkeypatch.setattr(settings, "agent_native", True)
    monkeypatch.setattr(settings, "dsh_sso_secret", "sso-test-secret")

    def fake_post(path, payload, timeout=15):
        assert path == "/internal/sso/exchange"
        assert payload.get("ticket")
        return {"code": "once-code"}

    monkeypatch.setattr("app.api.dsh.signed_dsh_post", fake_post)
    user = await _make_user(role_codes=["admin"])
    async with _client() as client:
        login = await client.post(
            "/api/auth/login", json={"username": user.username, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        resp = await client.post(
            "/api/dsh/ticket",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://30.251.255.142:8080",
            },
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["embed_url"] == "http://30.251.255.142:8080/sso?code=once-code"


@pytest.mark.asyncio
async def test_agent_native_ticket_without_sso_secret(monkeypatch):
    monkeypatch.setattr(settings, "agent_native", True)
    monkeypatch.setattr(settings, "dsh_sso_secret", "")
    user = await _make_user(role_codes=["admin"])
    async with _client() as client:
        login = await client.post(
            "/api/auth/login", json={"username": user.username, "password": PASSWORD}
        )
        token = login.json()["access_token"]
        resp = await client.post(
            "/api/dsh/ticket",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "http://30.251.255.142:8080",
            },
        )
    assert resp.status_code == 503


def test_assert_isolated_db_rejects_prod_host():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.assert_isolated_db import database_url_ok

    assert database_url_ok("postgresql+asyncpg://u:p@127.0.0.1:5432/project_workbench_agent")
    assert not database_url_ok(
        "postgresql+asyncpg://u:p@30.251.255.64:5432/project_workbench"
    )
