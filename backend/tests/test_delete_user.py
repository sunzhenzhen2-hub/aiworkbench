"""删除账号：保护规则与外键改挂。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Role, User, UserRole
from app.services.init_service import init_rbac
from app.services.user_sync import delete_user_account


async def _ensure_users():
    async with async_session() as db:
        await init_rbac(db)
        for username, name, pwd, role_code in [
            ("admin", "系统管理员", "InitAdmin2026", "admin"),
            ("del_target", "待删用户", "InitMember2026", "member"),
        ]:
            r = await db.execute(select(User).where(User.username == username))
            u = r.scalar_one_or_none()
            if not u:
                u = User(
                    username=username,
                    name=name,
                    hashed_password=hash_password(pwd),
                    must_change_password=False,
                    is_active=True,
                )
                db.add(u)
                await db.flush()
            else:
                u.hashed_password = hash_password(pwd)
                u.must_change_password = False
            role = (await db.execute(select(Role).where(Role.code == role_code))).scalar_one()
            ur = await db.execute(
                select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
            )
            if not ur.scalar_one_or_none():
                db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()


@pytest.mark.asyncio
async def test_cannot_delete_admin_or_self():
    await _ensure_users()
    async with async_session() as db:
        admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
        target = (await db.execute(select(User).where(User.username == "del_target"))).scalar_one()
        with pytest.raises(ValueError, match="admin"):
            await delete_user_account(db, admin.id, actor=admin)
        with pytest.raises(ValueError, match="当前登录"):
            await delete_user_account(db, target.id, actor=target)


@pytest.mark.asyncio
async def test_delete_user_api():
    await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "InitAdmin2026"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        async with async_session() as db:
            target = (
                await db.execute(select(User).where(User.username == "del_target"))
            ).scalar_one()
            tid = target.id

        resp = await client.delete(f"/api/admin/users/{tid}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["username"] == "del_target"

        async with async_session() as db:
            gone = (
                await db.execute(select(User).where(User.username == "del_target"))
            ).scalar_one_or_none()
            assert gone is None

        # 禁止删 admin
        async with async_session() as db:
            admin_id = (
                await db.execute(select(User.id).where(User.username == "admin"))
            ).scalar_one()
        bad = await client.delete(f"/api/admin/users/{admin_id}", headers=headers)
        assert bad.status_code == 400
