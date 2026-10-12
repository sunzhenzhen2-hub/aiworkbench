"""周报下周计划同步本人待办（不再复制给管理员）。"""

from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, Task, User, UserRole
from app.services.init_service import init_rbac
from app.services.project_service import seed_standard_phases
from app.services.report_next_plan import SOURCE_REPORT_DONE, SOURCE_REPORT_NEXT
from app.services.phase_match import guess_phase_name_from_text
from app.services.report_quality import monday_of, sunday_of
from app.api.progress_reports import _sync_submitted_report_todos


async def _ensure_admin():
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == "admin"))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username="admin",
                name="孙振振",
                hashed_password=hash_password("InitAdmin2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(u)
            await db.flush()
        else:
            u.name = "孙振振"
            u.is_active = True
            u.must_change_password = False
            u.hashed_password = hash_password("InitAdmin2026")
        role = (await db.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=role.id))
        dept = (await db.execute(select(Role).where(Role.code == "dept_head"))).scalar_one()
        ur2 = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == dept.id)
        )
        if not ur2.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=dept.id))
        await db.commit()
        return u.id


async def _ensure_user():
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == "t_nextplan"))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username="t_nextplan",
                name="下周计划测试",
                hashed_password=hash_password("TestPass2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(u)
            await db.flush()
        else:
            u.hashed_password = hash_password("TestPass2026")
            u.must_change_password = False
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=role.id))
        tasks = (
            await db.execute(
                select(Task).where(
                    Task.assignee_id == u.id,
                    Task.source.in_([SOURCE_REPORT_NEXT, SOURCE_REPORT_DONE]),
                )
            )
        ).scalars().all()
        for t in tasks:
            await db.delete(t)
        await db.commit()
        return u.id


@pytest.mark.asyncio
async def test_submit_week_report_creates_and_updates_next_plan_todos(monkeypatch):
    """无 LLM 时走规则兜底；提交建待办，再提交不翻倍；不复制给管理员。"""

    async def _no_llm(db, purpose: str = "report_coach"):
        return None

    monkeypatch.setattr("app.services.report_next_plan.resolve_llm", _no_llm)

    admin_id = await _ensure_admin()
    uid = await _ensure_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "t_nextplan", "password": "TestPass2026"},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        async with async_session() as db:
            user = (await db.execute(select(User).where(User.id == uid))).scalar_one()
            project = Project(
                name="下周计划测试项目",
                project_type="software",
                level="normal",
                owner_id=user.id,
            )
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, member_role="lead"))
            await seed_standard_phases(db, project.id)
            await db.commit()
            pid = project.id

        week_start = monday_of(date.today())
        week_end = sunday_of(week_start)
        due1 = week_end
        due2 = week_start
        due1_update = week_end

        body = {
            "period_type": "week",
            "period_start": week_start.isoformat(),
            "period_end": week_end.isoformat(),
            "theme": "",
            "submit": True,
            "items": [
                {
                    "project_id": pid,
                    "task_title": "热成像跟进",
                    "result_text": "本周完成点位勘察与设计沟通",
                    "next_goal": "继续跟进热成像到货",
                    "next_due_date": due1.isoformat(),
                    "stuck_status": "none",
                    "risk_level": "normal",
                    "sort_order": 0,
                },
                {
                    "project_id": pid,
                    "task_title": "方案汇报",
                    "result_text": "初稿已出",
                    "next_goal": "完成方案终稿并提交客户",
                    "next_due_date": due2.isoformat(),
                    "stuck_status": "none",
                    "risk_level": "normal",
                    "sort_order": 1,
                },
            ],
        }
        created = await client.post("/api/progress-reports", headers=headers, json=body)
        assert created.status_code == 200, created.text
        data = created.json()
        assert data["status"] == "submitted"
        # 提交接口已改为后台同步待办，响应里 next_plan_synced/done_synced 恒为 0
        assert data.get("next_plan_synced", 0) == 0
        assert data.get("done_synced", 0) == 0
        # 测试里显式跑一遍后台任务，避免依赖 ASGI BackgroundTasks 时机
        await _sync_submitted_report_todos(data["id"], uid)

        async with async_session() as db:
            n_author = (
                await db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.assignee_id == uid,
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.source_report_id == data["id"],
                    )
                )
            ).scalar()
            assert n_author == 2
            n_done = (
                await db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.assignee_id == uid,
                        Task.source == SOURCE_REPORT_DONE,
                        Task.source_report_id == data["id"],
                        Task.status == "completed",
                    )
                )
            ).scalar()
            assert n_done == 2
            design_done = (
                await db.execute(
                    select(Task).where(
                        Task.assignee_id == uid,
                        Task.source == SOURCE_REPORT_DONE,
                        Task.source_report_id == data["id"],
                        Task.source_ref == "done:idx:0",
                    )
                )
            ).scalar_one()
            assert design_done.phase_id is not None
            assert design_done.status == "completed"
            n_admin = (
                await db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.assignee_id == admin_id,
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.source_report_id == data["id"],
                    )
                )
            ).scalar()
            assert n_admin == 0

        # 管理员部门待办应能看到成员这两条
        admin_login = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "InitAdmin2026"},
        )
        assert admin_login.status_code == 200, admin_login.text
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        ws = await client.get("/api/personal/workspace", headers=admin_headers)
        assert ws.status_code == 200, ws.text
        ws_data = ws.json()
        assert ws_data["can_view_department_todos"] is True
        dept_ids = {t["id"] for t in ws_data["department_tasks"]}
        async with async_session() as db:
            author_task_ids = {
                t.id
                for t in (
                    await db.execute(
                        select(Task).where(
                            Task.source_report_id == data["id"],
                            Task.source == SOURCE_REPORT_NEXT,
                            Task.assignee_id == uid,
                        )
                    )
                ).scalars().all()
            }
        assert author_task_ids.issubset(dept_ids)

        # 再提交：更新不翻倍（本周已交仍可直接改写）
        body["items"][0]["next_goal"] = "确认热成像三套到货时间并反馈"
        body["items"][0]["next_due_date"] = due1_update.isoformat()
        again = await client.post("/api/progress-reports", headers=headers, json=body)
        assert again.status_code == 200, again.text
        again_data = again.json()
        assert again_data.get("next_plan_synced", 0) == 0
        await _sync_submitted_report_todos(again_data["id"], uid)

        async with async_session() as db:
            n2 = (
                await db.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.assignee_id == uid,
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.source_report_id == again_data["id"],
                    )
                )
            ).scalar()
            assert n2 == 2
            t0 = (
                await db.execute(
                    select(Task).where(
                        Task.source_report_id == again_data["id"],
                        Task.source_ref == "idx:0",
                        Task.assignee_id == uid,
                    )
                )
            ).scalar_one()
            assert t0.due_date == due1_update
            assert "热成像" in (t0.title + str(t0.checklist))


def test_guess_phase_name_from_text():
    assert guess_phase_name_from_text("完成需求调研") == "需求分析"
    assert guess_phase_name_from_text("本周做了设计评审") == "设计"
    # 「开发联调中」可能同时命中开发 / 联调；首项为别名更长或先匹配者
    assert guess_phase_name_from_text("开发联调中") in {"开发", "联调测试"}
    assert guess_phase_name_from_text("随便写点") is None
    # 「手表测试」不应仅因「测试」两字挂到测试阶段
    assert guess_phase_name_from_text("手表测试，对接厂家") is None
