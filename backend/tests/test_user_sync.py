"""用户同步：保留 admin、清空其他、按工号导入（钉钉接口用假数据）。"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.database import async_session
from app.core.security import hash_password, verify_password
from app.main import app
from app.models import Role, User, UserRole
from app.services.init_service import ensure_demo_users, init_rbac

PASSWORD = "AdminSync2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _ensure_admin() -> User:
    async with async_session() as db:
        await init_rbac(db)
        await ensure_demo_users(db)
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one()
        admin.hashed_password = hash_password(PASSWORD)
        admin.must_change_password = False
        await db.commit()
        await db.refresh(admin)
        return admin


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_sync_dingtalk_dept_keeps_admin_and_imports(monkeypatch):
    await _ensure_admin()
    # 额外演示用户
    async with async_session() as db:
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        u = User(
            username=f"demo_{uuid4().hex[:6]}",
            name="演示员",
            hashed_password=hash_password("DemoUser2026"),
            must_change_password=False,
        )
        db.add(u)
        await db.flush()
        db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()

    monkeypatch.setattr(
        "app.services.dingtalk.find_department_by_name",
        AsyncMock(return_value={"dept_id": 99, "name": "政企市场部"}),
    )
    monkeypatch.setattr(
        "app.services.dingtalk.list_department_users",
        AsyncMock(
            return_value=[
                {
                    "userid": "dd_ok_1",
                    "name": "张三",
                    "job_number": "E1001",
                    "mobile": "13800138000",
                },
                {
                    "userid": "dd_skip",
                    "name": "无手机",
                    "job_number": "E1002",
                    "mobile": "",
                },
                {
                    "userid": "dd_ok_2",
                    "name": "李四",
                    "job_number": "E1003",
                    "mobile": "13900139000",
                },
            ]
        ),
    )

    async with _client() as client:
        token = await _login(client, "admin", PASSWORD)
        resp = await client.post(
            "/api/admin/users/sync-dingtalk-dept",
            headers={"Authorization": f"Bearer {token}"},
            json={"department_name": "政企市场部"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported_count"] == 2
        assert body["skipped_count"] == 1
        assert body["skipped"][0]["reason"] == "缺少手机号"
        assert body["deleted"] >= 1

        users = await client.get(
            "/api/admin/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert users.status_code == 200
        rows = users.json()
        names = {u["username"] for u in rows}
        assert "admin" in names
        assert "E1001" in names
        assert "E1003" in names
        assert "demo_" not in "".join(names) or all(not n.startswith("demo_") for n in names)

        e1001 = next(u for u in rows if u["username"] == "E1001")
        assert e1001["job_number"] == "E1001"
        assert e1001["name"] == "张三"
        assert e1001["dingtalk_bound"] is True
        assert e1001["must_change_password"] is True

    async with async_session() as db:
        user = (await db.execute(select(User).where(User.username == "E1001"))).scalar_one()
        assert verify_password("E100113800138000", user.hashed_password)
        count = (await db.execute(select(func.count()).select_from(User))).scalar()
        # admin + 2 imported
        assert count == 3
