from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.password_policy import validate_strong_password
from app.core.security import hash_password
from app.main import app
from app.models import Role, User, UserRole

INIT_PASSWORD = "InitManager2026"
NEW_PASSWORD = "MyPass2026"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_password_policy(client):
    resp = await client.get("/api/auth/password-policy")
    assert resp.status_code == 200
    assert "至少 8 位" in resp.json()["rules"][0]


def test_validate_strong_password():
    assert validate_strong_password(NEW_PASSWORD) == NEW_PASSWORD
    with pytest.raises(ValueError):
        validate_strong_password("short1")


@pytest.mark.asyncio
async def test_first_login_must_change_password(client):
    username = f"pw_{uuid4().hex[:10]}"
    async with async_session() as db:
        user = User(
            username=username,
            name=username,
            hashed_password=hash_password(INIT_PASSWORD),
            must_change_password=True,
        )
        db.add(user)
        await db.flush()
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()

    login = await client.post(
        "/api/auth/login", json={"username": username, "password": INIT_PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    blocked = await client.get("/api/projects/overview/stats", headers=headers)
    assert blocked.status_code == 403

    changed = await client.post(
        "/api/auth/change-password",
        json={"old_password": INIT_PASSWORD, "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200

    me = await client.get("/api/auth/me", headers=headers)
    assert me.json()["must_change_password"] is False

    stats = await client.get("/api/projects/overview/stats", headers=headers)
    assert stats.status_code == 200
