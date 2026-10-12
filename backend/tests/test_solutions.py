"""方案库：存储校验、JSON 解析、API 权限与上传。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Role, Solution, SolutionTag, User, UserRole
from app.services.init_service import init_rbac
from app.services.solution_ai_tag import _normalize_tags, _parse_json_content
from app.services.solution_storage import ALLOWED_EXTENSIONS, safe_original_name


def test_safe_original_name_strips_path():
    assert safe_original_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert "\x00" not in safe_original_name("a\x00b.docx")


def test_allowed_extensions_cover_plan():
    for ext in (".pdf", ".docx", ".pptx", ".jpg", ".png", ".webp"):
        assert ext in ALLOWED_EXTENSIONS


def test_parse_json_content_plain_and_fenced():
    data = _parse_json_content('{"title":"测试","summary":"摘要","tags":["A","B"]}')
    assert data["title"] == "测试"
    assert data["tags"] == ["A", "B"]

    fenced = _parse_json_content(
        '```json\n{"title":"X","summary":"Y","tags":["t1"]}\n```'
    )
    assert fenced["title"] == "X"


def test_normalize_tags_dedupe_and_limit():
    tags = _normalize_tags([" 甲 ", "甲", "乙", "", 123] + [f"t{i}" for i in range(20)])
    assert tags[0] == "甲"
    assert "乙" in tags
    assert len(tags) <= 10


async def _ensure_member(username: str = "member1") -> None:
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == username))
        u = r.scalar_one_or_none()
        if not u:
            u = User(
                username=username,
                name="测试成员",
                hashed_password=hash_password("InitMember2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(u)
            await db.flush()
        else:
            u.hashed_password = hash_password("InitMember2026")
            u.must_change_password = False
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_solutions_list_requires_auth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/solutions")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_upload_and_list_with_mocked_ai(tmp_path: Path, monkeypatch):
    await _ensure_member()
    monkeypatch.setenv("SOLUTION_UPLOAD_DIR", str(tmp_path))
    # 重新读 settings 较麻烦；直接 patch upload_root
    from app.services import solution_storage

    monkeypatch.setattr(solution_storage, "upload_root", lambda: tmp_path)

    async def fake_process(solution_id: int) -> None:
        async with async_session() as db:
            sol = (
                await db.execute(select(Solution).where(Solution.id == solution_id))
            ).scalar_one()
            sol.title = "AI标题"
            sol.summary = "AI摘要"
            sol.status = "ready"
            db.add(SolutionTag(solution_id=sol.id, name="测试标签", source="ai"))
            await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "member1", "InitMember2026")
        headers = {"Authorization": f"Bearer {token}"}

        with patch(
            "app.api.solutions.process_solution",
            new=AsyncMock(side_effect=fake_process),
        ):
            files = {
                "file": (
                    "demo.docx",
                    BytesIO(b"PK\x03\x04fake-docx-bytes"),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            }
            resp = await client.post(
                "/api/solutions/upload", headers=headers, files=files
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["original_filename"] == "demo.docx"
            sid = body["id"]

        # BackgroundTasks 可能已跑完；若仍未就绪再手动处理一次
        detail0 = await client.get(f"/api/solutions/{sid}", headers=headers)
        if detail0.json().get("status") != "ready":
            await fake_process(sid)

        detail = await client.get(f"/api/solutions/{sid}", headers=headers)
        assert detail.status_code == 200
        data = detail.json()
        assert data["status"] == "ready"
        assert data["title"] == "AI标题"
        assert any(t["name"] == "测试标签" for t in data["tags"])

        listed = await client.get("/api/solutions", headers=headers, params={"tag": "测试标签"})
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1

        tags = await client.get("/api/solutions/tags", headers=headers)
        assert tags.status_code == 200
        assert any(t["name"] == "测试标签" for t in tags.json())

        # 清理
        del_resp = await client.delete(f"/api/solutions/{sid}", headers=headers)
        assert del_resp.status_code == 200
