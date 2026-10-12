"""LLM 模型管理与 resolver 测试。"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import LlmModel, Role, User, UserRole
from app.services.init_service import init_rbac
from app.services.llm_resolver import mask_api_key, resolve_llm


def test_mask_api_key():
    assert mask_api_key("") == "（未设置）"
    assert "***" in mask_api_key("sk-abcdefghijklmnop")
    assert "sk-" in mask_api_key("sk-abcdefghijklmnop") or mask_api_key("sk-abcdefghijklmnop").startswith("sk-")


TEST_MODEL_NAMES = {
    "测试模型",
    "模型A",
    "模型B",
    "通用模型",
    "任务专用",
    "通用A",
    "辅导A",
}


def _is_test_llm_row(row: LlmModel) -> bool:
    base = (row.api_base or "").lower()
    name = row.name or ""
    if name in TEST_MODEL_NAMES:
        return True
    if name.startswith("__auto_test_"):
        return True
    if "example.com" in base or "example/" in base:
        return True
    return False


async def _ensure_admin():
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == "admin"))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username="admin",
                name="系统管理员",
                hashed_password=hash_password("InitAdmin2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(u)
            await db.flush()
        else:
            # 仅保证可登录跑 API 单测；不改动其它用户
            u.hashed_password = hash_password("InitAdmin2026")
            u.must_change_password = False
        role = (await db.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=role.id))
        # 只删测试模型，绝不清空正式配置
        existing = await db.execute(select(LlmModel))
        for row in existing.scalars().all():
            if _is_test_llm_row(row):
                await db.delete(row)
        await db.commit()


async def _cleanup_test_models():
    async with async_session() as db:
        existing = await db.execute(select(LlmModel))
        for row in existing.scalars().all():
            if _is_test_llm_row(row):
                await db.delete(row)
        await db.commit()


@pytest.mark.asyncio
async def test_resolve_prefers_db_default_over_empty():
    await _ensure_admin()
    async with async_session() as db:
        db.add(
            LlmModel(
                name="测试模型",
                api_base="https://example.com/v1",
                api_key="sk-testkey123456",
                model_name="demo-model",
                purpose="report_coach",
                is_enabled=True,
                is_default=True,
            )
        )
        await db.commit()
        resolved = await resolve_llm(db, "report_coach")
        assert resolved is not None
        assert resolved.source == "db"
        assert resolved.model_name == "demo-model"
        assert resolved.api_base == "https://example.com/v1"
    await _cleanup_test_models()


@pytest.mark.asyncio
async def test_admin_models_crud_masks_key_and_default_mutex():
    await _ensure_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "InitAdmin2026"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/api/admin/models",
            headers=headers,
            json={
                "name": "模型A",
                "api_base": "https://a.example/v1",
                "api_key": "sk-aaaaaaaaaaaa",
                "model_name": "model-a",
                "purpose": "report_coach",
                "is_enabled": True,
                "is_default": True,
            },
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert "api_key" not in body
        assert body["api_key_masked"] != "sk-aaaaaaaaaaaa"
        assert "***" in body["api_key_masked"]
        id_a = body["id"]

        created_b = await client.post(
            "/api/admin/models",
            headers=headers,
            json={
                "name": "模型B",
                "api_base": "https://b.example/v1",
                "api_key": "sk-bbbbbbbbbbbb",
                "model_name": "model-b",
                "purpose": "report_coach",
                "is_enabled": True,
                "is_default": True,
            },
        )
        assert created_b.status_code == 200, created_b.text
        id_b = created_b.json()["id"]

        listing = await client.get("/api/admin/models", headers=headers)
        assert listing.status_code == 200
        rows = {r["id"]: r for r in listing.json()}
        assert rows[id_a]["is_default"] is False
        assert rows[id_b]["is_default"] is True
        assert "sk-bbbbbbbbbbbb" not in str(listing.json())

        deleted = await client.delete(f"/api/admin/models/{id_a}", headers=headers)
        assert deleted.status_code == 200
        await client.delete(f"/api/admin/models/{id_b}", headers=headers)


@pytest.mark.asyncio
async def test_resolve_general_covers_other_purposes():
    """只配 general 时，其它用途也能命中。"""
    await _ensure_admin()
    async with async_session() as db:
        db.add(
            LlmModel(
                name="通用模型",
                api_base="https://general.example/v1",
                api_key="sk-generalkkey12",
                model_name="general-model",
                purpose="general",
                is_enabled=True,
                is_default=True,
            )
        )
        await db.commit()
        for purpose in ("task_draft", "report_coach", "solution_tag"):
            resolved = await resolve_llm(db, purpose)
            assert resolved is not None, purpose
            assert resolved.source == "db"
            assert resolved.model_name == "general-model"
            assert resolved.api_base == "https://general.example/v1"
    await _cleanup_test_models()


@pytest.mark.asyncio
async def test_resolve_specific_overrides_general():
    """有专用用途时优先专用，不用 general。"""
    await _ensure_admin()
    async with async_session() as db:
        db.add(
            LlmModel(
                name="通用模型",
                api_base="https://general.example/v1",
                api_key="sk-generalkkey12",
                model_name="general-model",
                purpose="general",
                is_enabled=True,
                is_default=True,
            )
        )
        db.add(
            LlmModel(
                name="任务专用",
                api_base="https://task.example/v1",
                api_key="sk-taskkey123456",
                model_name="task-model",
                purpose="task_draft",
                is_enabled=True,
                is_default=True,
            )
        )
        await db.commit()
        task = await resolve_llm(db, "task_draft")
        assert task is not None
        assert task.model_name == "task-model"
        assert task.api_base == "https://task.example/v1"
        coach = await resolve_llm(db, "report_coach")
        assert coach is not None
        assert coach.model_name == "general-model"
    await _cleanup_test_models()


@pytest.mark.asyncio
async def test_general_default_mutex_does_not_affect_other_purpose():
    """general 的默认互斥不影响其它用途的默认。"""
    await _ensure_admin()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "InitAdmin2026"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        general = await client.post(
            "/api/admin/models",
            headers=headers,
            json={
                "name": "通用A",
                "api_base": "https://g.example/v1",
                "api_key": "sk-gggggggggggg",
                "model_name": "g-model",
                "purpose": "general",
                "is_enabled": True,
                "is_default": True,
            },
        )
        assert general.status_code == 200, general.text
        gid = general.json()["id"]

        coach = await client.post(
            "/api/admin/models",
            headers=headers,
            json={
                "name": "辅导A",
                "api_base": "https://c.example/v1",
                "api_key": "sk-cccccccccccc",
                "model_name": "c-model",
                "purpose": "report_coach",
                "is_enabled": True,
                "is_default": True,
            },
        )
        assert coach.status_code == 200, coach.text
        cid = coach.json()["id"]

        listing = await client.get("/api/admin/models", headers=headers)
        assert listing.status_code == 200
        rows = {r["id"]: r for r in listing.json()}
        assert rows[gid]["is_default"] is True
        assert rows[cid]["is_default"] is True

        await client.delete(f"/api/admin/models/{gid}", headers=headers)
        await client.delete(f"/api/admin/models/{cid}", headers=headers)
    await _cleanup_test_models()
