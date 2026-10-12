"""系统设置：向量模型配置 API（库优先、权限、开关）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Role, User, UserRole
from app.services import embedding_client as emb
from app.services import embedding_settings as emb_settings
from app.services.init_service import init_rbac

PASSWORD = "EmbedAdmin2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str]) -> User:
    username = f"t_ag_{uuid4().hex[:10]}"
    async with async_session() as db:
        await init_rbac(db)
        user = User(
            username=username,
            name=username,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        roles = (await db.execute(select(Role).where(Role.code.in_(role_codes)))).scalars().all()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        await db.refresh(user)
        return user


def _auth(user: User) -> dict[str, str]:
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _reset_embed(monkeypatch):
    monkeypatch.setattr(settings, "embedding_model", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    emb_settings.reset_embedding_cache()
    yield
    emb_settings.reset_embedding_cache()


@pytest.mark.asyncio
async def test_member_cannot_read_embedding_settings():
    member = await _make_user(role_codes=["member"])
    async with _client() as client:
        resp = await client.get("/api/admin/embedding-settings", headers=_auth(member))
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_save_enables_and_clear_disables():
    admin = await _make_user(role_codes=["admin"])
    async with _client() as client:
        headers = _auth(admin)
        get0 = await client.get("/api/admin/embedding-settings", headers=headers)
        assert get0.status_code == 200, get0.text
        assert get0.json()["enabled"] is False

        put = await client.put(
            "/api/admin/embedding-settings",
            headers=headers,
            json={
                "embedding_model": "bge-m3",
                "embedding_base_url": "http://127.0.0.1:18081/v1",
                "api_key": "sk-test-key",
            },
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["ok"] is True
        assert body["enabled"] is True
        assert body["embedding_model"] == "bge-m3"
        assert "sk-" in body["api_key_masked"] or "***" in body["api_key_masked"]
        assert emb.vector_enabled() is True

        clear = await client.put(
            "/api/admin/embedding-settings",
            headers=headers,
            json={
                "embedding_model": "",
                "embedding_base_url": "",
                "clear_api_key": True,
            },
        )
        assert clear.status_code == 200
        assert clear.json()["enabled"] is False
        assert emb.vector_enabled() is False


@pytest.mark.asyncio
async def test_embedding_test_without_endpoint_reports_failure():
    admin = await _make_user(role_codes=["admin"])
    async with _client() as client:
        headers = _auth(admin)
        resp = await client.post(
            "/api/admin/embedding-settings/test",
            headers=headers,
            json={
                "embedding_model": "bge-m3",
                "embedding_base_url": "http://127.0.0.1:1/v1",
                "api_key": "x",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["message"]
