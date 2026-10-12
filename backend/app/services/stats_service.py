"""轻量数据分析：人员工作视图与项目卡点分布（stats.view）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Blocker, ProgressReport, Project, Task, User
from app.services.project_service import list_visible_project_rows
from app.services.report_quality import monday_of
from app.services.report_roster import list_report_roster_users


async def people_stats(db: AsyncSession, user: User, permissions: set[str]) -> dict[str, Any]:
    """近 4 周周报提交率、逾期任务、未关卡点（负责人维度）。"""
    roster = await list_report_roster_users(db)
    today = date.today()
    this_monday = monday_of(today)
    weeks = [this_monday - timedelta(days=7 * i) for i in range(4)]
    week_set = set(weeks)

    if not roster:
        return {"weeks": [w.isoformat() for w in weeks], "people": []}

    user_ids = [u.id for u in roster]
    reports = list(
        (
            await db.execute(
                select(ProgressReport).where(
                    ProgressReport.user_id.in_(user_ids),
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start.in_(weeks),
                    ProgressReport.status == "submitted",
                )
            )
        )
        .scalars()
        .all()
    )
    submitted_map: dict[int, set[date]] = {uid: set() for uid in user_ids}
    for r in reports:
        if r.period_start in week_set:
            submitted_map.setdefault(r.user_id, set()).add(r.period_start)

    overdue_rows = list(
        (
            await db.execute(
                select(Task.assignee_id, func.count(Task.id))
                .where(
                    Task.assignee_id.in_(user_ids),
                    Task.status != "completed",
                    Task.due_date.is_not(None),
                    Task.due_date < today,
                )
                .group_by(Task.assignee_id)
            )
        ).all()
    )
    overdue_map = {int(uid): int(cnt) for uid, cnt in overdue_rows if uid}

    blocker_rows = list(
        (
            await db.execute(
                select(Blocker.assignee_id, func.count(Blocker.id))
                .where(
                    Blocker.assignee_id.in_(user_ids),
                    Blocker.status != "resolved",
                )
                .group_by(Blocker.assignee_id)
            )
        ).all()
    )
    blocker_map = {int(uid): int(cnt) for uid, cnt in blocker_rows if uid}

    people: list[dict[str, Any]] = []
    for u in roster:
        submitted = len(submitted_map.get(u.id) or [])
        rate = round(submitted / 4, 4)
        people.append(
            {
                "user_id": u.id,
                "name": u.name or u.username,
                "username": u.username,
                "report_submitted_weeks": submitted,
                "report_expected_weeks": 4,
                "report_submit_rate": rate,
                "overdue_tasks": overdue_map.get(u.id, 0),
                "open_blockers": blocker_map.get(u.id, 0),
            }
        )
    people.sort(
        key=lambda x: (
            x["overdue_tasks"] + x["open_blockers"],
            -x["report_submit_rate"],
            x["name"],
        ),
        reverse=True,
    )
    return {"weeks": [w.isoformat() for w in weeks], "people": people}


async def projects_stats(db: AsyncSession, user: User, permissions: set[str]) -> dict[str, Any]:
    """项目延期倾向、卡点分布、第三方/客户拖期排行。"""
    projects = await list_visible_project_rows(db, user, permissions)
    if not projects:
        return {
            "party_distribution": {
                "third_party": 0,
                "customer": 0,
                "internal": 0,
                "approval": 0,
                "other": 0,
            },
            "external_rank": [],
            "projects": [],
        }

    project_ids = [p.id for p in projects]
    today = date.today()
    blockers = list(
        (
            await db.execute(
                select(Blocker)
                .options(selectinload(Blocker.project))
                .where(
                    Blocker.project_id.in_(project_ids),
                    Blocker.status != "resolved",
                )
            )
        )
        .scalars()
        .all()
    )
    party_dist = {
        "third_party": 0,
        "customer": 0,
        "internal": 0,
        "approval": 0,
        "other": 0,
    }
    by_project: dict[int, list[Blocker]] = {pid: [] for pid in project_ids}
    external_names: dict[str, int] = {}
    for b in blockers:
        pt = (b.party_type or "internal").strip() or "internal"
        if pt not in party_dist:
            party_dist["other"] += 1
        else:
            party_dist[pt] += 1
        by_project.setdefault(b.project_id, []).append(b)
        if pt in {"third_party", "customer"}:
            name = (b.responsible_party or "").strip() or (
                "第三方" if pt == "third_party" else "客户"
            )
            external_names[name] = external_names.get(name, 0) + 1

    overdue_map: dict[int, int] = {}
    overdue_rows = list(
        (
            await db.execute(
                select(Task.project_id, func.count(Task.id))
                .where(
                    Task.project_id.in_(project_ids),
                    Task.status != "completed",
                    Task.due_date.is_not(None),
                    Task.due_date < today,
                )
                .group_by(Task.project_id)
            )
        ).all()
    )
    for pid, cnt in overdue_rows:
        overdue_map[int(pid)] = int(cnt)

    project_rows: list[dict[str, Any]] = []
    for p in projects:
        blist = by_project.get(p.id) or []
        overdue_n = overdue_map.get(p.id, 0)
        at_risk = (p.status in {"at_risk", "blocked"}) or overdue_n > 0 or len(blist) >= 2
        delay_tendency = "high" if (p.status == "blocked" or overdue_n >= 3) else (
            "medium" if at_risk else "low"
        )
        party_counts = {"third_party": 0, "customer": 0, "internal": 0, "approval": 0, "other": 0}
        for b in blist:
            pt = (b.party_type or "internal").strip() or "internal"
            if pt not in party_counts:
                party_counts["other"] += 1
            else:
                party_counts[pt] += 1
        project_rows.append(
            {
                "id": p.id,
                "name": p.name,
                "level": p.level,
                "status": p.status,
                "open_blockers": len(blist),
                "overdue_tasks": overdue_n,
                "delay_tendency": delay_tendency,
                "party_counts": party_counts,
                "is_key": p.level == "key",
            }
        )
    project_rows.sort(
        key=lambda x: (
            0 if x["is_key"] else 1,
            {"high": 0, "medium": 1, "low": 2}.get(x["delay_tendency"], 9),
            -x["open_blockers"],
        )
    )
    external_rank = [
        {"name": name, "open_blockers": cnt}
        for name, cnt in sorted(external_names.items(), key=lambda x: -x[1])[:15]
    ]
    return {
        "party_distribution": party_dist,
        "external_rank": external_rank,
        "projects": project_rows,
    }
