"""通知管理：模板种子、管道日志、管理员 API。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from unittest.mock import AsyncMock, patch

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import (
    Notification,
    NotificationDeliveryLog,
    NotificationTemplate,
    Role,
    User,
    UserRole,
)
from app.services.notification_templates_seed import seed_notification_templates
from app.services.notify_pipeline import cron_matches, dispatch_notification, in_quiet_hours, render_template


async def _login(client: AsyncClient, username: str, password: str = "InitAdmin2026") -> str:
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def _ensure_admin() -> User:
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if admin:
            await seed_notification_templates(db)
            await db.commit()
            return admin
        admin = User(
            username="admin",
            name="系统管理员",
            hashed_password=hash_password("InitAdmin2026"),
            must_change_password=False,
            is_active=True,
        )
        db.add(admin)
        await db.flush()
        roles = await db.execute(select(Role).where(Role.code == "admin"))
        role = roles.scalar_one()
        db.add(UserRole(user_id=admin.id, role_id=role.id))
        await seed_notification_templates(db)
        await db.commit()
        return admin


def test_render_and_cron_and_quiet():
    assert render_template("你好{name}", {"name": "张三"}) == "你好张三"
    assert render_template("缺{missing}", {}) == "缺"
    assert cron_matches("0 9 * * *", datetime(2026, 10, 1, 9, 0))
    assert not cron_matches("0 9 * * *", datetime(2026, 10, 1, 9, 1))
    assert cron_matches("0 12 * * 1", datetime(2026, 9, 28, 12, 0))  # Monday
    assert not cron_matches("0 12 * * 1", datetime(2026, 9, 29, 12, 0))
    assert in_quiet_hours(time(23, 0), time(22, 0), time(8, 0))
    assert not in_quiet_hours(time(10, 0), time(22, 0), time(8, 0))


@pytest.mark.asyncio
async def test_dispatch_writes_delivery_log():
    await _ensure_admin()
    async with async_session() as db:
        u = User(
            username=f"t_notify_recv_{uuid4().hex[:8]}",
            name="通知接收",
            hashed_password=hash_password("Aa123456"),
            must_change_password=False,
            is_active=True,
            dingtalk_user_id=f"dt_notify_{uuid4().hex[:10]}",
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)

        with patch(
            "app.services.notify_pipeline.send_work_notice",
            new_callable=AsyncMock,
        ) as mock_ding:
            mock_ding.return_value = None
            result = await dispatch_notification(
                db,
                event_key="task_assigned",
                users=[u],
                variables={"actor_name": "主管", "task_title": "测试任务"},
                ref_type="task",
                ref_id=99901,
                fallback_title="收到交办任务，请接收",
                fallback_body="fallback",
                respect_quiet=False,
            )
            await db.commit()
            assert result["dingtalk_notified"] is True
            mock_ding.assert_awaited()

        logs = await db.execute(
            select(NotificationDeliveryLog).where(
                NotificationDeliveryLog.user_id == u.id,
                NotificationDeliveryLog.event_key == "task_assigned",
            )
        )
        rows = list(logs.scalars().all())
        assert len(rows) >= 2  # inbox + dingtalk
        channels = {r.channel for r in rows}
        assert "inbox" in channels and "dingtalk" in channels
        assert any(r.status == "success" for r in rows)

        inbox = await db.execute(select(Notification).where(Notification.user_id == u.id))
        assert inbox.scalars().first() is not None


@pytest.mark.asyncio
async def test_admin_notification_templates_and_logs():
    await _ensure_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin")
        headers = {"Authorization": f"Bearer {token}"}

        r = await client.get("/api/admin/notification-templates", headers=headers)
        assert r.status_code == 200, r.text
        templates = r.json()
        assert len(templates) >= 10
        keys = {t["event_key"] for t in templates}
        assert "task_assigned" in keys
        assert "weekly_report_overdue" in keys

        tid = templates[0]["id"]
        r2 = await client.patch(
            f"/api/admin/notification-templates/{tid}",
            headers=headers,
            json={"title_template": "【测试】" + templates[0]["title_template"][:20]},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["title_template"].startswith("【测试】")

        r3 = await client.get("/api/admin/notification-logs", headers=headers)
        assert r3.status_code == 200, r3.text
        body = r3.json()
        assert "total" in body and "items" in body


@pytest.mark.asyncio
async def test_template_disabled_skips():
    await _ensure_admin()
    async with async_session() as db:
        tpl = (
            await db.execute(
                select(NotificationTemplate).where(
                    NotificationTemplate.event_key == "task_remind"
                )
            )
        ).scalar_one()
        tpl.is_enabled = False
        await db.commit()

        u = User(
            username=f"t_notify_skip_{uuid4().hex[:8]}",
            name="跳过接收",
            hashed_password=hash_password("Aa123456"),
            must_change_password=False,
            is_active=True,
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)

        result = await dispatch_notification(
            db,
            event_key="task_remind",
            users=[u],
            variables={"actor_name": "A", "task_title": "T"},
            respect_quiet=False,
        )
        await db.commit()
        assert result["dingtalk_notified"] is False

        logs = await db.execute(
            select(NotificationDeliveryLog).where(
                NotificationDeliveryLog.user_id == u.id,
                NotificationDeliveryLog.event_key == "task_remind",
            )
        )
        row = logs.scalars().first()
        assert row is not None
        assert row.status == "skipped"
        assert row.error_message == "template_disabled"

        tpl.is_enabled = True
        await db.commit()
