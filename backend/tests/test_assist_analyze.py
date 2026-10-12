"""分析管控：权限隔离 + 编排接口结构（assist.analyze 仅管理员）。"""

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

        member = (
            await db.execute(select(User).where(User.username == "t_af_analyze_m"))
        ).scalar_one_or_none()
        if not member:
            member = User(
                username="t_af_analyze_m",
                name="分析测试成员",
                hashed_password=hash_password("InitMember2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(member)
            await db.flush()
        else:
            member.hashed_password = hash_password("InitMember2026")
            member.must_change_password = False

        admin_role = (await db.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        member_role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        for user, role in ((admin, admin_role), (member, member_role)):
            exists = await db.execute(
                select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
            )
            if not exists.scalar_one_or_none():
                db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        return admin.id, member.id


async def _login(client: AsyncClient, username: str, password: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_analyze_requires_assist_analyze():
    await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "t_af_analyze_m", "InitMember2026")
        denied = await client.get(
            "/api/ai/analyze/team",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_analyze_team_ok_for_admin():
    await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin", "InitAdmin2026")
        resp = await client.get(
            "/api/ai/analyze/team",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("kind") == "team"
        assert "summary" in data or "conclusion" in data
        assert isinstance(data.get("actions"), list)
        assert data.get("prompt_pack")
        assert isinstance(data.get("snapshot"), dict)
        assert data["snapshot"].get("on_track_hint")
        assert data["snapshot"].get("top_risk")
        for a in data.get("actions") or []:
            assert a.get("issue_nature") in {
                "person",
                "external",
                "process",
                "unclear",
            }


@pytest.mark.asyncio
async def test_guidance_save_and_personal_read():
    _admin_id, member_id = await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        admin_token = await _login(client, "admin", "InitAdmin2026")
        save = await client.post(
            "/api/ai/analyze/guidance",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "target_user_id": member_id,
                "content": "请本周补齐周报结果说明，并优先处理逾期任务。",
                "action_items": ["补交周报", "处理逾期"],
                "notify": False,
                "source": "admin",
            },
        )
        assert save.status_code == 200, save.text
        assert save.json().get("ok") is True

        member_token = await _login(client, "t_af_analyze_m", "InitMember2026")
        mine = await client.get(
            "/api/personal/guidance",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert mine.status_code == 200, mine.text
        items = mine.json().get("items") or []
        assert any("补齐周报" in (it.get("content") or "") for it in items)
        note = next(it for it in items if "补齐周报" in (it.get("content") or ""))
        note_id = note["id"]

        read_resp = await client.post(
            f"/api/personal/guidance/{note_id}/read",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert read_resp.status_code == 200, read_resp.text
        assert read_resp.json().get("note", {}).get("read_at")

        improved = await client.post(
            f"/api/personal/guidance/{note_id}/improved",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"improve_note": "已补交并处理逾期"},
        )
        assert improved.status_code == 200, improved.text
        assert improved.json().get("note", {}).get("improved_at")

        # 成员不能保存指导
        denied = await client.post(
            "/api/ai/analyze/guidance",
            headers={"Authorization": f"Bearer {member_token}"},
            json={"target_user_id": member_id, "content": "无权"},
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_dept_head_can_analyze_and_stats():
    await _ensure_users()
    async with async_session() as db:
        await init_rbac(db)
        head = (
            await db.execute(select(User).where(User.username == "t_af_analyze_dh"))
        ).scalar_one_or_none()
        if not head:
            head = User(
                username="t_af_analyze_dh",
                name="分析测试部门负责人",
                hashed_password=hash_password("InitManager2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(head)
            await db.flush()
        else:
            head.hashed_password = hash_password("InitManager2026")
            head.must_change_password = False
        role = (await db.execute(select(Role).where(Role.code == "dept_head"))).scalar_one()
        exists = await db.execute(
            select(UserRole).where(UserRole.user_id == head.id, UserRole.role_id == role.id)
        )
        if not exists.scalar_one_or_none():
            db.add(UserRole(user_id=head.id, role_id=role.id))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "t_af_analyze_dh", "InitManager2026")
        analyze = await client.get(
            "/api/ai/analyze/team",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert analyze.status_code == 200, analyze.text
        stats_people = await client.get(
            "/api/stats/people",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stats_people.status_code == 200, stats_people.text
        assert "people" in stats_people.json()
        stats_proj = await client.get(
            "/api/stats/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert stats_proj.status_code == 200, stats_proj.text
        assert "party_distribution" in stats_proj.json()

        attr = await client.get(
            "/api/projects/overview/blocker-attribution",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert attr.status_code == 200, attr.text
        assert "counts" in attr.json()


@pytest.mark.asyncio
async def test_analyze_nudge_report_missing():
    from app.services.report_roster import list_report_roster_users

    _admin_id, member_id = await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin", "InitAdmin2026")
        # 未知类型应拒绝
        bad = await client.post(
            "/api/ai/analyze/nudge",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "custom_spam",
                "user_id": member_id,
                "title": "钓鱼标题",
                "detail": "任意内容",
            },
        )
        assert bad.status_code == 400, bad.text

        # 非名册用户不可催周报
        off = await client.post(
            "/api/ai/analyze/nudge",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "report_missing",
                "user_id": member_id,
                "title": "催交周报测试",
                "detail": "请尽快提交",
            },
        )
        assert off.status_code == 400, off.text

        async with async_session() as db:
            roster = await list_report_roster_users(db)
        if not roster:
            return
        target_id = roster[0].id
        resp = await client.post(
            "/api/ai/analyze/nudge",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "type": "report_missing",
                "user_id": target_id,
                "title": "催交周报测试",
                "detail": "请尽快提交",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json().get("ok") is True


@pytest.mark.asyncio
async def test_issue_nature_and_person_snapshot_helpers():
    from app.services.assist_analyze import (
        _build_snapshot,
        _finalize_analyze_payload,
        _issue_nature_for_action,
        _is_coaching_focus_line,
    )

    assert _issue_nature_for_action("blocker_open", "internal") == "person"
    assert _issue_nature_for_action("blocker_open", "customer") == "external"
    assert _issue_nature_for_action("blocker_open", "approval") == "process"
    assert not _is_coaching_focus_line("本周周报已提交")
    assert _is_coaching_focus_line("请本周内补交周报，写清做成了什么。")

    person_payload = {
        "kind": "person",
        "stats": {
            "this_week_submitted": False,
            "overdue_tasks": 0,
            "open_blockers": 0,
            "pending_accept": 0,
        },
        "actions": [
            {
                "type": "report_missing",
                "priority": "high",
                "title": "催交周报：张三",
                "detail": "本周未交",
            }
        ],
        "focus": ["本周周报未提交"],
        "blockers": [],
        "quality_alerts": [],
    }
    snap = _build_snapshot(person_payload)
    assert "偏黄" in snap["on_track_hint"] or "风险" in snap["on_track_hint"]
    assert "催交周报" in (snap["top_risk"] or "")

    finalized = _finalize_analyze_payload(
        {
            "kind": "team",
            "stats": {"overdue_tasks": 0, "open_blockers": 0, "report_expected": 0, "report_submitted": 0},
            "actions": [
                {
                    "type": "blocker_open",
                    "party_type": "internal",
                    "priority": "normal",
                    "title": "卡点",
                    "detail": "x",
                }
            ],
            "focus": [],
            "blockers": [],
            "quality_alerts": [],
        }
    )
    assert finalized["actions"][0]["issue_nature"] == "person"


@pytest.mark.asyncio
async def test_analyze_person_structure():
    _admin_id, member_id = await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin", "InitAdmin2026")
        resp = await client.get(
            f"/api/ai/analyze/person/{member_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("kind") == "person"
        assert data.get("user", {}).get("id") == member_id
        assert isinstance(data.get("guidance_draft"), list)


@pytest.mark.asyncio
async def test_analyze_team_stream_emits_partial():
    """流式接口在 LLM 完成前就应先推 partial（不等待整段结论）。"""
    await _ensure_users()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "admin", "InitAdmin2026")
        async with client.stream(
            "GET",
            "/api/ai/analyze/team/stream",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        ) as resp:
            assert resp.status_code == 200
            events: list[str] = []
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                # 拿到骨架即可结束，避免等 LLM 拉长测试
                if "partial" in events:
                    break
            assert "start" in events
            assert "partial" in events
