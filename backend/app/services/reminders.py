"""定时/手动提醒：卡点跟进逾期、阶段逾期、周报未交。"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Blocker, Phase, ProgressReport, Project, ProjectMember, Role, User, UserRole
from app.services.notify_pipeline import dispatch_notification
from app.services.report_quality import monday_of, sunday_of
from app.services.report_roster import list_report_roster_users


async def _users_with_role(db: AsyncSession, role_code: str) -> list[User]:
    result = await db.execute(
        select(User)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == role_code, User.is_active.is_(True))
    )
    return list(result.scalars().unique().all())


async def _project_lead(db: AsyncSession, project_id: int) -> User | None:
    lead = await db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.member_role == "lead",
        )
    )
    u = lead.scalar_one_or_none()
    if u:
        return u
    proj = await db.get(Project, project_id)
    if proj and proj.owner_id:
        return await db.get(User, proj.owner_id)
    return None


def _unique_users(users: list[User | None]) -> list[User]:
    seen: set[int] = set()
    out: list[User] = []
    for u in users:
        if u and u.id not in seen:
            seen.add(u.id)
            out.append(u)
    return out


async def scan_overdue_blockers(db: AsyncSession) -> list[dict]:
    today = date.today()
    result = await db.execute(
        select(Blocker)
        .options(selectinload(Blocker.assignee))
        .where(Blocker.status != "resolved", Blocker.next_follow_up < today)
        .order_by(Blocker.next_follow_up)
    )
    items = []
    for b in result.scalars().all():
        items.append(
            {
                "blocker_id": b.id,
                "project_id": b.project_id,
                "description": b.description,
                "next_follow_up": str(b.next_follow_up),
                "assignee_id": b.assignee_id,
                "assignee": b.assignee,
            }
        )
    return items


async def send_blocker_overdue_notices(db: AsyncSession) -> dict:
    items = await scan_overdue_blockers(db)
    dept_heads = await _users_with_role(db, "dept_head")
    sent = 0
    skipped = 0
    for it in items:
        recipients = _unique_users([it.get("assignee"), *dept_heads])
        if not recipients:
            skipped += 1
            continue
        result = await dispatch_notification(
            db,
            event_key="blocker_follow_up_overdue",
            users=recipients,
            variables={
                "blocker_desc": it["description"],
                "next_follow_up": it["next_follow_up"],
            },
            payload={
                "type": "blocker_follow_up_overdue",
                "blocker_id": it["blocker_id"],
                "project_id": it["project_id"],
            },
            ref_type="blocker",
            ref_id=it["blocker_id"],
            fallback_title="卡点跟进逾期提醒",
            fallback_body=(
                f"【提醒】卡点「{it['description']}」下次跟进时间已到"
                f"（{it['next_follow_up']}），请及时更新状态。"
            ),
            respect_quiet=True,
        )
        if result.get("dingtalk_notified"):
            sent += 1
        else:
            # 可能仅站内成功；仍计为已尝试
            sent += 1
    await db.commit()
    return {
        "rule": "blocker_follow_up_overdue",
        "scanned": len(items),
        "sent": sent,
        "skipped_no_recipient": skipped,
        "errors": [],
    }


async def scan_overdue_phases(db: AsyncSession) -> list[Phase]:
    today = date.today()
    result = await db.execute(
        select(Phase)
        .options(selectinload(Phase.project))
        .where(
            Phase.planned_end_date.is_not(None),
            Phase.planned_end_date < today,
            Phase.status != "completed",
        )
        .order_by(Phase.planned_end_date)
    )
    return list(result.scalars().all())


async def send_phase_overdue_notices(db: AsyncSession) -> dict:
    phases = await scan_overdue_phases(db)
    dept_heads = await _users_with_role(db, "dept_head")
    sent = 0
    for ph in phases:
        lead = await _project_lead(db, ph.project_id)
        recipients = _unique_users([lead, *dept_heads])
        if not recipients:
            continue
        project_name = ph.project.name if ph.project else ""
        planned = str(ph.planned_end_date) if ph.planned_end_date else ""
        await dispatch_notification(
            db,
            event_key="phase_overdue",
            users=recipients,
            variables={
                "project_name": project_name,
                "phase_name": ph.name,
                "planned_end": planned,
            },
            payload={
                "type": "phase_overdue",
                "phase_id": ph.id,
                "project_id": ph.project_id,
            },
            ref_type="phase",
            ref_id=ph.id,
            fallback_title="阶段逾期提醒",
            fallback_body=(
                f"【提醒】项目「{project_name}」的阶段「{ph.name}」"
                f"已过计划完成日 {planned}，请尽快推进。"
            ),
            respect_quiet=True,
        )
        sent += 1
    await db.commit()
    return {"rule": "phase_overdue", "scanned": len(phases), "sent": sent}


async def scan_weekly_report_overdue(db: AsyncSession) -> list[User]:
    """上周（上周一~周日）未提交 week 周报的名单成员。"""
    today = date.today()
    this_monday = monday_of(today)
    last_monday = this_monday - timedelta(days=7)
    last_sunday = sunday_of(last_monday)
    roster = await list_report_roster_users(db)
    if not roster:
        return []
    result = await db.execute(
        select(ProgressReport.user_id).where(
            ProgressReport.period_type == "week",
            ProgressReport.period_start == last_monday,
            ProgressReport.status == "submitted",
        )
    )
    submitted_ids = {row[0] for row in result.all()}
    missing = [u for u in roster if u.id not in submitted_ids]
    # 挂上周期信息供调用方使用
    for u in missing:
        setattr(u, "_overdue_period_start", last_monday)
        setattr(u, "_overdue_period_end", last_sunday)
    return missing


async def send_weekly_report_overdue_notices(db: AsyncSession) -> dict:
    missing = await scan_weekly_report_overdue(db)
    sent = 0
    for u in missing:
        period_start = getattr(u, "_overdue_period_start", None)
        period_end = getattr(u, "_overdue_period_end", None)
        await dispatch_notification(
            db,
            event_key="weekly_report_overdue",
            users=[u],
            variables={
                "period_start": str(period_start or ""),
                "period_end": str(period_end or ""),
            },
            payload={"type": "weekly_report_overdue", "user_id": u.id},
            ref_type="weekly_report",
            ref_id=f"{u.id}:{period_start}",
            fallback_title="周报逾期提醒",
            fallback_body=(
                f"【提醒】上周周报（{period_start} ~ {period_end}）还未提交，"
                "请于今天 18:00 前完成。"
            ),
            respect_quiet=True,
        )
        sent += 1
    await db.commit()
    return {"rule": "weekly_report_overdue", "scanned": sent, "sent": sent}


async def run_scheduled_event(db: AsyncSession, event_key: str) -> dict:
    if event_key == "blocker_follow_up_overdue":
        return await send_blocker_overdue_notices(db)
    if event_key == "phase_overdue":
        return await send_phase_overdue_notices(db)
    if event_key == "weekly_report_overdue":
        return await send_weekly_report_overdue_notices(db)
    return {"rule": event_key, "skipped": True, "reason": "unknown_event"}
