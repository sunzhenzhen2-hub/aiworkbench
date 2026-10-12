"""任务交办：自建 / 交办 / 换负责人 / 辅助人 / 流水账 / workspace。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import AuditLog, Project, ProjectMember, Role, Task, TaskEvent, User, UserRole
from app.services.init_service import init_rbac

PASSWORD = "TaskAssign2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"ta_{uuid4().hex[:10]}"
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
async def test_self_create_task_with_assistant():
    owner = await _make_user(role_codes=["project_owner"], name="负责人")
    member = await _make_user(role_codes=["member"], name="成员")
    assist = await _make_user(role_codes=["member"], name="辅助")
    project = await _make_project(owner, member, assist)

    async with _client() as client:
        token = await _login(client, member.username)
        resp = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "project_id": project.id,
                "title": "我自己的任务",
                "assignment_type": "self",
                "assistant_ids": [assist.id],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["assignment_type"] == "self"
        assert body["assignee"]["id"] == member.id
        assert len(body["assistants"]) == 1
        assert body["assistants"][0]["id"] == assist.id
        assert any(e["action"] == "created" for e in body["events"])
        assert any(e["action"] == "assistant_added" for e in body["events"])

        # 辅助人能在 workspace 看到
        atoken = await _login(client, assist.username)
        ws = await client.get(
            "/api/personal/workspace",
            headers={"Authorization": f"Bearer {atoken}"},
        )
        assert ws.status_code == 200
        assert any(t["id"] == body["id"] for t in ws.json()["my_tasks"])


@pytest.mark.asyncio
async def test_delegate_requires_lead_or_dept_head():
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    member = await _make_user(role_codes=["member"], name="普通")
    target = await _make_user(role_codes=["member"], name="被交办")
    project = await _make_project(owner, member, target)

    async with _client() as client:
        # 普通成员不能交办
        mtoken = await _login(client, member.username)
        bad = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={
                "project_id": project.id,
                "title": "非法交办",
                "assignment_type": "delegated",
                "assignee_id": target.id,
            },
        )
        assert bad.status_code == 403

        # 项目 lead 可交办
        otoken = await _login(client, owner.username)
        ok = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "请跟进联调",
                "assignment_type": "delegated",
                "assignee_id": target.id,
                "assistant_ids": [member.id],
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["assignment_type"] == "delegated"
        assert body["assignee"]["id"] == target.id
        assert {a["id"] for a in body["assistants"]} == {member.id}


@pytest.mark.asyncio
async def test_owner_change_and_assistant_manage_and_complete():
    owner = await _make_user(role_codes=["project_owner"])
    a = await _make_user(role_codes=["member"], name="甲")
    b = await _make_user(role_codes=["member"], name="乙")
    c = await _make_user(role_codes=["member"], name="丙")
    project = await _make_project(owner, a, b, c)

    async with _client() as client:
        otoken = await _login(client, owner.username)
        created = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "换人测试",
                "assignment_type": "delegated",
                "assignee_id": a.id,
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]

        # 换负责人
        ch = await client.patch(
            f"/api/tasks/{task_id}/owner",
            headers={"Authorization": f"Bearer {otoken}"},
            json={"assignee_id": b.id},
        )
        assert ch.status_code == 200, ch.text
        assert ch.json()["assignee"]["id"] == b.id
        assert any(e["action"] == "owner_changed" for e in ch.json()["events"])

        # 新负责人先接收，再加辅助人
        btoken = await _login(client, b.username)
        acc = await client.post(
            f"/api/tasks/{task_id}/accept",
            headers={"Authorization": f"Bearer {btoken}"},
        )
        assert acc.status_code == 200, acc.text
        assert acc.json()["acceptance_status"] == "accepted"

        add = await client.post(
            f"/api/tasks/{task_id}/assistants",
            headers={"Authorization": f"Bearer {btoken}"},
            json={"user_id": c.id},
        )
        assert add.status_code == 200, add.text
        assert {x["id"] for x in add.json()["assistants"]} == {c.id}

        # 完成须填结果
        bad = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {btoken}"},
            json={"status": "completed"},
        )
        assert bad.status_code == 400

        done = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {btoken}"},
            json={"status": "completed", "result_text": "已交付文档"},
        )
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "completed"
        assert done.json()["result_text"] == "已交付文档"

        async with async_session() as db:
            events = (
                await db.execute(select(TaskEvent).where(TaskEvent.task_id == task_id))
            ).scalars().all()
            actions = {e.action for e in events}
            assert "created" in actions
            assert "owner_changed" in actions
            assert "accepted" in actions
            assert "assistant_added" in actions
            assert "status_changed" in actions
            assert "result_updated" in actions


@pytest.mark.asyncio
async def test_task_priority_create_and_manager_change(monkeypatch):
    notices: list[tuple[str, str, str]] = []

    async def _fake_notice(userid: str, title: str, content: str) -> None:
        notices.append((userid, title, content))

    monkeypatch.setattr(
        "app.services.notify_pipeline.send_work_notice",
        _fake_notice,
    )

    owner = await _make_user(role_codes=["project_owner"], name="主责")
    member = await _make_user(role_codes=["member"], name="成员甲")
    project = await _make_project(owner, member)

    async with async_session() as db:
        m = (await db.execute(select(User).where(User.id == member.id))).scalar_one()
        m.dingtalk_user_id = f"dt_{uuid4().hex[:12]}"
        await db.commit()

    async with _client() as client:
        mtoken = await _login(client, member.username)
        created = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={
                "project_id": project.id,
                "title": "重点联调",
                "assignment_type": "self",
                "priority": "p0",
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["priority"] == "p0"
        assert any(e["action"] == "priority_set" for e in body["events"])
        task_id = body["id"]
        assert notices, "P0 任务应尝试发钉钉通知"

        # 普通成员不能改级别
        forbidden = await client.patch(
            f"/api/tasks/{task_id}/priority",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={"priority": "p3"},
        )
        assert forbidden.status_code == 403

        otoken = await _login(client, owner.username)
        changed = await client.patch(
            f"/api/tasks/{task_id}/priority",
            headers={"Authorization": f"Bearer {otoken}"},
            json={"priority": "p1"},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["priority"] == "p1"
        assert any(e["action"] == "priority_changed" for e in changed.json()["events"])

        # 升回 P0 再推送
        notices.clear()
        up = await client.patch(
            f"/api/tasks/{task_id}/priority",
            headers={"Authorization": f"Bearer {otoken}"},
            json={"priority": "p0"},
        )
        assert up.status_code == 200
        assert notices

        stats = await client.get(
            "/api/projects/overview/stats",
            headers={"Authorization": f"Bearer {otoken}"},
        )
        assert stats.status_code == 200
        assert stats.json()["key_task_count"] >= 1

        keys = await client.get(
            "/api/projects/overview/key-tasks",
            headers={"Authorization": f"Bearer {otoken}"},
        )
        assert keys.status_code == 200
        assert any(t["id"] == task_id for t in keys.json())


@pytest.mark.asyncio
async def test_personal_workspace_includes_summary():
    """个人工作台返回 summary：任务完成情况与本周周报状态。"""
    owner = await _make_user(role_codes=["project_owner"], name="概览负责人")
    member = await _make_user(role_codes=["member"], name="概览成员")
    project = await _make_project(owner, member)

    async with _client() as client:
        token = await _login(client, member.username)
        headers = {"Authorization": f"Bearer {token}"}
        created = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": project.id,
                "title": "概览任务",
                "assignment_type": "self",
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]

        ws = await client.get("/api/personal/workspace", headers=headers)
        assert ws.status_code == 200, ws.text
        body = ws.json()
        assert body.get("summary_scope") == "self"
        assert "summary" in body
        summary = body["summary"]
        assert summary["task_total"] >= 1
        assert summary["task_open"] >= 1
        assert summary["project_count"] >= 1
        assert summary["week_report_status"] in ("none", "draft", "submitted")
        assert summary["todo_open"] == summary["task_open"] + summary["blocker_open"]

        # 项目负责人仍为自己口径，不看全量
        otoken = await _login(client, owner.username)
        ws_owner = await client.get(
            "/api/personal/workspace",
            headers={"Authorization": f"Bearer {otoken}"},
        )
        assert ws_owner.status_code == 200
        assert ws_owner.json().get("summary_scope") == "self"

        # 管理员：全量口径，任务数含他人任务
        admin = await _make_user(role_codes=["admin", "dept_head"], name="概览管理员")
        atoken = await _login(client, admin.username)
        ws_admin = await client.get(
            "/api/personal/workspace",
            headers={"Authorization": f"Bearer {atoken}"},
        )
        assert ws_admin.status_code == 200, ws_admin.text
        admin_body = ws_admin.json()
        assert admin_body.get("summary_scope") == "all"
        assert admin_body.get("can_view_department_todos") is True
        admin_summary = admin_body["summary"]
        assert admin_summary["task_total"] >= 1
        assert admin_summary["task_open"] >= 1
        assert "report_submitted" in admin_summary
        assert "report_expected" in admin_summary
        dept_ids = {t["id"] for t in admin_body.get("department_tasks") or []}
        assert task_id in dept_ids


@pytest.mark.asyncio
async def test_delegate_enters_assignee_and_department_todos(monkeypatch):
    """交办后：进责任人待办、进管理者部门待办，并尝试钉钉通知。"""
    notices: list[str] = []

    async def _fake_notice(userid: str, title: str, content: str) -> None:
        notices.append(f"{userid}:{title}")

    monkeypatch.setattr("app.services.notify_pipeline.send_work_notice", _fake_notice)

    admin = await _make_user(role_codes=["admin", "dept_head"], name="交办管理员")
    target = await _make_user(role_codes=["member"], name="被交办人")
    # 绑定钉钉 userid，便于走工作通知
    async with async_session() as db:
        u = await db.get(User, target.id)
        assert u is not None
        u.dingtalk_user_id = f"dt_{target.id}"
        await db.commit()

    project = await _make_project(admin, target)

    async with _client() as client:
        atoken = await _login(client, admin.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {atoken}"},
            json={
                "project_id": project.id,
                "title": "交办进待办联调",
                "goal": "对方可见",
                "assignment_type": "delegated",
                "assignee_id": target.id,
            },
        )
        assert create.status_code == 200, create.text
        body = create.json()
        task_id = body["id"]
        assert body.get("synced_to_week_draft") is True
        assert body.get("dingtalk_notified") is True
        assert notices

        ttoken = await _login(client, target.username)
        ws_target = await client.get(
            "/api/personal/workspace",
            headers={"Authorization": f"Bearer {ttoken}"},
        )
        assert ws_target.status_code == 200
        my_ids = {t["id"] for t in ws_target.json()["my_tasks"]}
        assert task_id in my_ids

        ws_admin = await client.get(
            "/api/personal/workspace",
            headers={"Authorization": f"Bearer {atoken}"},
        )
        assert ws_admin.status_code == 200
        data = ws_admin.json()
        assert data.get("can_view_department_todos") is True
        dept_ids = {t["id"] for t in data.get("department_tasks") or []}
        assert task_id in dept_ids


@pytest.mark.asyncio
async def test_creator_can_edit_task_when_not_assignee():
    """创建人即使不是负责人，也能改标题；无关成员不能改。"""
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    creator = await _make_user(role_codes=["member"], name="创建人")
    assignee = await _make_user(role_codes=["member"], name="执行人")
    outsider = await _make_user(role_codes=["member"], name="旁观者")
    project = await _make_project(owner, creator, assignee, outsider)

    async with async_session() as db:
        task = Task(
            project_id=project.id,
            assignee_id=assignee.id,
            created_by_id=creator.id,
            assignment_type="delegated",
            title="原标题",
            goal="",
            result_text="",
            status="not_started",
            acceptance_status="accepted",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    async with _client() as client:
        ctoken = await _login(client, creator.username)
        ok = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {ctoken}"},
            json={"title": "创建人改过的标题"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["title"] == "创建人改过的标题"

        otoken = await _login(client, outsider.username)
        bad = await client.patch(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {otoken}"},
            json={"title": "旁观者乱改"},
        )
        assert bad.status_code == 403


@pytest.mark.asyncio
async def test_creator_can_delete_delegated_task_and_audit():
    """交办任务：创建人可删并留下审计日志；执行人不能删。"""
    owner = await _make_user(role_codes=["project_owner"], name="主负责人")
    creator = await _make_user(role_codes=["member"], name="创建人")
    assignee = await _make_user(role_codes=["member"], name="执行人")
    project = await _make_project(owner, creator, assignee)

    async with async_session() as db:
        task = Task(
            project_id=project.id,
            assignee_id=assignee.id,
            created_by_id=creator.id,
            assignment_type="delegated",
            title="待删除的交办",
            goal="",
            result_text="",
            status="not_started",
            acceptance_status="accepted",
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    async with _client() as client:
        atoken = await _login(client, assignee.username)
        denied = await client.request(
            "DELETE",
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {atoken}"},
            json={"password": PASSWORD},
        )
        assert denied.status_code == 403

        ctoken = await _login(client, creator.username)
        wrong = await client.request(
            "DELETE",
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {ctoken}"},
            json={"password": "wrong-password"},
        )
        assert wrong.status_code == 400

        ok = await client.request(
            "DELETE",
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {ctoken}"},
            json={"password": PASSWORD},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json().get("ok") is True

        gone = await client.get(
            f"/api/tasks/{task_id}",
            headers={"Authorization": f"Bearer {ctoken}"},
        )
        assert gone.status_code == 404

    async with async_session() as db:
        row = (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.resource == "task", AuditLog.resource_id == task_id)
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert row is not None
        assert row.action == "delete"
        assert row.user_id == creator.id
        assert row.detail and "待删除的交办" in row.detail
        assert "交办" in row.detail


@pytest.mark.asyncio
async def test_admin_and_dept_head_can_delete_others_task():
    """管理员（admin / dept_head）可删除他人创建的任务；普通成员不行。"""
    owner = await _make_user(role_codes=["project_owner"], name="负责人")
    creator = await _make_user(role_codes=["member"], name="任务创建人")
    member = await _make_user(role_codes=["member"], name="普通成员")
    admin = await _make_user(role_codes=["admin"], name="系统管理员")
    dept = await _make_user(role_codes=["dept_head"], name="部门负责人")
    project = await _make_project(owner, creator)

    async def _seed_task(title: str) -> int:
        async with async_session() as db:
            task = Task(
                project_id=project.id,
                assignee_id=creator.id,
                created_by_id=creator.id,
                assignment_type="self",
                title=title,
                goal="",
                result_text="",
                status="not_started",
                acceptance_status="accepted",
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            return task.id

    task_for_member = await _seed_task(f"成员不可删_{uuid4().hex[:6]}")
    task_for_admin = await _seed_task(f"管理员可删_{uuid4().hex[:6]}")
    task_for_dept = await _seed_task(f"负责人可删_{uuid4().hex[:6]}")

    async with _client() as client:
        mtoken = await _login(client, member.username)
        denied = await client.request(
            "DELETE",
            f"/api/tasks/{task_for_member}",
            headers={"Authorization": f"Bearer {mtoken}"},
            json={"password": PASSWORD},
        )
        assert denied.status_code == 403

        atoken = await _login(client, admin.username)
        admin_ok = await client.request(
            "DELETE",
            f"/api/tasks/{task_for_admin}",
            headers={"Authorization": f"Bearer {atoken}"},
            json={"password": PASSWORD},
        )
        assert admin_ok.status_code == 200, admin_ok.text

        dtoken = await _login(client, dept.username)
        dept_ok = await client.request(
            "DELETE",
            f"/api/tasks/{task_for_dept}",
            headers={"Authorization": f"Bearer {dtoken}"},
            json={"password": PASSWORD},
        )
        assert dept_ok.status_code == 200, dept_ok.text

    async with async_session() as db:
        for tid, uid in ((task_for_admin, admin.id), (task_for_dept, dept.id)):
            row = (
                await db.execute(
                    select(AuditLog)
                    .where(AuditLog.resource == "task", AuditLog.resource_id == tid)
                    .order_by(AuditLog.id.desc())
                )
            ).scalars().first()
            assert row is not None
            assert row.action == "delete"
            assert row.user_id == uid


@pytest.mark.asyncio
async def test_related_tasks_visible_to_creator_not_stranger():
    """任务管理：创建人能看到自己交办出去的任务，无关的人看不到。"""
    owner = await _make_user(role_codes=["project_owner"], name="交办人")
    target = await _make_user(role_codes=["member"], name="执行人")
    stranger = await _make_user(role_codes=["member"], name="无关人")
    project = await _make_project(owner, target)

    async with _client() as client:
        otoken = await _login(client, owner.username)
        created = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "交办给执行人",
                "assignment_type": "delegated",
                "assignee_id": target.id,
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]

        mine = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {otoken}"},
            params={"page_size": 100, "search": "交办给执行人"},
        )
        assert mine.status_code == 200, mine.text
        assert any(t["id"] == task_id for t in mine.json()["items"])

        ttoken = await _login(client, target.username)
        assigned = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {ttoken}"},
            params={"page_size": 100, "search": "交办给执行人"},
        )
        assert assigned.status_code == 200, assigned.text
        assert any(t["id"] == task_id for t in assigned.json()["items"])

        stoken = await _login(client, stranger.username)
        others = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {stoken}"},
            params={"page_size": 100, "search": "交办给执行人"},
        )
        assert others.status_code == 200, others.text
        assert all(t["id"] != task_id for t in others.json()["items"])


@pytest.mark.asyncio
async def test_admin_and_dept_head_see_all_tasks():
    """管理员、部门负责人能看到别人的任务；普通成员看不到无关任务。"""
    owner = await _make_user(role_codes=["project_owner"], name="立项人")
    target = await _make_user(role_codes=["member"], name="干活的人")
    admin = await _make_user(role_codes=["admin"], name="系统管理员")
    head = await _make_user(role_codes=["dept_head"], name="部门负责人")
    stranger = await _make_user(role_codes=["member"], name="路人")
    project = await _make_project(owner, target)

    async with _client() as client:
        otoken = await _login(client, owner.username)
        created = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "别人的交办",
                "assignment_type": "delegated",
                "assignee_id": target.id,
            },
        )
        assert created.status_code == 200, created.text
        task_id = created.json()["id"]

        for user in (admin, head):
            token = await _login(client, user.username)
            resp = await client.get(
                "/api/tasks/related",
                headers={"Authorization": f"Bearer {token}"},
                params={"page_size": 100, "search": "别人的交办"},
            )
            assert resp.status_code == 200, resp.text
            assert any(t["id"] == task_id for t in resp.json()["items"])

        stoken = await _login(client, stranger.username)
        hidden = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {stoken}"},
            params={"page_size": 100, "search": "别人的交办"},
        )
        assert hidden.status_code == 200, hidden.text
        assert all(t["id"] != task_id for t in hidden.json()["items"])


@pytest.mark.asyncio
async def test_related_tasks_search_assignee_priority_filters():
    """任务管理支持按标题、负责人、级别筛选；非法级别 400；可见性不被绕过。"""
    owner = await _make_user(role_codes=["project_owner"], name="筛选创建人")
    a = await _make_user(role_codes=["member"], name="负责人甲")
    b = await _make_user(role_codes=["member"], name="负责人乙")
    stranger = await _make_user(role_codes=["member"], name="路人丙")
    project = await _make_project(owner, a, b)

    async with _client() as client:
        otoken = await _login(client, owner.username)

        t1 = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "甲的重点接口联调",
                "assignment_type": "delegated",
                "assignee_id": a.id,
                "priority": "p0",
            },
        )
        assert t1.status_code == 200, t1.text
        id1 = t1.json()["id"]

        t2 = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "乙的日常文档整理",
                "assignment_type": "delegated",
                "assignee_id": b.id,
                "priority": "p3",
            },
        )
        assert t2.status_code == 200, t2.text
        id2 = t2.json()["id"]

        by_search = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {otoken}"},
            params={"status": "all", "search": "接口联调"},
        )
        assert by_search.status_code == 200, by_search.text
        ids = {t["id"] for t in by_search.json()["items"]}
        assert id1 in ids
        assert id2 not in ids

        by_assignee = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {otoken}"},
            params={"status": "all", "assignee_id": b.id},
        )
        assert by_assignee.status_code == 200, by_assignee.text
        ids = {t["id"] for t in by_assignee.json()["items"]}
        assert id2 in ids
        assert id1 not in ids

        by_priority = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {otoken}"},
            params={"status": "all", "priority": "p0"},
        )
        assert by_priority.status_code == 200, by_priority.text
        ids = {t["id"] for t in by_priority.json()["items"]}
        assert id1 in ids
        assert id2 not in ids

        bad = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {otoken}"},
            params={"status": "all", "priority": "p9"},
        )
        assert bad.status_code == 400

        stoken = await _login(client, stranger.username)
        sneak = await client.get(
            "/api/tasks/related",
            headers={"Authorization": f"Bearer {stoken}"},
            params={"status": "all", "search": "接口联调", "assignee_id": a.id},
        )
        assert sneak.status_code == 200, sneak.text
        assert all(t["id"] not in {id1, id2} for t in sneak.json()["items"])


@pytest.mark.asyncio
async def test_related_tasks_pagination_default_all():
    """任务管理分页：默认全部、默认每页 10；page_size 可调。"""
    owner = await _make_user(role_codes=["project_owner"], name="分页创建人")
    project = await _make_project(owner)

    async with _client() as client:
        token = await _login(client, owner.username)
        headers = {"Authorization": f"Bearer {token}"}
        created_ids: list[int] = []
        for i in range(12):
            resp = await client.post(
                "/api/tasks",
                headers=headers,
                json={
                    "project_id": project.id,
                    "title": f"分页任务_{i}_{uuid4().hex[:4]}",
                    "assignment_type": "self",
                },
            )
            assert resp.status_code == 200, resp.text
            created_ids.append(resp.json()["id"])

        default = await client.get("/api/tasks/related", headers=headers)
        assert default.status_code == 200, default.text
        body = default.json()
        assert body["page"] == 1
        assert body["page_size"] == 10
        assert body["total"] >= 12
        assert len(body["items"]) == 10
        assert set(created_ids) & {t["id"] for t in body["items"]}

        page2 = await client.get(
            "/api/tasks/related",
            headers=headers,
            params={"page": 2, "page_size": 10},
        )
        assert page2.status_code == 200, page2.text
        p2 = page2.json()
        assert p2["page"] == 2
        assert len(p2["items"]) >= 2
        page1_ids = {t["id"] for t in body["items"]}
        page2_ids = {t["id"] for t in p2["items"]}
        assert page1_ids.isdisjoint(page2_ids)

        big = await client.get(
            "/api/tasks/related",
            headers=headers,
            params={"page_size": 50},
        )
        assert big.status_code == 200, big.text
        assert big.json()["page_size"] == 50
        assert len(big.json()["items"]) >= 12
        assert {t["id"] for t in big.json()["items"]} >= set(created_ids)
