"""交办闭环：完成通知、结果回写周报、附件上传。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import AsyncMock, patch
from uuid import uuid4

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
    TaskAttachment,
    User,
    UserRole,
)
from app.services.init_service import init_rbac
from app.services.report_quality import monday_of

PASSWORD = "CloseLoop2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"cl_{uuid4().hex[:10]}"
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


async def _make_project(owner: User, *members: User) -> Project:
    async with async_session() as db:
        project = Project(
            name=f"cl_proj_{uuid4().hex[:8]}",
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
            db.add(
                ProjectMember(
                    project_id=project.id, user_id=m.id, member_role="member"
                )
            )
        await db.commit()
        await db.refresh(project)
        return project


@pytest.mark.asyncio
async def test_complete_delegated_notifies_creator_and_syncs_week_draft():
    owner = await _make_user(role_codes=["project_owner"], name="交办人甲")
    member = await _make_user(role_codes=["member"], name="责任人乙")
    project = await _make_project(owner, member)
    week_start = monday_of(date.today())

    async with async_session() as db:
        reports = (
            await db.execute(
                select(ProgressReport).where(
                    ProgressReport.user_id == member.id,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start == week_start,
                )
            )
        ).scalars().all()
        for r in reports:
            await db.delete(r)
        await db.commit()

    async with _client() as client:
        otoken = await _login(client, owner.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "闭环完成通知测试",
                "assignment_type": "delegated",
                "assignee_id": member.id,
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]

        mtoken = await _login(client, member.username)
        accept = await client.post(
            f"/api/tasks/{task_id}/accept",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert accept.status_code == 200, accept.text

        with patch(
            "app.api.tasks.notify_task_completed",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_notify:
            done = await client.patch(
                f"/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {mtoken}"},
                json={"status": "completed", "result_text": "已提交验收材料"},
            )
            assert done.status_code == 200, done.text
            body = done.json()
            assert body["status"] == "completed"
            assert body["synced_to_week_draft"] is True
            assert body["dingtalk_notified"] is True
            mock_notify.assert_awaited_once()

    async with async_session() as db:
        item = (
            await db.execute(
                select(ProgressReportItem)
                .join(ProgressReport)
                .where(
                    ProgressReport.user_id == member.id,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start == week_start,
                    ProgressReportItem.linked_task_id == task_id,
                )
            )
        ).scalar_one()
        assert "已提交验收材料" in (item.result_text or "")


@pytest.mark.asyncio
async def test_complete_when_week_submitted_writes_next_week():
    owner = await _make_user(role_codes=["project_owner"], name="交办人丙")
    member = await _make_user(role_codes=["member"], name="责任人丁")
    project = await _make_project(owner, member)
    week_start = monday_of(date.today())
    next_week = week_start + timedelta(days=7)

    async with async_session() as db:
        # 清掉相关周报
        for ws in (week_start, next_week):
            reports = (
                await db.execute(
                    select(ProgressReport).where(
                        ProgressReport.user_id == member.id,
                        ProgressReport.period_type == "week",
                        ProgressReport.period_start == ws,
                    )
                )
            ).scalars().all()
            for r in reports:
                await db.delete(r)
        await db.commit()

        submitted = ProgressReport(
            user_id=member.id,
            period_type="week",
            period_start=week_start,
            period_end=week_start + timedelta(days=6),
            theme="",
            status="submitted",
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(submitted)
        await db.commit()

    async with _client() as client:
        otoken = await _login(client, owner.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "跨周完成写下周",
                "assignment_type": "delegated",
                "assignee_id": member.id,
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]

        mtoken = await _login(client, member.username)
        await client.post(
            f"/api/tasks/{task_id}/accept",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        with patch(
            "app.api.tasks.notify_task_completed",
            new_callable=AsyncMock,
            return_value=False,
        ):
            done = await client.patch(
                f"/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {mtoken}"},
                json={"status": "completed", "result_text": "跨周完成结果"},
            )
        assert done.status_code == 200, done.text
        assert done.json()["synced_to_week_draft"] is True

    async with async_session() as db:
        item = (
            await db.execute(
                select(ProgressReportItem)
                .join(ProgressReport)
                .where(
                    ProgressReport.user_id == member.id,
                    ProgressReport.period_start == next_week,
                    ProgressReportItem.linked_task_id == task_id,
                )
            )
        ).scalar_one()
        assert "跨周完成结果" in (item.result_text or "")
        assert "交办完成" in (item.tags or [])


@pytest.mark.asyncio
async def test_task_attachment_upload_list_delete():
    owner = await _make_user(role_codes=["project_owner"], name="交办人戊")
    member = await _make_user(role_codes=["member"], name="责任人己")
    project = await _make_project(owner, member)

    async with _client() as client:
        otoken = await _login(client, owner.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {otoken}"},
            json={
                "project_id": project.id,
                "title": "附件佐证测试",
                "assignment_type": "delegated",
                "assignee_id": member.id,
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]

        mtoken = await _login(client, member.username)
        # 未接收不能上传
        blocked = await client.post(
            f"/api/tasks/{task_id}/attachments",
            headers={"Authorization": f"Bearer {mtoken}"},
            files={"file": ("shot.png", BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")},
        )
        assert blocked.status_code == 400

        await client.post(
            f"/api/tasks/{task_id}/accept",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        uploaded = await client.post(
            f"/api/tasks/{task_id}/attachments",
            headers={"Authorization": f"Bearer {mtoken}"},
            files={"file": ("shot.png", BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        att = uploaded.json()
        assert att["original_filename"] == "shot.png"
        att_id = att["id"]

        listed = await client.get(
            f"/api/tasks/{task_id}/attachments",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert listed.status_code == 200
        assert any(x["id"] == att_id for x in listed.json())

        dl = await client.get(
            f"/api/tasks/{task_id}/attachments/{att_id}/download",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert dl.status_code == 200
        assert dl.content.startswith(b"\x89PNG")

        deleted = await client.delete(
            f"/api/tasks/{task_id}/attachments/{att_id}",
            headers={"Authorization": f"Bearer {mtoken}"},
        )
        assert deleted.status_code == 200

    async with async_session() as db:
        left = (
            await db.execute(
                select(TaskAttachment).where(TaskAttachment.id == att_id)
            )
        ).scalar_one_or_none()
        assert left is None


@pytest.mark.asyncio
async def test_self_task_complete_does_not_require_creator_notify():
    member = await _make_user(role_codes=["member"], name="自建人")
    project = await _make_project(member)

    async with _client() as client:
        token = await _login(client, member.username)
        create = await client.post(
            "/api/tasks",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "project_id": project.id,
                "title": "自建完成",
                "assignment_type": "self",
            },
        )
        assert create.status_code == 200, create.text
        task_id = create.json()["id"]
        with patch(
            "app.api.tasks.notify_task_completed",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_notify:
            done = await client.patch(
                f"/api/tasks/{task_id}",
                headers={"Authorization": f"Bearer {token}"},
                json={"status": "completed", "result_text": "自己做完了"},
            )
            assert done.status_code == 200, done.text
            mock_notify.assert_awaited_once()
            assert done.json()["dingtalk_notified"] is False
