#!/usr/bin/env python3
"""Live E2E smoke for 周报工作台 (board summary, matrix, workbench, timelines, ai-review)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

BASE = "http://127.0.0.1:8100/api"
LOG = Path("/home/aiworkbench/.cursor/debug-937f5b.log")
E2E_USER = os.environ.get("WORKBENCH_E2E_USER", "e2e_runner")
E2E_PASS = os.environ.get("WORKBENCH_E2E_PASS", "E2eRunner2026Ab")
FALLBACK_USERS = (
    (os.environ.get("WORKBENCH_E2E_USER", ""), os.environ.get("WORKBENCH_E2E_PASS", "")),
    ("admin", "InitAdmin2026"),
    ("manager", "InitManager2026"),
)


def dbg(hid: str, location: str, message: str, data: dict[str, Any] | None = None) -> None:
    # #region agent log
    payload = {
        "sessionId": "937f5b",
        "runId": "e2e-workbench",
        "hypothesisId": hid,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    # #endregion


def monday_of(d: date) -> date:
    return d - timedelta(days=(d.weekday()))


async def ensure_e2e_runner() -> None:
    """Ensure dedicated E2E account with report.view.all + at least one roster user."""
    from app.core.database import async_session
    from app.core.security import hash_password
    from app.models import Role, User, UserRole
    from app.services.init_service import init_rbac
    from app.services.report_roster import REPORT_ROSTER_USERNAMES

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

        # ensure first roster username exists so board/matrix非空
        roster_name = REPORT_ROSTER_USERNAMES[0]
        r2 = await db.execute(select(User).where(User.username == roster_name))
        roster_u = r2.scalar_one_or_none()
        if not roster_u:
            db.add(
                User(
                    username=roster_name,
                    name="RosterE2E",
                    hashed_password=hash_password(E2E_PASS),
                    must_change_password=False,
                    is_active=True,
                )
            )
        await db.commit()


def login(client: httpx.Client) -> tuple[str, dict[str, str]] | None:
    candidates = [(E2E_USER, E2E_PASS)]
    for u, p in FALLBACK_USERS:
        if u and p and (u, p) not in candidates:
            candidates.append((u, p))
    for username, password in candidates:
        r = client.post(f"{BASE}/auth/login", json={"username": username, "password": password})
        if r.status_code == 200:
            token = r.json()["access_token"]
            return token, {"Authorization": f"Bearer {token}"}
        dbg("H2", "wb:login_try", "fail", {"user": username, "status": r.status_code})
    return None


def main() -> int:
    # bootstrap runner account (safe: dedicated test user, not prod admin password)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    asyncio.run(ensure_e2e_runner())
    fails: list[str] = []
    with httpx.Client(timeout=90.0) as client:
        # H1: backend health
        r = client.get(f"{BASE}/health")
        dbg("H1", "wb:health", "health", {"status": r.status_code})
        if r.status_code != 200:
            fails.append("health")
            dbg("H1", "wb:summary", "abort", {"fails": fails})
            print(json.dumps({"fails": fails, "ok": False}, ensure_ascii=False))
            return 1

        # H2: login + permissions
        auth = login(client)
        if not auth:
            dbg("H2", "wb:login", "all_login_fail", {})
            fails.append("login")
            print(json.dumps({"fails": fails, "ok": False}, ensure_ascii=False))
            return 1
        _token, h = auth
        me = client.get(f"{BASE}/auth/me", headers=h)
        perms = set((me.json() or {}).get("permissions") or [])
        dbg("H2", "wb:login", "admin_ok", {"has_report_view_all": "report.view.all" in perms})
        if "report.view.all" not in perms:
            fails.append("missing_report_view_all")

        # H3: board summary mode (no embedded report body)
        br = client.get(
            f"{BASE}/progress-reports/board",
            headers=h,
            params={"period_type": "week", "mode": "summary"},
        )
        board = br.json() if br.status_code == 200 else {}
        people = board.get("people") or []
        with_report_body = sum(1 for p in people if p.get("report") is not None)
        dbg(
            "H3",
            "wb:board_summary",
            "board",
            {
                "status": br.status_code,
                "mode": board.get("mode"),
                "people_n": len(people),
                "with_report_body": with_report_body,
                "has_submit_rate": all("submit_rate" in p for p in people) if people else True,
            },
        )
        if br.status_code != 200:
            fails.append("board_summary_status")
        elif board.get("mode") != "summary":
            fails.append("board_mode_not_summary")
        elif with_report_body > 0:
            fails.append("board_summary_has_report_body")

        period_start = board.get("period_start") or monday_of(date.today()).isoformat()

        # H4: submission matrix
        mx = client.get(
            f"{BASE}/progress-reports/submission-matrix",
            headers=h,
            params={"weeks": 8, "anchor": period_start},
        )
        matrix = mx.json() if mx.status_code == 200 else {}
        dbg(
            "H4",
            "wb:matrix",
            "matrix",
            {
                "status": mx.status_code,
                "people_n": len(matrix.get("people") or []),
                "periods_n": len(matrix.get("periods") or []),
            },
        )
        if mx.status_code != 200:
            fails.append("submission_matrix")
        elif not matrix.get("people") or not matrix.get("periods"):
            fails.append("submission_matrix_empty")

        # pick a roster person for workbench drill-down
        target = next((p for p in people if p.get("submitted")), people[0] if people else None)
        if not target:
            dbg("H5", "wb:workbench", "no_people", {})
            fails.append("board_no_people")
        else:
            uid = target["user"]["id"]
            submitted = bool(target.get("submitted"))

            # H5: person workbench
            wb = client.get(
                f"{BASE}/progress-reports/users/{uid}/workbench",
                headers=h,
                params={"period_type": "week", "period_start": period_start},
            )
            workbench = wb.json() if wb.status_code == 200 else {}
            dbg(
                "H5",
                "wb:workbench",
                "workbench",
                {
                    "status": wb.status_code,
                    "submitted": workbench.get("submitted"),
                    "has_history": bool(workbench.get("history")),
                    "has_completion": "completion_stats" in workbench,
                    "has_review": workbench.get("phase1_review") is not None,
                    "review_reason": workbench.get("review_unavailable_reason"),
                },
            )
            if wb.status_code != 200:
                fails.append("workbench_status")
            if submitted:
                if not workbench.get("report"):
                    fails.append("workbench_missing_report")
                if not workbench.get("phase1_review"):
                    fails.append("workbench_missing_phase1_review")
            else:
                if workbench.get("phase1_review"):
                    fails.append("workbench_review_when_unsubmitted")
                if not workbench.get("review_unavailable_reason"):
                    fails.append("workbench_missing_unsubmitted_reason")

            # H6: timelines
            tl = client.get(
                f"{BASE}/progress-reports/users/{uid}/timelines",
                headers=h,
                params={"period_type": "week", "period_start": period_start, "include_history": True},
            )
            timelines = tl.json() if tl.status_code == 200 else {}
            chains = timelines.get("chains") or []
            dbg(
                "H6",
                "wb:timelines",
                "timelines",
                {
                    "status": tl.status_code,
                    "chains_n": len(chains),
                    "has_progress_nodes": any(c.get("progress_nodes") for c in chains),
                },
            )
            if tl.status_code != 200:
                fails.append("timelines_status")

            # H7: force ai-review when submitted
            rid = workbench.get("report", {}).get("id") if submitted else None
            if rid:
                ar = client.post(
                    f"{BASE}/progress-reports/{rid}/ai-review",
                    headers=h,
                    params={"force": True},
                )
                review = ar.json() if ar.status_code == 200 else {}
                dbg(
                    "H7",
                    "wb:ai_review",
                    "force_review",
                    {
                        "status": ar.status_code,
                        "source": review.get("source"),
                        "summary_len": len(review.get("summary") or ""),
                    },
                )
                if ar.status_code != 200 or not review.get("summary"):
                    fails.append("ai_review_force")

        # H8: board full mode (for overview compatibility)
        bf = client.get(
            f"{BASE}/progress-reports/board",
            headers=h,
            params={"period_type": "week", "period_start": period_start, "mode": "full"},
        )
        full = bf.json() if bf.status_code == 200 else {}
        full_with_body = sum(1 for p in (full.get("people") or []) if p.get("submitted") and p.get("report"))
        dbg(
            "H8",
            "wb:board_full",
            "full",
            {"status": bf.status_code, "submitted_with_body": full_with_body},
        )
        if bf.status_code != 200:
            fails.append("board_full_status")

        # H9: frontend assets (PC + DingTalk report board)
        for url in (
            "http://127.0.0.1/aiworkbench/",
            "http://127.0.0.1/aiworkbench/m/",
        ):
            fr = client.get(url)
            ok = fr.status_code == 200
            dbg("H9", f"wb:fe:{url}", "frontend", {"status": fr.status_code, "ok": ok, "len": len(fr.text)})
            if not ok:
                fails.append(f"fe:{url}")

        assets = Path("/home/aiworkbench/web-pc/dist/assets")
        bundles = sorted(assets.glob("ReportBoardView-*.js")) if assets.exists() else []
        if bundles:
            js_url = f"http://127.0.0.1/aiworkbench/assets/{bundles[-1].name}"
            jr = client.get(js_url)
            ok = jr.status_code == 200 and (
                "ReportPeopleBoard" in jr.text or "submit_rate" in jr.text
            )
            dbg("H9", "wb:fe:bundle", "report_board_js", {"url": js_url, "status": jr.status_code, "ok": ok})
            if not ok:
                fails.append("fe:ReportBoardView_bundle")
        else:
            dbg("H9", "wb:fe:bundle", "bundle_missing_on_disk", {})
            fails.append("fe:bundle_missing")

    summary = {"fails": fails, "ok": not fails, "checked": 9}
    dbg("H1", "wb:summary", "done", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
