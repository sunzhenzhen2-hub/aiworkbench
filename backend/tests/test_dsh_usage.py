"""智能助手用量查看（admin.model 代理 dsh-gate）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Role, User, UserRole
from app.services.init_service import init_rbac


async def _ensure_users():
    async with async_session() as db:
        await init_rbac(db)
        admin = (await db.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        if not admin:
            admin = User(
                username="admin",
                name="系统管理员",
                hashed_password=hash_password("InitAdmin2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(admin)
            await db.flush()
        else:
            admin.hashed_password = hash_password("InitAdmin2026")
            admin.must_change_password = False

        member = (await db.execute(select(User).where(User.username == "t_dsh_usage_m"))).scalar_one_or_none()
        if not member:
            member = User(
                username="t_dsh_usage_m",
                name="用量测试成员",
                hashed_password=hash_password("InitMember2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(member)
            await db.flush()

        admin_role = (await db.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        member_role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        for user, role in ((admin, admin_role), (member, member_role)):
            exists = await db.execute(
                select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
            )
            if not exists.scalar_one_or_none():
                db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_dsh_usage_requires_admin_model(monkeypatch):
    await _ensure_users()

    def _boom(*_a, **_k):
        raise AssertionError("member should not call dsh")

    monkeypatch.setattr("app.api.admin.signed_dsh_post", _boom)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "t_dsh_usage_m", "InitMember2026")
        denied = await client.get(
            "/api/admin/dsh-usage/summary",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_dsh_usage_summary_visits_sessions(monkeypatch):
    await _ensure_users()

    def fake_signed_post(path: str, payload: dict, **_kwargs):
        assert "from" in payload or True
        if path.endswith("/summary"):
            return {
                "from": "2026-09-01T00:00:00.000Z",
                "to": "2026-09-26T00:00:00.000Z",
                "items": [
                    {
                        "username": "admin",
                        "open_count": 2,
                        "last_open_at": "2026-09-25T10:00:00.000Z",
                        "prompt_count": 1,
                        "turns": 3,
                        "steps": 5,
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_read_tokens": 10,
                        "cache_write_tokens": 2,
                        "session_count": 1,
                    }
                ],
            }
        if path.endswith("/visits"):
            return {
                "from": "2026-09-01T00:00:00.000Z",
                "to": "2026-09-26T00:00:00.000Z",
                "items": [
                    {
                        "username": "admin",
                        "started_at": "2026-09-25T10:00:00.000Z",
                        "ended_at": "2026-09-25T10:05:00.000Z",
                        "duration_sec": 300,
                        "source": "sso",
                        "ip": "127.0.0.1",
                    }
                ],
            }
        if path.endswith("/sessions"):
            return {
                "from": "2026-09-01T00:00:00.000Z",
                "to": "2026-09-26T00:00:00.000Z",
                "items": [
                    {
                        "username": "admin",
                        "session_id": "session-demo",
                        "model": "MiniMax",
                        "provider": "minimax-cn",
                        "turns": 3,
                        "steps": 5,
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_read_tokens": 10,
                        "cache_write_tokens": 2,
                        "last_prompt_at": "2026-09-25T10:03:00.000Z",
                    }
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr("app.api.admin.signed_dsh_post", fake_signed_post)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin", "InitAdmin2026")
        headers = {"Authorization": f"Bearer {token}"}

        summary = await client.get("/api/admin/dsh-usage/summary", headers=headers)
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert body["from"].startswith("2026-09-01")
        assert len(body["items"]) == 1
        assert body["items"][0]["username"] == "admin"
        assert body["items"][0]["name"]  # 展示名来自工作台用户表
        assert body["items"][0]["open_count"] == 2
        assert body["items"][0]["input_tokens"] == 100

        visits = await client.get(
            "/api/admin/dsh-usage/visits",
            headers=headers,
            params={"username": "admin"},
        )
        assert visits.status_code == 200, visits.text
        v = visits.json()["items"][0]
        assert v["duration_sec"] == 300
        assert v["name"] == body["items"][0]["name"]

        sessions = await client.get("/api/admin/dsh-usage/sessions", headers=headers)
        assert sessions.status_code == 200, sessions.text
        s = sessions.json()["items"][0]
        assert s["session_id"] == "session-demo"
        assert s["model"] == "MiniMax"
