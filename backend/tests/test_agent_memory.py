"""长期记忆：隔离、V1 关键词、V2 向量开关。"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import AgentMemoryChunk, Role, User, UserRole
from app.services import embedding_client as emb
from app.services import embedding_settings as emb_settings
from app.services.init_service import init_rbac
from app.services.service_auth import SERVICE_DSH, build_signed_headers

PASSWORD = "AgentMemory2026"
SECRET = "dsh-memory-test-secret"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_member(name: str | None = None) -> User:
    username = f"t_ag_{uuid4().hex[:10]}"
    async with async_session() as db:
        await init_rbac(db)
        user = User(
            username=username,
            name=name or username,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        await db.refresh(user)
        return user


async def _signed(client: AsyncClient, path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(SECRET, SERVICE_DSH, "POST", path, body)
    return await client.post(path, content=body.encode("utf-8"), headers=headers)


@pytest.fixture
def memory_secret(monkeypatch):
    monkeypatch.setattr(settings, "dsh_to_wb_secret", SECRET)
    monkeypatch.setattr(settings, "dsh_service_allowlist", "127.0.0.1,::1")
    monkeypatch.setattr(settings, "embedding_model", "")
    monkeypatch.setattr(settings, "embedding_base_url", "")
    monkeypatch.setattr(settings, "embedding_api_key", "")
    emb_settings.reset_embedding_cache()
    return SECRET


@pytest.mark.asyncio
async def test_memory_upsert_search_isolated(memory_secret):
    alice = await _make_member("记忆甲")
    bob = await _make_member("记忆乙")

    async with _client() as client:
        up_a = await _signed(
            client,
            "/api/dsh/tools/memory-upsert",
            {
                "wb_user_id": alice.id,
                "kind": "fact",
                "title": "卡点",
                "body": "甲在跟进红月项目接口联调",
            },
        )
        assert up_a.status_code == 200, up_a.text
        assert up_a.json()["ok"] is True

        up_b = await _signed(
            client,
            "/api/dsh/tools/memory-upsert",
            {
                "wb_user_id": bob.id,
                "kind": "fact",
                "title": "卡点",
                "body": "乙在跟进蓝海项目数据迁移",
            },
        )
        assert up_b.status_code == 200, up_b.text

        search_a = await _signed(
            client,
            "/api/dsh/tools/memory-search",
            {"wb_user_id": alice.id, "query": "红月项目"},
        )
        assert search_a.status_code == 200, search_a.text
        data_a = search_a.json()
        assert data_a["mode"] == "keyword"
        assert data_a["count"] >= 1
        blob = " ".join(item.get("text", "") for item in data_a["items"])
        assert "红月" in blob
        assert "蓝海" not in blob

        search_b = await _signed(
            client,
            "/api/dsh/tools/memory-search",
            {"wb_user_id": bob.id, "query": "红月项目"},
        )
        assert search_b.status_code == 200
        assert all("红月" not in (i.get("text") or "") for i in search_b.json()["items"])

        got = await _signed(client, "/api/dsh/tools/memory-get", {"wb_user_id": alice.id})
        assert got.status_code == 200
        assert got.json()["vector_enabled"] is False
        assert got.json()["recent_facts"]


@pytest.mark.asyncio
async def test_memory_work_summary_single_row(memory_secret):
    user = await _make_member()
    async with _client() as client:
        first = await _signed(
            client,
            "/api/dsh/tools/memory-upsert",
            {
                "wb_user_id": user.id,
                "kind": "work_summary",
                "body": "本周主攻接口",
            },
        )
        second = await _signed(
            client,
            "/api/dsh/tools/memory-upsert",
            {
                "wb_user_id": user.id,
                "kind": "work_summary",
                "body": "本周主攻联调验收",
            },
        )
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["note"]["id"] == second.json()["note"]["id"]
        assert "联调验收" in second.json()["note"]["body"]


@pytest.mark.asyncio
async def test_memory_vector_path_when_configured(memory_secret, monkeypatch):
    user = await _make_member()
    emb_settings.set_cached_config(
        emb_settings.EmbeddingConfig(
            model="bge-m3",
            base_url="http://127.0.0.1:9/v1",
            api_key="",
            source="test",
        )
    )
    assert emb.vector_enabled() is True

    async def fake_embed_one(text: str) -> list[float]:
        # 固定三维：含「红月」偏 [1,0,0]，否则 [0,1,0]
        if "红月" in text:
            return [1.0, 0.0, 0.0]
        if "蓝海" in text:
            return [0.0, 1.0, 0.0]
        return [0.7, 0.7, 0.0]

    monkeypatch.setattr(emb, "embed_one", fake_embed_one)

    async with _client() as client:
        await _signed(
            client,
            "/api/dsh/tools/memory-upsert",
            {
                "wb_user_id": user.id,
                "kind": "fact",
                "body": "红月项目接口联调",
            },
        )
        # 直接写入一条无关键词但有向量的 chunk，验证 hybrid 可命中
        async with async_session() as db:
            db.add(
                AgentMemoryChunk(
                    user_id=user.id,
                    source_type="session",
                    source_ref=f"session-vec-{uuid4().hex[:8]}",
                    text="以前讨论过同一立项的进度",
                    embedding=[0.95, 0.05, 0.0],
                )
            )
            await db.commit()

        resp = await _signed(
            client,
            "/api/dsh/tools/memory-search",
            {"wb_user_id": user.id, "query": "红月项目"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["mode"] == "hybrid"
        assert data["count"] >= 1


def test_cosine_similarity_basic():
    assert emb.cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert emb.cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
