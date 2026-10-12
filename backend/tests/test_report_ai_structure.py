"""周报 AI 结构化 + 任务链：规则匹配与 API 集成测试。"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.init_service import init_rbac
from app.services.report_ai_structure import (
    _rule_match,
    _similarity,
    is_continuation_text,
    strip_continuation_prefix,
)


def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def test_similarity_contains_and_ratio():
    assert _similarity("天网接入冲刺", "天网接入") >= 0.8
    assert _similarity("完全无关的事", "另一件事情啊") < 0.55


def test_strip_continuation_prefix():
    assert strip_continuation_prefix("继续推进零信任联调") == "零信任联调"
    assert strip_continuation_prefix("持续跟进：政务外网巡检") == "政务外网巡检"
    assert strip_continuation_prefix("天网接入冲刺") == "天网接入冲刺"
    assert is_continuation_text("继续推进零信任", "") is True
    assert is_continuation_text("天网接入", "下周安装") is False
    assert is_continuation_text("", "继续跟进验收") is True
    # 套话「继续政务外网…」不算明确延续前缀（避免全员误降阈值）
    assert is_continuation_text("", "继续政务外网相关平台设备运维及故障处理。") is False
    from app.services.report_ai_structure import is_soft_continuation_text

    assert is_soft_continuation_text("", "继续政务外网相关平台设备运维及故障处理。") is True


def test_rule_match_continuation_links_prior_without_prefix():
    """「继续推进XX」应能命中上周无前缀的「XX」。"""
    history_chain = str(uuid.uuid4())
    draft = {
        "task_title": "继续推进零信任准入联调",
        "result_text": "本周完成两台终端试点",
        "stuck_status": "none",
        "next_goal": "扩大试点范围",
        "risk_level": "normal",
        "project_id": 21,
    }
    ctx = {
        "projects": [{"project_id": 21, "name": "电子政务网驻场"}],
        "history": [
            {
                "item_id": 301,
                "chain_id": history_chain,
                "project_id": 21,
                "linked_task_id": None,
                "task_title": "零信任准入联调",
                "result_text": "完成方案与账号开通",
                "next_goal": "终端试点",
                "period_start": "2026-09-01",
            },
            {
                "item_id": 302,
                "chain_id": None,
                "project_id": 99,
                "linked_task_id": None,
                "task_title": "完全无关的文旅宣传",
                "result_text": "写软文",
                "next_goal": "再发一篇",
                "period_start": "2026-09-01",
            },
        ],
        "tasks": [],
    }
    out = _rule_match(draft, ctx, existing_chain_id=None)
    assert out["chain_suggestion"] is not None
    assert out["chain_suggestion"]["chain_id"] == history_chain
    assert out["create_new_chain"] is False
    assert out["chain_suggestion"]["seed_item_id"] is None


def test_rule_match_suggests_chain_from_history():
    draft = {
        "task_title": "天网接入冲刺",
        "result_text": "本周完成点位勘察 3 处",
        "quant_value": "3",
        "stuck_status": "none",
        "next_goal": "下周安装",
        "next_due_date": "2026-09-12",
        "risk_level": "normal",
    }
    history_chain = str(uuid.uuid4())
    ctx = {
        "projects": [{"project_id": 11, "name": "天网项目"}],
        "history": [
            {
                "item_id": 101,
                "chain_id": history_chain,
                "project_id": 11,
                "linked_task_id": None,
                "task_title": "天网接入冲刺",
                "result_text": "上周完成方案评审",
                "next_goal": "开始勘察",
                "period_start": "2026-08-25",
            }
        ],
        "tasks": [{"task_id": 55, "title": "天网接入冲刺", "project_id": 11}],
    }
    out = _rule_match(draft, ctx, existing_chain_id=None)
    assert out["enabled"] is True
    assert out["chain_suggestion"] is not None
    assert out["chain_suggestion"]["chain_id"] == history_chain
    assert out["create_new_chain"] is False
    assert out["project_suggestion"] is not None
    assert out["project_suggestion"]["project_id"] == 11
    assert out["task_suggestion"] is not None
    assert out["task_suggestion"]["task_id"] == 55


def test_rule_match_seed_item_when_history_has_no_chain():
    draft = {
        "task_title": "热成像到货跟进",
        "result_text": "催厂家发货",
        "stuck_status": "none",
        "next_goal": "到货安装",
        "risk_level": "normal",
    }
    ctx = {
        "projects": [],
        "history": [
            {
                "item_id": 202,
                "chain_id": None,
                "project_id": None,
                "linked_task_id": None,
                "task_title": "热成像到货跟进",
                "result_text": "确认合同",
                "next_goal": "催货",
                "period_start": "2026-08-18",
            }
        ],
        "tasks": [],
    }
    out = _rule_match(draft, ctx, None)
    assert out["chain_suggestion"]["seed_item_id"] == 202
    assert out["chain_suggestion"]["chain_id"]


async def _ensure_users():
    async with async_session() as db:
        await init_rbac(db)
        for username, name, role_code in [
            ("t_chain_a", "链测试甲", "member"),
            ("t_chain_b", "链测试乙", "member"),
            ("t_chain_mgr", "链测试经理", "dept_head"),
        ]:
            r = await db.execute(select(User).where(User.username == username))
            u = r.scalar_one_or_none()
            if not u:
                u = User(
                    username=username,
                    name=name,
                    hashed_password=hash_password("TestPass2026"),
                    must_change_password=False,
                    is_active=True,
                )
                db.add(u)
                await db.flush()
            else:
                u.hashed_password = hash_password("TestPass2026")
                u.must_change_password = False
                u.is_active = True
            role = (await db.execute(select(Role).where(Role.code == role_code))).scalar_one()
            ur = await db.execute(
                select(UserRole).where(UserRole.user_id == u.id, UserRole.role_id == role.id)
            )
            if not ur.scalar_one_or_none():
                db.add(UserRole(user_id=u.id, role_id=role.id))
        await db.commit()


async def _login(client: AsyncClient, username: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "TestPass2026"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_ai_structure_and_chain_timeline_flow():
    await _ensure_users()
    transport = ASGITransport(app=app)
    uniq = uuid.uuid4().hex[:8]
    title = f"天网接入冲刺-{uniq}"
    this_monday = _monday_of(date.today())
    last_monday = this_monday - timedelta(days=7)
    last_end = last_monday + timedelta(days=6)
    this_end = this_monday + timedelta(days=6)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token_a = await _login(client, "t_chain_a")
        token_b = await _login(client, "t_chain_b")
        token_mgr = await _login(client, "t_chain_mgr")

        async with async_session() as db:
            user_a = (
                await db.execute(select(User).where(User.username == "t_chain_a"))
            ).scalar_one()
            project = Project(
                name=f"天网接入专项-{uniq}",
                project_type="software",
                level="normal",
                owner_id=user_a.id,
            )
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user_a.id, member_role="lead"))
            await db.commit()
            pid = project.id

        # 上周已提交（无 chain_id，供后续相似匹配 / 补链）
        week1 = await client.post(
            "/api/progress-reports",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "period_type": "week",
                "period_start": last_monday.isoformat(),
                "period_end": last_end.isoformat(),
                "submit": True,
                "force_edit_submitted": True,
                "items": [
                    {
                        "project_id": pid,
                        "task_title": title,
                        "result_text": "完成方案评审与点位清单",
                        "quant_value": "1",
                        "next_goal": "开始现场勘察",
                        "next_due_date": (last_monday + timedelta(days=10)).isoformat(),
                        "stuck_status": "none",
                        "risk_level": "normal",
                    }
                ],
            },
        )
        assert week1.status_code == 200, week1.text
        seed_item_id = week1.json()["items"][0]["id"]
        assert week1.json()["items"][0].get("chain_id") in (None, "")

        # 本周草稿触发结构化（无模型时规则兜底也应 200）
        struct = await client.post(
            "/api/progress-reports/ai-structure",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "period_type": "week",
                "period_start": this_monday.isoformat(),
                "period_end": this_end.isoformat(),
                "item": {
                    "task_title": title,
                    "result_text": "完成现场勘察 3 处",
                    "next_goal": "推进安装",
                    "next_due_date": (this_monday + timedelta(days=5)).isoformat(),
                    "stuck_status": "none",
                    "risk_level": "normal",
                },
            },
        )
        assert struct.status_code == 200, struct.text
        sj = struct.json()
        assert sj["enabled"] is True
        assert sj["structured"]["task_title"]
        assert sj["chain_suggestion"] is not None
        chain_id = sj["chain_suggestion"]["chain_id"]
        assert chain_id
        assert sj["chain_suggestion"].get("seed_item_id") == seed_item_id
        assert sj.get("project_suggestion") is not None
        assert sj["project_suggestion"]["project_id"] == pid

        # 补链历史
        link = await client.post(
            "/api/progress-reports/chains/link-history",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"chain_id": chain_id, "seed_item_id": seed_item_id},
        )
        assert link.status_code == 200, link.text

        # 本周提交带 chain_id + project
        week2 = await client.post(
            "/api/progress-reports",
            headers={"Authorization": f"Bearer {token_a}"},
            json={
                "period_type": "week",
                "period_start": this_monday.isoformat(),
                "period_end": this_end.isoformat(),
                "submit": True,
                "items": [
                    {
                        "project_id": pid,
                        "chain_id": chain_id,
                        "task_title": title,
                        "result_text": "完成现场勘察 3 处",
                        "quant_value": "3",
                        "next_goal": "推进安装",
                        "next_due_date": (this_monday + timedelta(days=5)).isoformat(),
                        "stuck_status": "none",
                        "risk_level": "normal",
                    }
                ],
            },
        )
        assert week2.status_code == 200, week2.text
        assert week2.json()["items"][0]["chain_id"] == chain_id
        assert week2.json()["items"][0]["project_id"] == pid

        # 本人可读时间线，应有 2 个节点
        tl = await client.get(
            f"/api/progress-reports/chains/{chain_id}",
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert tl.status_code == 200, tl.text
        nodes = tl.json()["nodes"]
        assert len(nodes) == 2
        assert nodes[0]["period_start"] == last_monday.isoformat()
        assert nodes[1]["period_start"] == this_monday.isoformat()

        # 他人无权限
        forbidden = await client.get(
            f"/api/progress-reports/chains/{chain_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert forbidden.status_code == 403

        # 管理者可看
        mgr_tl = await client.get(
            f"/api/progress-reports/chains/{chain_id}",
            headers={"Authorization": f"Bearer {token_mgr}"},
        )
        assert mgr_tl.status_code == 200, mgr_tl.text
        assert len(mgr_tl.json()["nodes"]) == 2

        # 他人不能补链别人的历史
        bad_link = await client.post(
            "/api/progress-reports/chains/link-history",
            headers={"Authorization": f"Bearer {token_b}"},
            json={"chain_id": chain_id, "seed_item_id": seed_item_id},
        )
        assert bad_link.status_code == 403
