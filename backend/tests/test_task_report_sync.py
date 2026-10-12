"""任务保存 → 自动写入本周周报草稿；本人可读周报/任务评价。"""

from datetime import date, timedelta

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
from app.services.report_quality import monday_of


async def _ensure_member(username: str = "t_task_report"):
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == username))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username=username,
                name="任务周报测试",
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
        return u.id


async def _ensure_project(owner_id: int, member_id: int) -> int:
    async with async_session() as db:
        p = Project(
            name=f"任务周报联调-{date.today().isoformat()}",
            project_type="software",
            level="normal",
            owner_id=owner_id,
            status="normal",
        )
        db.add(p)
        await db.flush()
        for uid, role in ((owner_id, "lead"), (member_id, "member")):
            exists = await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == p.id, ProjectMember.user_id == uid
                )
            )
            if not exists.scalar_one_or_none():
                db.add(
                    ProjectMember(
                        project_id=p.id, user_id=uid, member_role=role
                    )
                )
        await db.commit()
        return p.id


@pytest.mark.asyncio
async def test_create_self_task_appends_week_draft_item():
    uid = await _ensure_member()
    pid = await _ensure_project(uid, uid)
    week_start = monday_of(date.today())

    async with async_session() as db:
        # 清掉本期草稿，保证从空开始
        reports = (
            await db.execute(
                select(ProgressReport).where(
                    ProgressReport.user_id == uid,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start == week_start,
                )
            )
        ).scalars().all()
        for r in reports:
            await db.delete(r)
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "t_task_report", "password": "TestPass2026"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        due = (date.today() + timedelta(days=3)).isoformat()
        create = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": pid,
                "title": "联调任务-自动进周报",
                "goal": "完成接口联调并自测通过",
                "due_date": due,
                "assignment_type": "self",
                "priority": "p2",
            },
        )
        assert create.status_code == 200, create.text
        task = create.json()
        assert task["title"] == "联调任务-自动进周报"
        assert task.get("goal") == "完成接口联调并自测通过"
        task_id = task["id"]

        mine = await client.get(
            "/api/progress-reports/mine",
            headers=headers,
            params={"period_type": "week"},
        )
        assert mine.status_code == 200
        report = mine.json()
        assert report is not None
        assert report["status"] == "draft"
        items = report.get("items") or []
        linked = [it for it in items if it.get("linked_task_id") == task_id]
        assert len(linked) == 1
        assert linked[0]["task_title"] == "联调任务-自动进周报"
        assert linked[0]["project_id"] == pid
        assert "完成接口联调" in (linked[0].get("next_goal") or "")
        assert task.get("synced_to_week_draft") is True

        # 再创建同任务不会重复（同一 task 只一条）
        create2 = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": pid,
                "title": "联调任务-自动进周报-二次",
                "goal": "二次目标",
                "assignment_type": "self",
                "priority": "p3",
            },
        )
        assert create2.status_code == 200
        task2_id = create2.json()["id"]
        mine2 = await client.get(
            "/api/progress-reports/mine",
            headers=headers,
            params={"period_type": "week"},
        )
        items2 = mine2.json().get("items") or []
        assert len([it for it in items2 if it.get("linked_task_id") == task_id]) == 1
        assert len([it for it in items2 if it.get("linked_task_id") == task2_id]) == 1


@pytest.mark.asyncio
async def test_member_can_read_own_report_review(monkeypatch):
    async def _no_llm(*args, **kwargs):
        return None, None

    monkeypatch.setattr("app.services.report_ai_review.run_structured", _no_llm)

    uid = await _ensure_member("t_review_self")
    week_start = monday_of(date.today())
    week_end = week_start + timedelta(days=6)

    async with async_session() as db:
        old = (
            await db.execute(
                select(ProgressReport).where(
                    ProgressReport.user_id == uid,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start == week_start,
                )
            )
        ).scalars().all()
        for r in old:
            await db.delete(r)
        await db.flush()
        report = ProgressReport(
            user_id=uid,
            period_type="week",
            period_start=week_start,
            period_end=week_end,
            theme="自测",
            status="submitted",
        )
        db.add(report)
        await db.flush()
        db.add(
            ProgressReportItem(
                report_id=report.id,
                task_title="已提交任务卡",
                result_text="完成了联调文档 3 页",
                quant_value="3页",
                next_goal="下周推进评审",
                stuck_status="none",
                risk_level="normal",
                sort_order=0,
            )
        )
        await db.commit()
        report_id = report.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "t_review_self", "password": "TestPass2026"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 员工不需要 report.view.all
        res = await client.get(
            f"/api/progress-reports/{report_id}/my-review",
            headers=headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["summary"]
        assert body["report_id"] == report_id

        # mine 也应带回评价（已提交）
        mine = await client.get(
            "/api/progress-reports/mine",
            headers=headers,
            params={"period_type": "week", "period_start": week_start.isoformat()},
        )
        assert mine.status_code == 200
        data = mine.json()
        assert data is not None
        assert data.get("phase1_review") is not None
        assert data["phase1_review"]["summary"]


@pytest.mark.asyncio
async def test_member_can_read_own_task_review(monkeypatch):
    async def _no_llm(*args, **kwargs):
        return None, None

    monkeypatch.setattr("app.services.task_ai_review.run_structured", _no_llm)

    uid = await _ensure_member("t_task_review")
    pid = await _ensure_project(uid, uid)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "t_task_review", "password": "TestPass2026"},
        )
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        create = await client.post(
            "/api/tasks",
            headers=headers,
            json={
                "project_id": pid,
                "title": "需要评价的任务",
                "goal": "目标写清楚",
                "assignment_type": "self",
                "priority": "p1",
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]

        rev = await client.get(f"/api/tasks/{task_id}/review", headers=headers)
        assert rev.status_code == 200, rev.text
        body = rev.json()
        assert body["summary"]
        assert body["task_id"] == task_id
        assert body.get("suggestions") is not None

        # 个人评价聚合可读
        mine = await client.get("/api/personal/reviews", headers=headers)
        assert mine.status_code == 200, mine.text
        agg = mine.json()
        assert "report_reviews" in agg and "task_reviews" in agg
        assert any(t["task_id"] == task_id for t in agg["task_reviews"])
