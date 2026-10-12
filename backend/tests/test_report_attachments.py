"""周报/日报附件上传权限测试。"""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac

PASSWORD = "ReportAttach2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"ra_{uuid4().hex[:10]}"
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
async def test_report_attachment_author_upload_outsider_denied_viewer_download():
    owner = await _make_user(role_codes=["project_owner", "dept_head"], name="负责人")
    author = await _make_user(role_codes=["member"], name="作者")
    outsider = await _make_user(role_codes=["member"], name="旁观者")
    project = await _make_project(owner, author, outsider)

    async with _client() as client:
        atoken = await _login(client, author.username)
        headers = {"Authorization": f"Bearer {atoken}"}
        created = await client.post(
            "/api/progress-reports",
            headers=headers,
            json={
                "period_type": "day",
                "period_start": "2026-09-19",
                "period_end": "2026-09-19",
                "submit": True,
                "items": [
                    {
                        "project_id": project.id,
                        "task_title": "附件测试",
                        "result_text": "做成了",
                        "stuck_status": "none",
                        "next_goal": "明天",
                        "next_due_date": "2026-09-20",
                        "sort_order": 0,
                    }
                ],
            },
        )
        assert created.status_code == 200, created.text
        report_id = created.json()["id"]

        files = {"file": ("proof.txt", BytesIO(b"hello proof"), "text/plain")}
        up = await client.post(
            f"/api/progress-reports/{report_id}/attachments",
            headers=headers,
            files=files,
        )
        assert up.status_code == 200, up.text
        att_id = up.json()["id"]

        mine = await client.get(
            "/api/progress-reports/mine",
            headers=headers,
            params={"period_type": "day", "period_start": "2026-09-19"},
        )
        assert mine.status_code == 200
        assert any(a["id"] == att_id for a in mine.json().get("attachments") or [])

        otoken = await _login(client, outsider.username)
        oh = {"Authorization": f"Bearer {otoken}"}
        bad = await client.post(
            f"/api/progress-reports/{report_id}/attachments",
            headers=oh,
            files={"file": ("x.txt", BytesIO(b"x"), "text/plain")},
        )
        assert bad.status_code == 403

        bad_dl = await client.get(
            f"/api/progress-reports/{report_id}/attachments/{att_id}/download",
            headers=oh,
        )
        assert bad_dl.status_code == 403

        mtoken = await _login(client, owner.username)
        mh = {"Authorization": f"Bearer {mtoken}"}
        ok_dl = await client.get(
            f"/api/progress-reports/{report_id}/attachments/{att_id}/download",
            headers=mh,
        )
        assert ok_dl.status_code == 200
        assert ok_dl.content == b"hello proof"
