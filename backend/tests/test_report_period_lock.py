"""周报周期归属：历史已交需 force_edit_submitted；本周可直接写。"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import ProgressReport, ProgressReportItem, Project, ProjectMember, Role, Task, User, UserRole
from app.services.init_service import init_rbac
from app.services.report_quality import monday_of, sunday_of


async def _ensure_user():
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == "t_period_lock"))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username="t_period_lock",
                name="周期锁测",
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
        proj = (
            await db.execute(select(Project).where(Project.name == "周期锁测试项目"))
        ).scalar_one_or_none()
        if not proj:
            proj = Project(
                name="周期锁测试项目",
                project_type="software",
                level="normal",
                owner_id=u.id,
            )
            db.add(proj)
            await db.flush()
            db.add(ProjectMember(project_id=proj.id, user_id=u.id, member_role="lead"))
        await db.commit()
        return u.id, proj.id


async def _login(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/auth/login",
        json={"username": "t_period_lock", "password": "TestPass2026"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _item(project_id: int, title: str = "本周进展核对") -> dict:
    return {
        "project_id": project_id,
        "task_title": title,
        "result_text": "完成接口联调与验收材料整理",
        "next_goal": "下周提交验收报告",
        "next_due_date": (date.today() + timedelta(days=3)).isoformat(),
        "stuck_status": "none",
        "risk_level": "normal",
        "no_action": False,
    }


@pytest.mark.asyncio
async def test_historical_submitted_requires_force_edit():
    user_id, project_id = await _ensure_user()
    this_monday = monday_of(date.today())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = sunday_of(last_monday)

    async with async_session() as db:
        # 清掉可能残留的本周/上周记录（先解绑任务再删，避免外键）
        for ws in (last_monday, this_monday):
            old = await db.execute(
                select(ProgressReport).where(
                    ProgressReport.user_id == user_id,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start == ws,
                )
            )
            report = old.scalar_one_or_none()
            if not report:
                continue
            tasks = (
                await db.execute(select(Task).where(Task.source_report_id == report.id))
            ).scalars().all()
            for t in tasks:
                t.source_report_id = None
            await db.flush()
            await db.delete(report)
        await db.flush()

        report = ProgressReport(
            user_id=user_id,
            period_type="week",
            period_start=last_monday,
            period_end=last_sunday,
            status="submitted",
            theme="上周已交",
        )
        db.add(report)
        await db.flush()
        db.add(
            ProgressReportItem(
                report_id=report.id,
                project_id=project_id,
                task_title="上周任务",
                result_text="上周已完成事项说明足够长",
                next_goal="本应下周继续",
                next_due_date=this_monday,
                stuck_status="none",
                risk_level="normal",
                sort_order=0,
            )
        )
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client)
        headers = {"Authorization": f"Bearer {token}"}

        # 本周 mine 为空（后端不自动切历史；前端也应停在本周）
        mine = await client.get(
            "/api/progress-reports/mine",
            headers=headers,
            params={
                "period_type": "week",
                "period_start": this_monday.isoformat(),
                "period_end": sunday_of(this_monday).isoformat(),
            },
        )
        assert mine.status_code == 200
        assert mine.json() is None

        # 无 force：改历史已交 → 409
        blocked = await client.post(
            "/api/progress-reports",
            headers=headers,
            json={
                "period_type": "week",
                "period_start": last_monday.isoformat(),
                "period_end": last_sunday.isoformat(),
                "submit": True,
                "items": [_item(project_id, "误写进上周")],
            },
        )
        assert blocked.status_code == 409, blocked.text

        # 带 force：可改
        forced = await client.post(
            "/api/progress-reports",
            headers=headers,
            json={
                "period_type": "week",
                "period_start": last_monday.isoformat(),
                "period_end": last_sunday.isoformat(),
                "submit": True,
                "force_edit_submitted": True,
                "items": [_item(project_id, "确认修改上周")],
            },
        )
        assert forced.status_code == 200, forced.text
        assert forced.json()["period_start"] == last_monday.isoformat()

        # 本周可直接新建/提交
        this_week_save = await client.post(
            "/api/progress-reports",
            headers=headers,
            json={
                "period_type": "week",
                "period_start": this_monday.isoformat(),
                "period_end": sunday_of(this_monday).isoformat(),
                "submit": True,
                "items": [_item(project_id, "正确写本周")],
            },
        )
        assert this_week_save.status_code == 200, this_week_save.text
        assert this_week_save.json()["period_start"] == this_monday.isoformat()
        assert this_week_save.json()["status"] == "submitted"
