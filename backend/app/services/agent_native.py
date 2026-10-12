"""一人一助手：待办隔离、习惯排序、催办去重、改派草稿。"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    AgentDraft,
    AgentNudgeLog,
    AgentUserPref,
    Blocker,
    ProgressReport,
    ProgressReportItem,
    Project,
    ProjectMember,
    Solution,
    SolutionTag,
    Task,
    User,
)
from app.rbac.deps import get_user_permissions
from app.services import task_service as task_svc
from app.services.project_roles import default_roles_for_creator
from app.services.project_service import (
    ensure_daily_project,
    list_projects,
    phases_for,
    seed_standard_phases,
)
from app.services.report_quality import monday_of, sunday_of

LEGACY_AI_MSG = "请到「我的助手」里说，确认后再写入账本。工作台不再单独调模型。"


def refuse_legacy_ai() -> None:
    """142 一人一助手：禁止工作台自己再调大模型。"""
    if settings.agent_native:
        raise HTTPException(status_code=410, detail=LEGACY_AI_MSG)

_PRIORITY_RANK = {
    "p0": 0,
    "critical": 0,
    "p1": 1,
    "p2": 2,
    "p3": 3,
    "p4": 4,
    "watch": 2,
    "normal": 3,
}


def in_quiet_hours(now: time, quiet_start: time | None, quiet_end: time | None) -> bool:
    if quiet_start is None or quiet_end is None:
        return False
    if quiet_start <= quiet_end:
        return quiet_start <= now <= quiet_end
    return now >= quiet_start or now <= quiet_end


def parse_time(raw: str | None) -> time | None:
    text = (raw or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        return time(hour, minute, second)
    except (TypeError, ValueError):
        return None


def _task_brief(item) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "due_date": data.get("due_date"),
        "priority": data.get("priority"),
        "status": data.get("status"),
        "acceptance_status": data.get("acceptance_status"),
        "assignment_type": data.get("assignment_type"),
        "project_id": data.get("project_id"),
        "project_name": data.get("project_name"),
        "assignee_id": (data.get("assignee") or {}).get("id"),
    }


def rank_briefs(items: list[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()

    def sort_key(row: dict[str, Any]) -> tuple:
        due = row.get("due_date")
        due_d: date | None
        if isinstance(due, date):
            due_d = due
        elif isinstance(due, str) and due:
            due_d = date.fromisoformat(due[:10])
        else:
            due_d = None
        overdue = 0 if (due_d is not None and due_d < today) else 1
        pending = 0 if (row.get("acceptance_status") or "") == "pending" else 1
        pr = _PRIORITY_RANK.get(str(row.get("priority") or "p3"), 9)
        due_ord = due_d.toordinal() if due_d else 10**9
        return (overdue, pending, pr, due_ord, int(row.get("id") or 0))

    return sorted(items, key=sort_key)


async def my_todo_briefs(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    tasks = await task_svc.list_my_personal_tasks(db, user)
    return [_task_brief(t) for t in tasks]


async def ranked_todos(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    return rank_briefs(await my_todo_briefs(db, user))


async def my_week_report(db: AsyncSession, user: User) -> dict[str, Any]:
    start = monday_of(date.today())
    end = sunday_of(date.today())
    result = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items).selectinload(ProgressReportItem.project))
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == "week",
            ProgressReport.period_start == start,
        )
    )
    report = result.scalar_one_or_none()
    if not report:
        return {
            "exists": False,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "items": [],
        }
    items = []
    for card in report.items:
        items.append(
            {
                "id": card.id,
                "task_title": card.task_title,
                "result_text": card.result_text,
                "next_goal": card.next_goal,
            }
        )
    return {
        "exists": True,
        "id": report.id,
        "status": report.status,
        "period_start": start.isoformat(),
        "period_end": (report.period_end or end).isoformat(),
        "items": items,
    }


async def upsert_prefs(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentUserPref:
    row = await db.get(AgentUserPref, user.id)
    if not row:
        row = AgentUserPref(user_id=user.id)
        db.add(row)
    if "quiet_start" in payload:
        row.quiet_start = parse_time(str(payload.get("quiet_start") or "")) 
    if "quiet_end" in payload:
        row.quiet_end = parse_time(str(payload.get("quiet_end") or ""))
    if payload.get("digest_mode"):
        row.digest_mode = str(payload["digest_mode"])[:16]
    if "channel_inbox" in payload:
        row.channel_inbox = bool(payload["channel_inbox"])
    if "channel_dingtalk" in payload:
        row.channel_dingtalk = bool(payload["channel_dingtalk"])
    if "channel_dsh" in payload:
        row.channel_dsh = bool(payload["channel_dsh"])
    await db.commit()
    await db.refresh(row)
    return row


def prefs_out(row: AgentUserPref | None, user_id: int) -> dict[str, Any]:
    if not row:
        return {
            "user_id": user_id,
            "quiet_start": None,
            "quiet_end": None,
            "digest_mode": "instant",
            "channel_inbox": True,
            "channel_dingtalk": True,
            "channel_dsh": True,
        }
    return {
        "user_id": row.user_id,
        "quiet_start": row.quiet_start.isoformat() if row.quiet_start else None,
        "quiet_end": row.quiet_end.isoformat() if row.quiet_end else None,
        "digest_mode": row.digest_mode,
        "channel_inbox": row.channel_inbox,
        "channel_dingtalk": row.channel_dingtalk,
        "channel_dsh": row.channel_dsh,
    }


async def nudge(
    db: AsyncSession,
    user: User,
    *,
    event: str,
    task_id: int,
    now_time: time | None = None,
) -> dict[str, Any]:
    event_key = f"{event}:{task_id}:{user.id}"
    existing = await db.execute(select(AgentNudgeLog).where(AgentNudgeLog.event_key == event_key))
    if existing.scalar_one_or_none():
        return {"sent": False, "reason": "already_nudged", "event_key": event_key}

    prefs = await db.get(AgentUserPref, user.id)
    clock = now_time or datetime.now().time().replace(microsecond=0)
    quiet_start = prefs.quiet_start if prefs else None
    quiet_end = prefs.quiet_end if prefs else None
    if in_quiet_hours(clock, quiet_start, quiet_end):
        db.add(AgentNudgeLog(user_id=user.id, event_key=event_key))
        await db.commit()
        return {"sent": False, "reason": "quiet_hours", "event_key": event_key}

    db.add(AgentNudgeLog(user_id=user.id, event_key=event_key))
    await db.commit()
    return {"sent": True, "reason": "ok", "event_key": event_key}


async def draft_reassign(
    db: AsyncSession,
    user: User,
    *,
    task_id: int,
    new_assignee_id: int,
    reason: str,
) -> AgentDraft:
    perms = get_user_permissions(user)
    if "task.assign" not in perms:
        raise HTTPException(status_code=403, detail="无权改派")
    task = await task_svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await task_svc.can_manage_owner(db, user, task):
        raise HTTPException(status_code=403, detail="无权更换负责人")
    draft = AgentDraft(
        user_id=user.id,
        kind="reassign",
        status="pending",
        payload={
            "task_id": task_id,
            "new_assignee_id": new_assignee_id,
            "reason": reason,
        },
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def confirm_draft(db: AsyncSession, user: User, draft_id: int) -> dict[str, Any]:
    draft = await db.get(AgentDraft, draft_id)
    if not draft or draft.user_id != user.id:
        raise HTTPException(status_code=404, detail="草稿不存在")
    if draft.status != "pending":
        raise HTTPException(status_code=400, detail="草稿已处理")
    kind = draft.kind or "reassign"
    if kind == "reassign":
        result = await _confirm_reassign(db, user, draft)
    elif kind == "week_report":
        result = await _confirm_week_report(db, user, draft)
    elif kind == "task":
        result = await _confirm_task(db, user, draft)
    elif kind == "todo":
        result = await _confirm_todo(db, user, draft)
    elif kind == "project":
        result = await _confirm_project(db, user, draft)
    elif kind == "case":
        result = await _confirm_case(db, user, draft)
    elif kind == "blocker":
        result = await _confirm_blocker(db, user, draft)
    else:
        raise HTTPException(status_code=400, detail="不支持的草稿")
    draft.status = "confirmed"
    draft.confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "kind": kind, **result}


async def list_visible_projects(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    perms = get_user_permissions(user)
    projects = await list_projects(db, user, perms)
    return [{"id": p.id, "name": p.name, "level": p.level, "status": p.status} for p in projects]


async def search_cases(db: AsyncSession, text: str) -> dict[str, Any]:
    """案例检索只查账本关键词，不调用工作台大模型。智能在 DSH 对话里完成。"""
    raw = (text or "").strip()
    keywords = [w for w in re.split(r"[\s，,。？?、]+", raw) if len(w) >= 2][:6]
    fallback = {
        "keywords": keywords,
        "source": "ledger",
        "items": [],
    }
    clauses = []
    for word in keywords[:5]:
        like = f"%{word}%"
        clauses.append(Solution.title.ilike(like))
        clauses.append(Solution.summary.ilike(like))
        clauses.append(Solution.original_filename.ilike(like))
    if not clauses:
        return fallback
    rows = (
        await db.execute(
            select(Solution)
            .options(selectinload(Solution.tags))
            .where(Solution.status == "ready", or_(*clauses))
            .order_by(Solution.updated_at.desc())
            .limit(12)
        )
    ).scalars().unique().all()
    fallback["items"] = [
        {
            "id": row.id,
            "title": row.title or row.original_filename,
            "summary": (row.summary or "")[:400],
            "tags": [t.name for t in (row.tags or [])],
        }
        for row in rows
    ]
    return fallback


def _parse_iso_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


async def _save_pending_draft(
    db: AsyncSession, user: User, kind: str, payload: dict[str, Any]
) -> AgentDraft:
    draft = AgentDraft(user_id=user.id, kind=kind, status="pending", payload=payload)
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def draft_week_report(
    db: AsyncSession, user: User, payload: dict[str, Any]
) -> AgentDraft:
    items = payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="请至少起草一条周报事项")
    week_start = _parse_iso_date(payload.get("week_start") or payload.get("period_start")) or monday_of(
        date.today()
    )
    clean: list[dict[str, Any]] = []
    for raw in items[:20]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("task_title") or "").strip()[:256]
        if not title:
            continue
        clean.append(
            {
                "task_id": raw.get("task_id") or raw.get("linked_task_id"),
                "project_id": raw.get("project_id"),
                "task_title": title[:120],
                "result_text": str(raw.get("result_text") or "").strip()[:2000],
                "quant_value": str(raw.get("quant_value") or "").strip()[:64],
                "next_goal": str(raw.get("next_goal") or raw.get("next_week_plan") or "").strip()[:2000],
                "risk_level": str(raw.get("risk_level") or "normal")[:16],
                "risk_reason": str(raw.get("risk_reason") or "").strip()[:512],
                "no_action": bool(raw.get("no_action")),
            }
        )
    if not clean:
        raise HTTPException(status_code=400, detail="周报事项缺少标题")
    return await _save_pending_draft(
        db, user, "week_report", {"week_start": week_start.isoformat(), "items": clean}
    )


async def draft_task(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentDraft:
    title = str(payload.get("title") or "").strip()[:256]
    if not title:
        raise HTTPException(status_code=400, detail="任务标题不能为空")
    project_id = int(payload.get("project_id") or 0)
    if project_id < 1:
        raise HTTPException(status_code=400, detail="请指定项目")
    due = _parse_iso_date(payload.get("due_date"))
    if not due:
        raise HTTPException(status_code=400, detail="请指定截止日期")
    pri = str(payload.get("priority") or "p2").lower()
    if pri not in ("p0", "p1", "p2", "p3"):
        pri = "p2"
    return await _save_pending_draft(
        db,
        user,
        "task",
        {
            "project_id": project_id,
            "title": title,
            "goal": str(payload.get("goal") or payload.get("description") or "")[:4000],
            "priority": pri,
            "due_date": due.isoformat(),
            "assignment_type": "self",
        },
    )


def _normalize_todo_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """把单条或 items 列表规整为待办草稿条目。"""
    raw_items = payload.get("items")
    if raw_items is None:
        raw_items = [
            {
                "title": payload.get("title"),
                "due_date": payload.get("due_date"),
                "goal": payload.get("goal") or payload.get("description"),
                "priority": payload.get("priority"),
                "project_id": payload.get("project_id"),
            }
        ]
    if not isinstance(raw_items, list) or not raw_items:
        raise HTTPException(status_code=400, detail="请提供待办标题")
    if len(raw_items) > 30:
        raise HTTPException(status_code=400, detail="一次最多起草 30 条待办")
    out: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:256]
        if not title:
            continue
        due = _parse_iso_date(raw.get("due_date")) or date.today()
        pri = str(raw.get("priority") or "p2").lower()
        if pri not in ("p0", "p1", "p2", "p3"):
            pri = "p2"
        project_raw = raw.get("project_id")
        project_id = int(project_raw) if project_raw not in (None, "") else None
        out.append(
            {
                "title": title,
                "goal": str(raw.get("goal") or raw.get("description") or "")[:4000],
                "priority": pri,
                "due_date": due.isoformat(),
                "project_id": project_id,
            }
        )
    if not out:
        raise HTTPException(status_code=400, detail="请提供待办标题")
    return out


async def draft_todo(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentDraft:
    """起草本人待办（确认后进 my-todos）。默认挂「日常任务」，可指定 project_id。"""
    items = _normalize_todo_items(payload)
    # 确认前确保日常任务存在，避免确认时才失败
    await ensure_daily_project(db)
    return await _save_pending_draft(
        db,
        user,
        "todo",
        {"items": items, "assignment_type": "self"},
    )


async def draft_project(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentDraft:
    name = str(payload.get("name") or "").strip()[:256]
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    start = _parse_iso_date(payload.get("start_date")) or date.today()
    end = _parse_iso_date(payload.get("end_date") or payload.get("planned_end_date"))
    if not end:
        raise HTTPException(status_code=400, detail="请指定计划结束日")
    ptype = str(payload.get("project_type") or "software")
    if ptype not in ("software", "integration", "fusion"):
        ptype = "software"
    level = str(payload.get("level") or "normal")
    if level not in ("key", "normal", "daily"):
        level = "normal"
    supports = payload.get("support_user_ids") or []
    if not isinstance(supports, list):
        supports = []
    lead_raw = payload.get("lead_user_id")
    return await _save_pending_draft(
        db,
        user,
        "project",
        {
            "name": name,
            "project_type": ptype,
            "level": level,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "description": str(payload.get("description") or "")[:4000],
            "lead_user_id": int(lead_raw) if lead_raw not in (None, "") else None,
            "support_user_ids": [
                int(x) for x in supports if isinstance(x, int) or str(x).isdigit()
            ],
        },
    )


async def draft_case(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentDraft:
    title = str(payload.get("title") or "").strip()[:256]
    summary = str(payload.get("summary") or "").strip()[:4000]
    if not title or not summary:
        raise HTTPException(status_code=400, detail="案例需要标题和摘要")
    tags = payload.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    return await _save_pending_draft(
        db,
        user,
        "case",
        {
            "title": title,
            "summary": summary,
            "tags": [str(t).strip()[:64] for t in tags if str(t).strip()][:8],
        },
    )


async def draft_blocker(db: AsyncSession, user: User, payload: dict[str, Any]) -> AgentDraft:
    project_id = int(payload.get("project_id") or 0)
    description = str(payload.get("description") or "").strip()[:512]
    if project_id < 1 or not description:
        raise HTTPException(status_code=400, detail="卡点需要项目和描述")
    found = _parse_iso_date(payload.get("found_date")) or date.today()
    follow = _parse_iso_date(payload.get("next_follow_up")) or found
    return await _save_pending_draft(
        db,
        user,
        "blocker",
        {
            "project_id": project_id,
            "description": description,
            "responsible_party": str(payload.get("responsible_party") or "待确认")[:128],
            "party_type": str(payload.get("party_type") or "internal")[:32],
            "found_date": found.isoformat(),
            "actions_taken": str(payload.get("actions_taken") or "已记录，待跟进")[:2000],
            "next_action": str(payload.get("next_action") or "跟进责任方")[:512],
            "next_follow_up": follow.isoformat(),
            "impact": str(payload.get("impact") or "")[:256],
        },
    )


async def _confirm_reassign(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    task_id = int(draft.payload["task_id"])
    new_assignee_id = int(draft.payload["new_assignee_id"])
    task = await task_svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await task_svc.can_manage_owner(db, user, task):
        raise HTTPException(status_code=403, detail="无权更换负责人")
    await task_svc.ensure_users_are_members(
        db, task.project_id, [new_assignee_id], label="负责人"
    )
    old_id = task.assignee_id
    task.assignee_id = new_assignee_id
    if (task.assignment_type or "self") == "delegated":
        task.acceptance_status = "pending"
        task.accepted_at = None
    task_svc.add_event(
        db,
        task,
        user.id,
        "owner_changed",
        {"old_assignee_id": old_id, "new_assignee_id": new_assignee_id, "via": "agent_draft"},
    )
    return {"task_id": task_id, "assignee_id": new_assignee_id}


async def _confirm_week_report(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    week_start = _parse_iso_date(
        draft.payload.get("week_start") or draft.payload.get("period_start")
    ) or monday_of(date.today())
    week_end = sunday_of(week_start)
    report = (
        await db.execute(
            select(ProgressReport)
            .options(selectinload(ProgressReport.items))
            .where(
                ProgressReport.user_id == user.id,
                ProgressReport.period_type == "week",
                ProgressReport.period_start == week_start,
            )
        )
    ).scalar_one_or_none()
    if not report:
        report = ProgressReport(
            user_id=user.id,
            period_type="week",
            period_start=week_start,
            period_end=week_end,
            status="draft",
        )
        db.add(report)
        await db.flush()
    elif report.status == "submitted":
        raise HTTPException(status_code=400, detail="本周周报已提交，不能再改")
    added = 0
    for raw in draft.payload.get("items") or []:
        title = str(raw.get("task_title") or "").strip()
        if not title:
            continue
        task_id = raw.get("task_id") or raw.get("linked_task_id")
        project_id = raw.get("project_id")
        item = ProgressReportItem(
            report_id=report.id,
            project_id=int(project_id) if project_id else None,
            chain_id=str(uuid.uuid4()),
            linked_task_id=int(task_id) if task_id else None,
            task_title=title[:120],
            result_text=str(raw.get("result_text") or ""),
            quant_value=str(raw.get("quant_value") or ""),
            next_goal=str(raw.get("next_goal") or raw.get("next_week_plan") or ""),
            risk_level=str(raw.get("risk_level") or "normal")[:16],
            risk_reason=str(raw.get("risk_reason") or ""),
            no_action=bool(raw.get("no_action")),
        )
        db.add(item)
        added += 1
    return {"report_id": report.id, "added_items": added}


async def _confirm_task(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    payload = draft.payload
    project_id = int(payload["project_id"])
    await task_svc.ensure_users_are_members(db, project_id, [user.id], label="负责人")
    due = _parse_iso_date(payload.get("due_date")) or date.today()
    task = Task(
        project_id=project_id,
        title=str(payload["title"])[:256],
        goal=str(payload.get("goal") or payload.get("description") or ""),
        assignee_id=user.id,
        created_by_id=user.id,
        assignment_type="self",
        acceptance_status="accepted",
        accepted_at=datetime.now(timezone.utc),
        due_date=due,
        priority=str(payload.get("priority") or "p2"),
        status="not_started",
        source="agent",
    )
    db.add(task)
    await db.flush()
    task_svc.add_event(db, task, user.id, "created", {"via": "agent_draft"})
    return {"task_id": task.id}


async def _confirm_todo(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    """确认待办：每人一条自建任务，默认「日常任务」项目。"""
    daily = await ensure_daily_project(db)
    items = draft.payload.get("items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="待办草稿为空")
    task_ids: list[int] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()[:256]
        if not title:
            continue
        project_id = int(raw.get("project_id") or daily.id)
        await task_svc.ensure_users_are_members(db, project_id, [user.id], label="负责人")
        due = _parse_iso_date(raw.get("due_date")) or date.today()
        pri = str(raw.get("priority") or "p2")
        if pri not in ("p0", "p1", "p2", "p3"):
            pri = "p2"
        task = Task(
            project_id=project_id,
            title=title,
            goal=str(raw.get("goal") or "")[:4000],
            assignee_id=user.id,
            created_by_id=user.id,
            assignment_type="self",
            acceptance_status="accepted",
            accepted_at=datetime.now(timezone.utc),
            due_date=due,
            priority=pri,
            status="not_started",
            source="agent",
        )
        db.add(task)
        await db.flush()
        task_svc.add_event(db, task, user.id, "created", {"via": "agent_todo_draft"})
        task_ids.append(task.id)
    if not task_ids:
        raise HTTPException(status_code=400, detail="待办草稿为空")
    return {
        "task_ids": task_ids,
        "count": len(task_ids),
        "project_id": daily.id,
        "project_name": daily.name,
    }


async def _confirm_project(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    payload = draft.payload
    name = str(payload["name"]).strip()
    if name == "日常任务":
        raise HTTPException(status_code=400, detail="「日常任务」为系统内置名称，请换用真实立项名")
    start = _parse_iso_date(payload.get("start_date")) or date.today()
    end = _parse_iso_date(payload.get("end_date") or payload.get("planned_end_date")) or start
    ptype = str(payload.get("project_type") or "software")
    if ptype not in ("software", "integration", "fusion"):
        ptype = "software"
    lead_id = payload.get("lead_user_id")
    support_ids = list(payload.get("support_user_ids") or [])
    default_lead, default_supports = default_roles_for_creator(user)
    if lead_id is None:
        lead_id = default_lead
    support_ids = list(dict.fromkeys([*default_supports, *support_ids]))
    if lead_id in support_ids:
        support_ids = [sid for sid in support_ids if sid != lead_id]
    if not support_ids:
        raise HTTPException(status_code=400, detail="请指定至少一名项目支撑")
    phase_names = phases_for(ptype)
    project = Project(
        name=name[:128],
        project_type=ptype,
        level=str(payload.get("level") or "normal"),
        owner_id=user.id,
        start_date=start,
        end_date=end,
        description=str(payload.get("description") or ""),
        status="normal",
        current_phase=phase_names[0] if phase_names else None,
    )
    db.add(project)
    await db.flush()
    if lead_id:
        db.add(ProjectMember(project_id=project.id, user_id=int(lead_id), member_role="lead"))
    for sid in support_ids:
        db.add(ProjectMember(project_id=project.id, user_id=int(sid), member_role="support"))
    if user.id not in {int(lead_id) if lead_id else 0, *{int(s) for s in support_ids}}:
        db.add(ProjectMember(project_id=project.id, user_id=user.id, member_role="member"))
    await seed_standard_phases(db, project.id, ptype)
    return {"project_id": project.id}


async def _confirm_case(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    payload = draft.payload
    sol = Solution(
        title=str(payload["title"])[:256],
        original_filename=str(payload["title"])[:256],
        stored_path="",
        file_size=0,
        mime_type="text/plain",
        uploader_id=user.id,
        status="ready",
        summary=str(payload.get("summary") or ""),
    )
    db.add(sol)
    await db.flush()
    for name in payload.get("tags") or []:
        tag = str(name).strip()
        if tag:
            db.add(SolutionTag(solution_id=sol.id, name=tag[:64], source="manual"))
    return {"solution_id": sol.id}


async def _confirm_blocker(
    db: AsyncSession, user: User, draft: AgentDraft
) -> dict[str, Any]:
    payload = draft.payload
    project_id = int(payload["project_id"])
    await task_svc.ensure_users_are_members(db, project_id, [user.id], label="跟进人")
    found = _parse_iso_date(payload.get("found_date")) or date.today()
    follow = _parse_iso_date(payload.get("next_follow_up")) or found
    blocker = Blocker(
        project_id=project_id,
        assignee_id=user.id,
        description=str(payload["description"])[:512],
        responsible_party=str(payload.get("responsible_party") or "待确认")[:128],
        party_type=str(payload.get("party_type") or "internal")[:32],
        found_date=found,
        actions_taken=str(payload.get("actions_taken") or ""),
        next_action=str(payload.get("next_action") or "")[:512],
        next_follow_up=follow,
        impact=str(payload.get("impact") or "")[:256] or None,
        status="following",
    )
    db.add(blocker)
    await db.flush()
    return {"blocker_id": blocker.id}
