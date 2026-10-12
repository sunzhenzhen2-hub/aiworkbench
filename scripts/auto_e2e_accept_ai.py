#!/usr/bin/env python3
"""Live API smoke for accept flow + AI (writes NDJSON debug log)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8100/api"
LOG = Path("/home/aiworkbench/.cursor/debug-937f5b.log")
E2E_USER = "e2e_runner"
E2E_PASS = "E2eRunner2026Ab"
PASS = E2E_PASS  # compat


def dbg(hid: str, location: str, message: str, data: dict | None = None) -> None:
    # #region agent log
    payload = {
        "sessionId": "937f5b",
        "runId": "auto-e2e-accept",
        "hypothesisId": hid,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # #endregion


def main() -> int:
    # prefer dedicated e2e_runner (created by all-modules script / bootstrap)
    fails: list[str] = []
    with httpx.Client(timeout=60.0) as client:
        # health
        r = client.get(f"{BASE}/health")
        dbg("A", "e2e:health", "health", {"status": r.status_code, "body": r.text[:80]})
        if r.status_code != 200:
            fails.append("health")

        # login e2e_runner then fallbacks
        token = None
        for username, password in (
            (E2E_USER, E2E_PASS),
            ("admin", "InitAdmin2026"),
            ("manager", "InitManager2026"),
        ):
            r = client.post(f"{BASE}/auth/login", json={"username": username, "password": password})
            if r.status_code == 200:
                token = r.json()["access_token"]
                dbg("A", "e2e:login", "login_ok", {"user": username, "must_change": r.json().get("must_change_password")})
                break
            dbg("A", "e2e:login", "login_fail", {"user": username, "status": r.status_code})
        if not token:
            fails.append("admin_login")
            print("FAIL", fails)
            return 1
        h = {"Authorization": f"Bearer {token}"}

        # list projects
        pr = client.get(f"{BASE}/projects", headers=h)
        projects = pr.json() if pr.status_code == 200 else []
        dbg("A", "e2e:projects", "list_projects", {"status": pr.status_code, "n": len(projects)})
        if not projects:
            fails.append("no_projects")
            print("FAIL", fails)
            return 1
        project = next((p for p in projects if p.get("name") != "日常任务"), projects[0])
        pid = project["id"]

        # members
        mr = client.get(f"{BASE}/projects/{pid}/members", headers=h)
        members = mr.json() if mr.status_code == 200 else []
        dbg("A", "e2e:members", "members", {"status": mr.status_code, "n": len(members)})
        # pick assignee != admin if possible
        me = client.get(f"{BASE}/auth/me", headers=h)
        me_id = me.json().get("id") if me.status_code == 200 else 1
        assignees = [m["user"]["id"] for m in members if m.get("user") and m["user"]["id"] != me_id]
        if not assignees:
            # self-create path only
            assignee_id = me_id
        else:
            assignee_id = assignees[0]

        # AI draft
        dr = client.post(
            f"{BASE}/tasks/ai/draft",
            headers=h,
            json={"text": f"交办跟进{project.get('name','项目')}联调验收"},
        )
        draft = dr.json() if dr.status_code == 200 else {}
        dbg(
            "C",
            "e2e:ai_draft",
            "task_draft",
            {
                "status": dr.status_code,
                "source": draft.get("source"),
                "assignment_type": draft.get("assignment_type"),
            },
        )
        if dr.status_code != 200:
            fails.append("ai_draft")

        # overview brief
        br = client.get(f"{BASE}/ai/overview/brief", headers=h)
        brief = br.json() if br.status_code == 200 else {}
        dbg(
            "C",
            "e2e:brief",
            "overview_brief",
            {"status": br.status_code, "source": brief.get("source"), "summary_len": len(brief.get("summary") or "")},
        )
        if br.status_code != 200:
            fails.append("overview_brief")

        # project draft
        pd = client.post(
            f"{BASE}/ai/project/draft",
            headers=h,
            json={"text": "新建终端运维项目处理中毒事件"},
        )
        dbg(
            "C",
            "e2e:project_draft",
            "project_draft",
            {"status": pd.status_code, "source": (pd.json() or {}).get("source") if pd.status_code == 200 else None},
        )
        if pd.status_code != 200:
            fails.append("project_draft")

        # blocker draft
        bd = client.post(
            f"{BASE}/ai/blocker/draft",
            headers=h,
            json={"text": "客户侧账号未开通导致无法联调，下周跟进", "project_id": pid},
        )
        dbg(
            "C",
            "e2e:blocker_draft",
            "blocker_draft",
            {"status": bd.status_code, "source": (bd.json() or {}).get("source") if bd.status_code == 200 else None},
        )
        if bd.status_code != 200:
            fails.append("blocker_draft")

        # solution search (may 403 if no perm - admin should have)
        ss = client.post(
            f"{BASE}/ai/solution/search",
            headers=h,
            json={"text": "家庭医生开票怎么处理"},
        )
        dbg(
            "C",
            "e2e:solution_search",
            "solution_search",
            {"status": ss.status_code, "hits": len((ss.json() or {}).get("items") or []) if ss.status_code == 200 else -1},
        )
        if ss.status_code not in (200, 403):
            fails.append("solution_search")

        # Create temp assignee with known password for full accept loop
        from uuid import uuid4

        temp_user = f"e2e_{uuid4().hex[:8]}"
        temp_pass = "E2eTest2026Ab"
        temp_pass2 = "E2eTest2026Xy"
        cu = client.post(
            f"{BASE}/admin/users",
            headers=h,
            json={
                "username": temp_user,
                "name": "自动验收临时责任人",
                "password": temp_pass,
                "role_codes": ["member"],
            },
        )
        dbg("A", "e2e:temp_user", "create_user", {"status": cu.status_code, "body": cu.text[:160]})
        if cu.status_code != 200:
            fails.append("temp_user")
            dbg("A", "e2e:summary", "done", {"fails": fails, "ok": False})
            print(json.dumps({"fails": fails, "ok": False}, ensure_ascii=False))
            return 1
        assignee_id = cu.json()["id"]

        lead_id = None
        assistant_ids: list[int] = []
        member_ids: list[int] = []
        for m in members:
            role = m.get("member_role")
            uid = m.get("user", {}).get("id")
            if not uid:
                continue
            if role == "lead":
                lead_id = uid
            elif role == "assistant":
                assistant_ids.append(uid)
            else:
                member_ids.append(uid)
        if not lead_id:
            lead_id = project.get("owner", {}).get("id") or me_id
        if assignee_id not in member_ids and assignee_id != lead_id:
            member_ids.append(assignee_id)

        mem_put = client.put(
            f"{BASE}/projects/{pid}/members",
            headers=h,
            json={
                "lead_user_id": lead_id,
                "assistant_user_ids": assistant_ids,
                "member_user_ids": member_ids,
            },
        )
        dbg(
            "A",
            "e2e:temp_user",
            "members_put",
            {"status": mem_put.status_code, "detail": mem_put.text[:200] if mem_put.status_code >= 400 else "ok"},
        )
        if mem_put.status_code >= 400:
            fails.append("members_put")

        cr = client.post(
            f"{BASE}/tasks",
            headers=h,
            json={
                "project_id": pid,
                "title": f"自动验收交办-{int(time.time())}",
                "assignment_type": "delegated",
                "assignee_id": assignee_id,
                "priority": "p1",
            },
        )
        task = cr.json() if cr.status_code == 200 else {}
        dbg(
            "A",
            "e2e:create_delegated",
            "created",
            {
                "status": cr.status_code,
                "acceptance": task.get("acceptance_status"),
                "task_id": task.get("id"),
                "detail": cr.text[:200] if cr.status_code >= 400 else None,
            },
        )
        if cr.status_code != 200:
            fails.append("create_delegated")
            print(json.dumps({"fails": fails, "ok": False, "detail": cr.text[:300]}, ensure_ascii=False))
            return 1
        tid = task["id"]

        # progress should fail before accept
        bad = client.patch(
            f"{BASE}/tasks/{tid}",
            headers=h,
            json={"status": "in_progress"},
        )
        dbg(
            "B",
            "e2e:gate_before",
            "patch_before_accept",
            {"status": bad.status_code, "detail": (bad.json() or {}).get("detail") if bad.status_code >= 400 else None},
        )
        if bad.status_code == 200:
            fails.append("gate_not_blocking")
        elif bad.status_code == 400 and "接收" in str((bad.json() or {}).get("detail", "")):
            dbg("B", "e2e:gate_before", "blocked_ok", {"ok": True})

        rem = client.post(f"{BASE}/tasks/{tid}/remind", headers=h)
        dbg("E", "e2e:remind", "remind", {"status": rem.status_code})
        if rem.status_code != 200:
            fails.append("remind")

        # login as temp assignee, change password if required, accept, progress, breakdown
        lr = client.post(f"{BASE}/auth/login", json={"username": temp_user, "password": temp_pass})
        dbg("B", "e2e:accept", "assignee_login", {"status": lr.status_code, "must_change": (lr.json() or {}).get("must_change_password")})
        if lr.status_code != 200:
            fails.append("assignee_login")
        else:
            ah = {"Authorization": f"Bearer {lr.json()['access_token']}"}
            if lr.json().get("must_change_password"):
                cp = client.post(
                    f"{BASE}/auth/change-password",
                    headers=ah,
                    json={"old_password": temp_pass, "new_password": temp_pass2},
                )
                dbg("B", "e2e:accept", "change_password", {"status": cp.status_code})
                if cp.status_code != 200:
                    fails.append("change_password")
                else:
                    lr2 = client.post(
                        f"{BASE}/auth/login",
                        json={"username": temp_user, "password": temp_pass2},
                    )
                    ah = {"Authorization": f"Bearer {lr2.json()['access_token']}"}
                    temp_pass = temp_pass2

            ar = client.post(f"{BASE}/tasks/{tid}/accept", headers=ah)
            dbg(
                "B",
                "e2e:accept",
                "accept_as_assignee",
                {
                    "status": ar.status_code,
                    "acceptance": (ar.json() or {}).get("acceptance_status") if ar.status_code == 200 else ar.text[:160],
                },
            )
            if ar.status_code != 200:
                fails.append("accept")
            else:
                ok = client.patch(
                    f"{BASE}/tasks/{tid}",
                    headers=ah,
                    json={"status": "in_progress", "result_text": "自动验收推进"},
                )
                dbg(
                    "B",
                    "e2e:progress_after",
                    "patch_after_accept",
                    {"status": ok.status_code, "task_status": (ok.json() or {}).get("status")},
                )
                if ok.status_code != 200:
                    fails.append("progress_after_accept")
                brk = client.post(f"{BASE}/tasks/{tid}/ai/breakdown", headers=ah)
                dbg(
                    "C",
                    "e2e:breakdown",
                    "breakdown",
                    {
                        "status": brk.status_code,
                        "items": len((brk.json() or {}).get("items") or []) if brk.status_code == 200 else -1,
                    },
                )
                if brk.status_code != 200:
                    fails.append("breakdown")

        # reassign to another existing member if any, else create second temp
        other = next((uid for uid in member_ids if uid not in {assignee_id, lead_id}), None)
        if other:
            ch = client.patch(
                f"{BASE}/tasks/{tid}/owner",
                headers=h,
                json={"assignee_id": other},
            )
            dbg(
                "E",
                "e2e:reassign",
                "reassign",
                {
                    "status": ch.status_code,
                    "acceptance": (ch.json() or {}).get("acceptance_status"),
                },
            )
            if ch.status_code != 200 or (ch.json() or {}).get("acceptance_status") != "pending":
                fails.append("reassign_reset")

        # self task sanity
        self_cr = client.post(
            f"{BASE}/tasks",
            headers=h,
            json={
                "project_id": pid,
                "title": f"自动验收自建-{int(time.time())}",
                "assignment_type": "self",
                "priority": "p3",
            },
        )
        dbg(
            "A",
            "e2e:create_self",
            "created_self",
            {
                "status": self_cr.status_code,
                "acceptance": (self_cr.json() or {}).get("acceptance_status"),
            },
        )
        if self_cr.status_code != 200 or (self_cr.json() or {}).get("acceptance_status") != "accepted":
            fails.append("create_self")

        # frontend assets over nginx
        for url in (
            "http://127.0.0.1/aiworkbench/",
            "http://127.0.0.1/aiworkbench/m/",
        ):
            fr = client.get(url)
            ok = fr.status_code == 200
            dbg("D", f"e2e:fe:{url}", "frontend", {"status": fr.status_code, "ok": ok, "len": len(fr.text)})
            if not ok:
                fails.append(f"fe:{url}")
        # TaskAssignPanel bundle from disk
        from pathlib import Path as _P

        assets = _P("/home/aiworkbench/web-pc/dist/assets")
        tap = sorted(assets.glob("TaskAssignPanel-*.js")) if assets.exists() else []
        if tap:
            js_url = f"http://127.0.0.1/aiworkbench/assets/{tap[-1].name}"
            fr = client.get(js_url)
            ok = fr.status_code == 200 and ("一键接收" in fr.text or "接收" in fr.text)
            dbg("D", "e2e:fe:TaskAssignPanel", "frontend", {"status": fr.status_code, "ok": ok})
            if not ok:
                fails.append("fe:TaskAssignPanel")
        else:
            fails.append("fe:TaskAssignPanel_missing")

    summary = {"fails": fails, "ok": not fails}
    dbg("A", "e2e:summary", "done", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
