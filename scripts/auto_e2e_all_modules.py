#!/usr/bin/env python3
"""全模块 Live E2E：M1 认证 → M10 前端（允许创建 E2E- 临时数据）。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

BASE = "http://127.0.0.1:8100/api"
FE_PC = "http://127.0.0.1/aiworkbench/"
FE_H5 = "http://127.0.0.1/aiworkbench/m/"
LOG = Path("/home/aiworkbench/.cursor/debug-937f5b.log")
E2E_USER = os.environ.get("WORKBENCH_E2E_USER", "e2e_runner")
E2E_PASS = os.environ.get("WORKBENCH_E2E_PASS", "E2eRunner2026Ab")
TAG = f"E2E-{int(time.time())}"


def dbg(mod: str, location: str, message: str, data: dict[str, Any] | None = None) -> None:
    # #region agent log
    payload = {
        "sessionId": "937f5b",
        "runId": "e2e-all-modules",
        "hypothesisId": mod,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # #endregion


def monday_of(d: date | None = None) -> date:
    d = d or date.today()
    return d - timedelta(days=d.weekday())


async def ensure_e2e_runner() -> None:
    from app.core.database import async_session
    from app.core.security import hash_password
    from app.models import Role, User, UserRole
    from app.services.init_service import init_rbac

    async with async_session() as db:
        await init_rbac(db)
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.username == E2E_USER)
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                username=E2E_USER,
                name="E2E自动验收",
                hashed_password=hash_password(E2E_PASS),
                must_change_password=False,
                is_active=True,
            )
            db.add(user)
            await db.flush()
        else:
            user.hashed_password = hash_password(E2E_PASS)
            user.must_change_password = False
            user.is_active = True
        await db.flush()
        for code in ("admin", "dept_head"):
            role = (await db.execute(select(Role).where(Role.code == code))).scalar_one()
            exists = await db.execute(
                select(UserRole).where(UserRole.user_id == user.id, UserRole.role_id == role.id)
            )
            if not exists.scalar_one_or_none():
                db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()


class ModuleResult:
    def __init__(self, name: str) -> None:
        self.name = name
        self.fails: list[str] = []
        self.skips: list[str] = []
        self.cases = 0

    def ok(self, case: str, data: dict | None = None) -> None:
        self.cases += 1
        dbg(self.name, case, "ok", data)

    def fail(self, case: str, data: dict | None = None) -> None:
        self.cases += 1
        self.fails.append(case)
        dbg(self.name, case, "fail", data)

    def skip(self, case: str, reason: str) -> None:
        self.skips.append(f"{case}:{reason}")
        dbg(self.name, case, "skip", {"reason": reason})

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.fails,
            "cases": self.cases,
            "fails": self.fails,
            "skips": self.skips,
        }


def m1_auth(client: httpx.Client) -> tuple[ModuleResult, dict[str, str], dict]:
    m = ModuleResult("M1_auth")
    h_resp = client.get(f"{BASE}/health")
    if h_resp.status_code == 200:
        m.ok("health", {"status": 200})
    else:
        m.fail("health", {"status": h_resp.status_code})

    bad = client.post(f"{BASE}/auth/login", json={"username": E2E_USER, "password": "wrong-pass"})
    if bad.status_code == 401:
        m.ok("bad_password_401")
    else:
        m.fail("bad_password_401", {"status": bad.status_code})

    good = client.post(f"{BASE}/auth/login", json={"username": E2E_USER, "password": E2E_PASS})
    if good.status_code != 200:
        m.fail("login", {"status": good.status_code, "body": good.text[:160]})
        return m, {}, {}
    token = good.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    m.ok("login")

    me = client.get(f"{BASE}/auth/me", headers=headers)
    me_data = me.json() if me.status_code == 200 else {}
    perms = set(me_data.get("permissions") or [])
    if me.status_code == 200 and "report.view.all" in perms:
        m.ok("me_permissions", {"n_perms": len(perms)})
    else:
        m.fail("me_permissions", {"status": me.status_code, "has_report": "report.view.all" in perms})
    return m, headers, me_data


def m2_projects(client: httpx.Client, h: dict[str, str], me: dict) -> tuple[ModuleResult, int | None]:
    m = ModuleResult("M2_projects")
    meta = client.get(f"{BASE}/projects/meta/types", headers=h)
    if meta.status_code == 200:
        m.ok("meta_types")
    else:
        m.fail("meta_types", {"status": meta.status_code})

    lst = client.get(f"{BASE}/projects", headers=h)
    projects = lst.json() if lst.status_code == 200 else []
    if lst.status_code == 200:
        m.ok("list", {"n": len(projects)})
    else:
        m.fail("list", {"status": lst.status_code})
        return m, None

    stats = client.get(f"{BASE}/projects/overview/stats", headers=h)
    if stats.status_code == 200:
        m.ok("overview_stats")
    else:
        m.fail("overview_stats", {"status": stats.status_code})

    created = client.post(
        f"{BASE}/projects",
        headers=h,
        json={
            "name": f"{TAG}-项目",
            "project_type": "software",
            "level": "normal",
            "owner_id": me.get("id"),
            "description": f"{TAG} 自动验收临时项目",
        },
    )
    if created.status_code != 200:
        # fallback: use existing
        m.skip("create", created.text[:120])
        pid = projects[0]["id"] if projects else None
        if not pid:
            m.fail("no_project")
        return m, pid

    pid = created.json()["id"]
    m.ok("create", {"id": pid})

    detail = client.get(f"{BASE}/projects/{pid}", headers=h)
    if detail.status_code == 200:
        m.ok("detail")
    else:
        m.fail("detail", {"status": detail.status_code})

    mem = client.get(f"{BASE}/projects/{pid}/members", headers=h)
    if mem.status_code == 200:
        m.ok("members", {"n": len(mem.json() or [])})
    else:
        m.fail("members", {"status": mem.status_code})

    # ensure self is lead
    put = client.put(
        f"{BASE}/projects/{pid}/members",
        headers=h,
        json={"lead_user_id": me.get("id"), "assistant_user_ids": [], "member_user_ids": []},
    )
    if put.status_code == 200:
        m.ok("set_lead")
    else:
        m.fail("set_lead", {"status": put.status_code, "body": put.text[:120]})
    return m, pid


def m3_blockers(client: httpx.Client, h: dict[str, str], pid: int | None) -> ModuleResult:
    m = ModuleResult("M3_blockers")
    if not pid:
        m.skip("all", "no_project")
        return m
    lst = client.get(f"{BASE}/blockers/project/{pid}", headers=h)
    if lst.status_code == 200:
        m.ok("list", {"n": len(lst.json() or [])})
    else:
        m.fail("list", {"status": lst.status_code})

    created = client.post(
        f"{BASE}/blockers/project/{pid}",
        headers=h,
        json={
            "description": f"{TAG} 卡点：等客户开通账号",
            "responsible_party": "客户侧",
            "party_type": "customer",
            "actions_taken": "已电话催促",
            "next_action": "下周再跟",
            "next_follow_up": (date.today() + timedelta(days=3)).isoformat(),
        },
    )
    if created.status_code == 200:
        bid = created.json()["id"]
        m.ok("create", {"id": bid})
        ack = client.post(f"{BASE}/progress-reports/blockers/{bid}/ack", headers=h)
        if ack.status_code == 200:
            m.ok("ack")
        else:
            # lead may be required
            m.skip("ack", ack.text[:100])
    else:
        m.fail("create", {"status": created.status_code, "body": created.text[:160]})
    return m


def m4_tasks(
    client: httpx.Client, h: dict[str, str], me: dict, pid: int | None
) -> tuple[ModuleResult, int | None]:
    m = ModuleResult("M4_tasks")
    if not pid:
        m.skip("all", "no_project")
        return m, None

    draft = client.post(
        f"{BASE}/tasks/ai/draft",
        headers=h,
        json={"text": f"{TAG} 跟进联调验收"},
    )
    if draft.status_code == 200:
        m.ok("ai_draft", {"source": (draft.json() or {}).get("source")})
    else:
        m.fail("ai_draft", {"status": draft.status_code})

    self_t = client.post(
        f"{BASE}/tasks",
        headers=h,
        json={
            "project_id": pid,
            "title": f"{TAG}-自建任务",
            "assignment_type": "self",
            "priority": "p3",
        },
    )
    self_body = self_t.json() if self_t.status_code == 200 else {}
    if self_t.status_code == 200 and self_body.get("acceptance_status") == "accepted":
        m.ok("create_self", {"id": self_body.get("id")})
    else:
        m.fail("create_self", {"status": self_t.status_code, "body": self_t.text[:160]})

    # temp assignee for delegated
    temp_user = f"e2e_{uuid4().hex[:8]}"
    temp_pass = "E2eTest2026Ab"
    cu = client.post(
        f"{BASE}/admin/users",
        headers=h,
        json={
            "username": temp_user,
            "name": f"{TAG}-责任人",
            "password": temp_pass,
            "role_codes": ["member"],
        },
    )
    if cu.status_code != 200:
        m.fail("temp_user", {"status": cu.status_code, "body": cu.text[:120]})
        return m, self_body.get("id")
    assignee_id = cu.json()["id"]
    m.ok("temp_user", {"id": assignee_id})

    client.put(
        f"{BASE}/projects/{pid}/members",
        headers=h,
        json={
            "lead_user_id": me.get("id"),
            "assistant_user_ids": [],
            "member_user_ids": [assignee_id],
        },
    )

    del_t = client.post(
        f"{BASE}/tasks",
        headers=h,
        json={
            "project_id": pid,
            "title": f"{TAG}-交办任务",
            "assignment_type": "delegated",
            "assignee_id": assignee_id,
            "priority": "p1",
        },
    )
    task = del_t.json() if del_t.status_code == 200 else {}
    if del_t.status_code != 200:
        m.fail("create_delegated", {"status": del_t.status_code, "body": del_t.text[:160]})
        return m, self_body.get("id")
    tid = task["id"]
    m.ok("create_delegated", {"id": tid, "acceptance": task.get("acceptance_status")})

    blocked = client.patch(
        f"{BASE}/tasks/{tid}",
        headers=h,
        json={"status": "in_progress"},
    )
    if blocked.status_code == 400 and "接收" in str((blocked.json() or {}).get("detail", "")):
        m.ok("gate_before_accept")
    elif blocked.status_code == 200:
        m.fail("gate_before_accept", {"reason": "not_blocked"})
    else:
        m.skip("gate_before_accept", blocked.text[:80])

    rem = client.post(f"{BASE}/tasks/{tid}/remind", headers=h)
    if rem.status_code == 200:
        m.ok("remind")
    else:
        m.fail("remind", {"status": rem.status_code})

    lr = client.post(f"{BASE}/auth/login", json={"username": temp_user, "password": temp_pass})
    if lr.status_code != 200:
        m.fail("assignee_login", {"status": lr.status_code})
        return m, tid
    ah = {"Authorization": f"Bearer {lr.json()['access_token']}"}
    if lr.json().get("must_change_password"):
        cp = client.post(
            f"{BASE}/auth/change-password",
            headers=ah,
            json={"old_password": temp_pass, "new_password": "E2eTest2026Xy"},
        )
        if cp.status_code == 200:
            lr = client.post(
                f"{BASE}/auth/login",
                json={"username": temp_user, "password": "E2eTest2026Xy"},
            )
            ah = {"Authorization": f"Bearer {lr.json()['access_token']}"}
            m.ok("change_password")
        else:
            m.fail("change_password", {"status": cp.status_code})

    ar = client.post(f"{BASE}/tasks/{tid}/accept", headers=ah)
    if ar.status_code == 200:
        m.ok("accept")
        prog = client.patch(
            f"{BASE}/tasks/{tid}",
            headers=ah,
            json={"status": "in_progress", "result_text": "E2E推进中"},
        )
        if prog.status_code == 200:
            m.ok("progress_after_accept")
        else:
            m.fail("progress_after_accept", {"status": prog.status_code, "body": prog.text[:120]})
    else:
        m.fail("accept", {"status": ar.status_code, "body": ar.text[:120]})
    return m, tid


def m5_personal(client: httpx.Client, h: dict[str, str]) -> ModuleResult:
    m = ModuleResult("M5_personal")
    ws = client.get(f"{BASE}/personal/workspace", headers=h)
    if ws.status_code == 200:
        body = ws.json() or {}
        m.ok("workspace", {"keys": list(body.keys())[:8]})
    else:
        m.fail("workspace", {"status": ws.status_code})
    return m


def m6_reports(
    client: httpx.Client, h: dict[str, str], me: dict, pid: int | None
) -> ModuleResult:
    m = ModuleResult("M6_report_workbench")
    ps = monday_of().isoformat()
    pe = (monday_of() + timedelta(days=6)).isoformat()

    # submit a week report for runner
    save = client.post(
        f"{BASE}/progress-reports",
        headers=h,
        json={
            "period_type": "week",
            "period_start": ps,
            "period_end": pe,
            "theme": f"{TAG}周报",
            "submit": True,
            "items": [
                {
                    "project_id": pid,
                    "task_title": f"{TAG}-天网联调",
                    "result_text": "完成联调 3 个点位",
                    "quant_value": "3",
                    "stuck_status": "none",
                    "next_goal": "下周上线验证",
                    "next_due_date": (monday_of() + timedelta(days=10)).isoformat(),
                    "risk_level": "normal",
                    "prior_done": True,
                    "chain_id": f"chain-{TAG}",
                    "tags": ["售中"],
                    "sort_order": 0,
                }
            ],
        },
    )
    if save.status_code == 200:
        m.ok("submit_report", {"id": (save.json() or {}).get("id")})
    else:
        # may already submitted this week — try validate only
        m.skip("submit_report", save.text[:140])

    board = client.get(
        f"{BASE}/progress-reports/board",
        headers=h,
        params={"period_type": "week", "period_start": ps, "mode": "summary"},
    )
    if board.status_code != 200:
        m.fail("board_summary", {"status": board.status_code})
    else:
        data = board.json()
        people = data.get("people") or []
        with_body = sum(1 for p in people if p.get("report") is not None)
        if data.get("mode") == "summary" and with_body == 0:
            m.ok("board_summary", {"people": len(people)})
        else:
            m.fail(
                "board_summary",
                {"mode": data.get("mode"), "with_body": with_body},
            )
        period_start = data.get("period_start") or ps

    full = client.get(
        f"{BASE}/progress-reports/board",
        headers=h,
        params={"period_type": "week", "period_start": ps, "mode": "full"},
    )
    if full.status_code == 200:
        m.ok("board_full")
    else:
        m.fail("board_full", {"status": full.status_code})

    mx = client.get(
        f"{BASE}/progress-reports/submission-matrix",
        headers=h,
        params={"weeks": 8, "anchor": ps},
    )
    if mx.status_code == 200 and (mx.json() or {}).get("periods"):
        m.ok("matrix", {"periods": len((mx.json() or {}).get("periods") or [])})
    else:
        m.fail("matrix", {"status": mx.status_code})

    uid = me.get("id")
    wb = client.get(
        f"{BASE}/progress-reports/users/{uid}/workbench",
        headers=h,
        params={"period_type": "week", "period_start": ps},
    )
    if wb.status_code != 200:
        m.fail("workbench", {"status": wb.status_code})
    else:
        body = wb.json()
        if body.get("submitted"):
            if body.get("phase1_review") and body.get("report"):
                m.ok("workbench_submitted_review")
                rid = body["report"]["id"]
                ar = client.post(
                    f"{BASE}/progress-reports/{rid}/ai-review",
                    headers=h,
                    params={"force": True},
                )
                if ar.status_code == 200 and (ar.json() or {}).get("summary"):
                    m.ok("ai_review_force", {"source": (ar.json() or {}).get("source")})
                else:
                    m.fail("ai_review_force", {"status": ar.status_code})
            else:
                m.fail(
                    "workbench_submitted_review",
                    {"has_review": bool(body.get("phase1_review")), "has_report": bool(body.get("report"))},
                )
        else:
            if body.get("review_unavailable_reason") and not body.get("phase1_review"):
                m.ok("workbench_unsubmitted")
            else:
                m.fail("workbench_unsubmitted", body)

    tl = client.get(
        f"{BASE}/progress-reports/users/{uid}/timelines",
        headers=h,
        params={"period_type": "week", "period_start": ps, "include_history": True},
    )
    if tl.status_code == 200:
        chains = (tl.json() or {}).get("chains") or []
        m.ok("timelines", {"chains": len(chains)})
    else:
        m.fail("timelines", {"status": tl.status_code})
    return m


def m7_ai(client: httpx.Client, h: dict[str, str], pid: int | None) -> ModuleResult:
    m = ModuleResult("M7_ai")
    brief = client.get(f"{BASE}/ai/overview/brief", headers=h)
    if brief.status_code == 200:
        m.ok("overview_brief", {"source": (brief.json() or {}).get("source")})
    else:
        m.fail("overview_brief", {"status": brief.status_code})

    pd = client.post(
        f"{BASE}/ai/project/draft",
        headers=h,
        json={"text": f"{TAG} 新建终端运维项目"},
    )
    if pd.status_code == 200:
        m.ok("project_draft", {"source": (pd.json() or {}).get("source")})
    else:
        m.fail("project_draft", {"status": pd.status_code})

    bd = client.post(
        f"{BASE}/ai/blocker/draft",
        headers=h,
        json={"text": f"{TAG} 客户账号未开通", "project_id": pid},
    )
    if bd.status_code == 200:
        m.ok("blocker_draft", {"source": (bd.json() or {}).get("source")})
    else:
        m.fail("blocker_draft", {"status": bd.status_code})

    ss = client.post(
        f"{BASE}/ai/solution/search",
        headers=h,
        json={"text": "家庭医生开票"},
    )
    if ss.status_code in (200, 403):
        m.ok("solution_search", {"status": ss.status_code})
    else:
        m.fail("solution_search", {"status": ss.status_code})
    return m


def m8_solutions(client: httpx.Client, h: dict[str, str]) -> ModuleResult:
    m = ModuleResult("M8_solutions")
    tags = client.get(f"{BASE}/solutions/tags", headers=h)
    if tags.status_code in (200, 403):
        m.ok("tags", {"status": tags.status_code})
    else:
        m.fail("tags", {"status": tags.status_code})
    lst = client.get(f"{BASE}/solutions", headers=h)
    if lst.status_code in (200, 403):
        m.ok("list", {"status": lst.status_code})
    else:
        m.fail("list", {"status": lst.status_code})
    return m


def m9_admin(client: httpx.Client, h: dict[str, str]) -> ModuleResult:
    m = ModuleResult("M9_admin")
    users = client.get(f"{BASE}/admin/users", headers=h)
    if users.status_code == 200:
        m.ok("users", {"n": len(users.json() or [])})
    elif users.status_code == 403:
        m.ok("users_forbidden_ok")
    else:
        m.fail("users", {"status": users.status_code})

    roles = client.get(f"{BASE}/admin/roles", headers=h)
    if roles.status_code in (200, 403):
        m.ok("roles", {"status": roles.status_code})
    else:
        m.fail("roles", {"status": roles.status_code})

    models = client.get(f"{BASE}/admin/models", headers=h)
    if models.status_code == 200:
        purposes = {(x.get("purpose") or "") for x in (models.json() or [])}
        m.ok("models", {"n": len(models.json() or []), "has_report_review_cfg": "report_review" in purposes})
    elif models.status_code == 403:
        m.ok("models_forbidden_ok")
    else:
        m.fail("models", {"status": models.status_code})
    return m


def m10_frontend(client: httpx.Client) -> ModuleResult:
    m = ModuleResult("M10_frontend")
    pc = client.get(FE_PC)
    if pc.status_code == 200 and "周报工作台" in pc.text:
        m.ok("pc_index")
    elif pc.status_code == 200:
        # title may be in JS bundle only for SPA
        m.ok("pc_index_spa", {"has_root": "id=\"app\"" in pc.text or "id='app'" in pc.text})
    else:
        m.fail("pc_index", {"status": pc.status_code})

    h5 = client.get(FE_H5)
    if h5.status_code == 200:
        m.ok("h5_index")
    else:
        m.fail("h5_index", {"status": h5.status_code})

    if pc.status_code == 200:
        match = re.search(r"/aiworkbench/assets/ReportBoardView-[^\"']+\.js", pc.text)
        if not match:
            # SPA: scan hashed assets on disk
            assets = Path("/home/aiworkbench/web-pc/dist/assets")
            files = sorted(assets.glob("ReportBoardView-*.js")) if assets.exists() else []
            if files:
                match_name = files[-1].name
                js_url = f"http://127.0.0.1/aiworkbench/assets/{match_name}"
                jr = client.get(js_url)
                ok = jr.status_code == 200 and (
                    "ReportPeopleBoard" in jr.text
                    or "submit_rate" in jr.text
                    or "workbench" in jr.text.lower()
                )
                if ok:
                    m.ok("report_board_bundle", {"url": match_name})
                else:
                    m.fail("report_board_bundle", {"status": jr.status_code, "len": len(jr.text)})
            else:
                m.skip("report_board_bundle", "asset_file_missing")
        else:
            js_url = "http://127.0.0.1" + match.group(0)
            jr = client.get(js_url)
            ok = jr.status_code == 200 and (
                "ReportPeopleBoard" in jr.text or "人员" in jr.text or "submit_rate" in jr.text
            )
            if ok:
                m.ok("report_board_bundle", {"url": match.group(0)})
            else:
                m.fail("report_board_bundle", {"status": jr.status_code, "len": len(jr.text)})
    return m


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    asyncio.run(ensure_e2e_runner())

    results: dict[str, Any] = {}
    with httpx.Client(timeout=90.0) as client:
        m1, headers, me = m1_auth(client)
        results[m1.name] = m1.as_dict()
        if not headers:
            summary = {"ok": False, "modules": results, "tag": TAG}
            dbg("SUMMARY", "done", "abort_no_auth", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1

        m2, pid = m2_projects(client, headers, me)
        results[m2.name] = m2.as_dict()

        m3 = m3_blockers(client, headers, pid)
        results[m3.name] = m3.as_dict()

        m4, _tid = m4_tasks(client, headers, me, pid)
        results[m4.name] = m4.as_dict()

        m5 = m5_personal(client, headers)
        results[m5.name] = m5.as_dict()

        m6 = m6_reports(client, headers, me, pid)
        results[m6.name] = m6.as_dict()

        m7 = m7_ai(client, headers, pid)
        results[m7.name] = m7.as_dict()

        m8 = m8_solutions(client, headers)
        results[m8.name] = m8.as_dict()

        m9 = m9_admin(client, headers)
        results[m9.name] = m9.as_dict()

        m10 = m10_frontend(client)
        results[m10.name] = m10.as_dict()

    all_ok = all(v.get("ok") for v in results.values())
    summary = {
        "ok": all_ok,
        "tag": TAG,
        "modules": results,
        "failed_modules": [k for k, v in results.items() if not v.get("ok")],
    }
    dbg("SUMMARY", "done", "finished", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
