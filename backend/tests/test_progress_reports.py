"""主辅角色与周报轻量提交集成测试。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac


async def _ensure_users():
    async with async_session() as db:
        await init_rbac(db)
        for username, name, role_code in [
            ("t_mgr", "测试经理", "dept_head"),
            ("t_lead", "测试主责", "project_owner"),
            ("t_assist", "测试辅助", "member"),
        ]:
            r = await db.execute(select(User).where(User.username == username))
            u = r.scalar_one_or_none()
            if not u:
                u = User(
                    username=username,
                    name=name,
                    hashed_password=hash_password("TestPass2026"),
                    must_change_password=False,
                    is_active=True,
                )
                db.add(u)
                await db.flush()
            else:
                u.hashed_password = hash_password("TestPass2026")
                u.must_change_password = False
            role = (
                await db.execute(select(Role).where(Role.code == role_code))
            ).scalar_one()
            ur = await db.execute(
                select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
            )
            if not ur.scalar_one_or_none():
                db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()


async def _login(client: AsyncClient, username: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "TestPass2026"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_set_lead_and_assistants_and_submit_light_report():
    await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        mgr_token = await _login(client, "t_mgr")
        lead_token = await _login(client, "t_lead")
        assist_token = await _login(client, "t_assist")

        async with async_session() as db:
            lead = (await db.execute(select(User).where(User.username == "t_lead"))).scalar_one()
            lead.name = "许金斌"
            assist = (
                await db.execute(select(User).where(User.username == "t_assist"))
            ).scalar_one()
            support = (
                await db.execute(select(User).where(User.username == "t_support"))
            ).scalar_one_or_none()
            if not support:
                support = User(
                    username="t_support",
                    name="测试支撑",
                    hashed_password=hash_password("TestPass2026"),
                    must_change_password=False,
                    is_active=True,
                    job_title="技术经理",
                )
                db.add(support)
            else:
                support.job_title = "技术经理"
                support.is_active = True
            await db.flush()
            project = Project(
                name="周报测试项目",
                project_type="software",
                level="normal",
                owner_id=lead.id,
            )
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=lead.id, member_role="lead"))
            await db.commit()
            pid = project.id
            lead_id = lead.id
            assist_id = assist.id
            support_id = support.id

        # 设辅助人
        resp = await client.put(
            f"/api/projects/{pid}/members",
            headers={"Authorization": f"Bearer {mgr_token}"},
            json={
                "lead_user_id": lead_id,
                "support_user_id": support_id,
                "assistant_user_ids": [assist_id],
                "member_user_ids": [],
            },
        )
        assert resp.status_code == 200, resp.text
        roles = {m["user"]["id"]: m["member_role"] for m in resp.json()}
        assert roles[lead_id] == "lead"
        assert roles[assist_id] == "assistant"

        # 空话应被拒
        bad = await client.post(
            "/api/progress-reports",
            headers={"Authorization": f"Bearer {assist_token}"},
            json={
                "period_type": "week",
                "period_start": "2099-03-02",
                "period_end": "2099-03-08",
                "submit": True,
                "items": [
                    {
                        "task_title": "接入跟进",
                        "result_text": "跟进项目进度",
                        "next_goal": "继续跟进",
                        "next_due_date": "2099-03-10",
                        "stuck_status": "none",
                        "risk_level": "normal",
                    }
                ],
            },
        )
        assert bad.status_code == 400

        # 合法三问可提交（须带 project_id；用远期唯一周期，避免撞上「历史已交需解锁」）
        ok = await client.post(
            "/api/progress-reports",
            headers={"Authorization": f"Bearer {assist_token}"},
            json={
                "period_type": "week",
                "period_start": "2099-03-02",
                "period_end": "2099-03-06",
                "submit": True,
                "items": [
                    {
                        "project_id": project.id,
                        "task_title": "监控接入",
                        "result_text": "新接入 20 路监控",
                        "quant_value": "20",
                        "next_goal": "完成剩余 10 路接入",
                        "next_due_date": "2099-03-10",
                        "stuck_status": "none",
                        "risk_level": "normal",
                    }
                ],
            },
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["status"] == "submitted"
        assert body["period_end"] == "2099-03-06"
        assert body["theme"] == "监控接入"
        assert body["items"][0]["project_id"] == project.id
        assert body["items"][0]["task_title"] == "监控接入"

        board = await client.get(
            "/api/progress-reports/board?period_type=week&period_start=2099-03-02",
            headers={"Authorization": f"Bearer {mgr_token}"},
        )
        assert board.status_code == 200, board.text
        bj = board.json()
        # 按项目应能看到已挂项目的周报；按人名单仅含固定 11 人团队
        assert any(p["project_id"] == project.id for p in bj.get("projects") or [])
        hit = next(p for p in bj["projects"] if p["project_id"] == project.id)
        assert any(r.get("author", {}).get("username") == "t_assist" for r in hit.get("reports") or [])
