"""项目阶段：按类型模板播种 + current_phase 与时间线双向同步。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Phase, Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac
from app.services.phase_match import guess_phase_name_from_text, guess_phase_names_from_text
from app.services.project_phases import (
    INTEGRATION_PHASES,
    SOFTWARE_PHASES,
    is_pristine_default_timeline,
    phases_for,
)
from app.services.project_service import seed_standard_phases, sync_project_phase_progress

PASSWORD = "PhaseLoop2026"


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(*, role_codes: list[str], name: str | None = None) -> User:
    username = f"pr_{uuid4().hex[:10]}"
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
    resp = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _make_project_with_phases(
    owner: User, *, project_type: str = "software", name: str | None = None
) -> Project:
    async with async_session() as db:
        names = phases_for(project_type)
        project = Project(
            name=name or f"phase_{uuid4().hex[:8]}",
            project_type=project_type,
            level="normal",
            status="normal",
            owner_id=owner.id,
            current_phase=names[0],
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
        await seed_standard_phases(db, project.id, project_type)
        await db.commit()
        await db.refresh(project)
        return project


def test_phases_for_by_type():
    assert phases_for("software") == SOFTWARE_PHASES
    assert phases_for("fusion") == SOFTWARE_PHASES
    assert phases_for("integration") == INTEGRATION_PHASES
    assert "开发" in phases_for("software")
    assert "现场实施" in phases_for("integration")
    assert "实施/开发" not in phases_for("software")


def test_guess_phase_names_prefer_longer_and_multi():
    assert guess_phase_name_from_text("完成需求调研") == "需求分析"
    assert guess_phase_name_from_text("本周做了设计评审") == "设计"
    names = guess_phase_names_from_text("开发联调中")
    assert "开发" in names
    assert guess_phase_name_from_text("随便写点") is None
    assert guess_phase_name_from_text("手表测试，对接厂家") is None
    assert guess_phase_name_from_text("现场施工布线") == "现场实施"
    assert guess_phase_name_from_text("设备到货验收") == "采购到货"


@pytest.mark.asyncio
async def test_seed_phases_differ_by_type():
    owner = await _make_user(role_codes=["project_owner"])
    soft = await _make_project_with_phases(owner, project_type="software")
    integ = await _make_project_with_phases(owner, project_type="integration")

    async with async_session() as db:
        soft_names = [
            p.name
            for p in (
                await db.execute(
                    select(Phase).where(Phase.project_id == soft.id).order_by(Phase.sort_order)
                )
            )
            .scalars()
            .all()
        ]
        integ_names = [
            p.name
            for p in (
                await db.execute(
                    select(Phase).where(Phase.project_id == integ.id).order_by(Phase.sort_order)
                )
            )
            .scalars()
            .all()
        ]
    assert soft_names == SOFTWARE_PHASES
    assert integ_names == INTEGRATION_PHASES


@pytest.mark.asyncio
async def test_sync_current_phase_updates_timeline():
    owner = await _make_user(role_codes=["project_owner"])
    project = await _make_project_with_phases(owner, project_type="software")

    async with async_session() as db:
        proj = await db.get(Project, project.id)
        assert proj is not None
        await sync_project_phase_progress(db, proj, phase_name="开发", status="in_progress")
        await db.commit()
        rows = list(
            (
                await db.execute(
                    select(Phase).where(Phase.project_id == project.id).order_by(Phase.sort_order)
                )
            )
            .scalars()
            .all()
        )
        await db.refresh(proj)
        assert proj.current_phase == "开发"
        assert [p.status for p in rows] == [
            "completed",
            "completed",
            "completed",
            "in_progress",
            "not_started",
            "not_started",
            "not_started",
            "not_started",
            "not_started",
        ]


@pytest.mark.asyncio
async def test_patch_current_phase_and_phase_status_via_api():
    owner = await _make_user(role_codes=["project_owner"], name="阶段负责人")
    project = await _make_project_with_phases(owner, project_type="software")

    async with _client() as client:
        token = await _login(client, owner.username)
        headers = {"Authorization": f"Bearer {token}"}

        patched = await client.patch(
            f"/api/projects/{project.id}",
            headers=headers,
            json={"current_phase": "测试"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["current_phase"] == "测试"

        phases = await client.get(f"/api/projects/{project.id}/phases", headers=headers)
        assert phases.status_code == 200, phases.text
        rows = phases.json()
        by_name = {p["name"]: p for p in rows}
        assert by_name["需求分析"]["status"] == "completed"
        assert by_name["测试"]["status"] == "in_progress"
        assert by_name["上线"]["status"] == "not_started"

        test_id = by_name["测试"]["id"]
        done = await client.patch(
            f"/api/projects/phases/{test_id}",
            headers=headers,
            json={"status": "completed"},
        )
        assert done.status_code == 200, done.text

        again = await client.get(f"/api/projects/{project.id}/phases", headers=headers)
        rows2 = {p["name"]: p for p in again.json()}
        assert rows2["测试"]["status"] == "completed"
        assert rows2["上线"]["status"] == "in_progress"

        detail = await client.get(f"/api/projects/{project.id}", headers=headers)
        assert detail.json()["current_phase"] == "上线"


@pytest.mark.asyncio
async def test_type_change_replaces_pristine_phases_only():
    owner = await _make_user(role_codes=["project_owner"])
    project = await _make_project_with_phases(owner, project_type="software")

    async with _client() as client:
        token = await _login(client, owner.username)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.patch(
            f"/api/projects/{project.id}",
            headers=headers,
            json={"project_type": "integration"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["project_type"] == "integration"
        assert resp.json()["current_phase"] == INTEGRATION_PHASES[0]

        phases = await client.get(f"/api/projects/{project.id}/phases", headers=headers)
        names = [p["name"] for p in phases.json()]
        assert names == INTEGRATION_PHASES

    # 改过状态后再换类型：不重刷
    touched = await _make_project_with_phases(owner, project_type="software")
    async with async_session() as db:
        proj = await db.get(Project, touched.id)
        assert proj is not None
        await sync_project_phase_progress(db, proj, phase_name="设计", status="in_progress")
        await db.commit()
        rows = list(
            (
                await db.execute(
                    select(Phase).where(Phase.project_id == touched.id).order_by(Phase.sort_order)
                )
            )
            .scalars()
            .all()
        )
        assert not is_pristine_default_timeline(rows, project_type="software")

    async with _client() as client:
        token = await _login(client, owner.username)
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.patch(
            f"/api/projects/{touched.id}",
            headers=headers,
            json={"project_type": "integration"},
        )
        assert resp.status_code == 200, resp.text
        phases = await client.get(f"/api/projects/{touched.id}/phases", headers=headers)
        names = [p["name"] for p in phases.json()]
        assert names == SOFTWARE_PHASES


@pytest.mark.asyncio
async def test_meta_types_includes_phases_by_type():
    owner = await _make_user(role_codes=["member"])
    async with _client() as client:
        token = await _login(client, owner.username)
        resp = await client.get(
            "/api/projects/meta/types",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "phases_by_type" in data
        soft = [p["value"] for p in data["phases_by_type"]["software"]]
        integ = [p["value"] for p in data["phases_by_type"]["integration"]]
        assert soft == SOFTWARE_PHASES
        assert integ == INTEGRATION_PHASES
