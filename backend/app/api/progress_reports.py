"""轻量周报/日报 API：三问填写、校验、预填、AI 辅导、管理者看板。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import async_session, get_db
from app.models import (
    Blocker,
    ProgressReport,
    ProgressReportAttachment,
    ProgressReportItem,
    ProgressReportReview,
    Project,
    ProjectMember,
    Task,
    TaskEvent,
    User,
)
from app.rbac.deps import get_user_permissions, require_password_updated, require_permission
from app.schemas import (
    AiCoachOut,
    AiCoachRequest,
    AiStructureOut,
    AiStructureRequest,
    ChainTimelineNode,
    ChainTimelineOut,
    PersonCompletionStats,
    PersonHistoryPeriod,
    PersonTimelineChain,
    PersonTimelinesOut,
    PersonWorkbenchOut,
    Phase1ReviewOut,
    ProgressItemIn,
    ProgressItemOut,
    ProgressPrefillOut,
    ProgressReportAttachmentOut,
    ProgressReportOut,
    ProgressReportSave,
    ProgressValidateOut,
    ProjectRelatedItemOut,
    ProjectRelatedPeriodOut,
    ProjectRelatedReportOut,
    ProjectRelatedReportsOut,
    ReportBoardOut,
    ReportBoardPeriod,
    ReportBoardPerson,
    ReportBoardProject,
    MyReportPeriod,
    SubmissionMatrixCell,
    SubmissionMatrixOut,
    SubmissionMatrixPeriod,
    TaskEventBrief,
    UserBrief,
)
from app.services.report_ai_coach import ai_coach
from app.services.agent_native import refuse_legacy_ai
from app.services.report_ai_review import ensure_phase1_review
from app.services.report_ai_structure import ai_structure
from app.services.report_attachment_storage import (
    absolute_path as report_attachment_absolute_path,
    delete_stored as report_attachment_delete_stored,
    save_upload as report_attachment_save_upload,
    upload_root as report_attachment_upload_root,
)
from app.services.report_next_plan import sync_done_result_todos, sync_next_plan_todos
from app.services.report_quality import (
    compose_theme_from_titles,
    monday_of,
    sunday_of,
    validate_report_items,
)
from app.services.project_match import guess_project_id, looks_like_daily_work
from app.services.project_related_reports import match_item_to_project
from app.services.project_service import (
    build_project_context,
    ensure_daily_project,
    guess_text_in_bundle,
)
from app.services.report_roster import REPORT_ROSTER_USERNAMES, list_report_roster_users
from app.services.work_tags import WORK_TAG_OPTIONS, normalize_work_tags
from app.services.notify_pipeline import dispatch_notification

router = APIRouter(prefix="/progress-reports", tags=["周报日报"])


@router.get("/work-tags", response_model=list[str])
async def list_work_tags(
    _user: User = Depends(require_password_updated),
) -> list[str]:
    """周报任务卡可选工作标签（固定词表）。"""
    return list(WORK_TAG_OPTIONS)


def _user_brief(u: User) -> UserBrief:
    return UserBrief(id=u.id, name=u.name, username=u.username, avatar_url=u.avatar_url)


def _week_starts(anchor: date, weeks: int) -> list[date]:
    """从 anchor 所在周往前共 weeks 个周一（含当前周）。"""
    end_monday = monday_of(anchor)
    return [end_monday - timedelta(days=7 * i) for i in range(weeks - 1, -1, -1)]


def _period_floor(today: date | None = None) -> date:
    """看板可选周期起点：当年 9 月 1 日；未到 9 月则从上一年 9 月 1 日。"""
    d = today or date.today()
    if d.month >= 9:
        return date(d.year, 9, 1)
    return date(d.year - 1, 9, 1)


def _week_starts_from_floor(floor: date, until_monday: date) -> list[date]:
    """从 floor 所在周（周一）到 until_monday 的全部周一（含两端）。"""
    start_m = monday_of(floor)
    weeks: list[date] = []
    cur = start_m
    while cur <= until_monday:
        weeks.append(cur)
        cur += timedelta(days=7)
    return weeks


async def _submit_stats_map(
    db: AsyncSession,
    *,
    user_ids: list[int],
    period_type: str,
    weeks: int,
    anchor: date | None = None,
) -> dict[int, tuple[int, int, float]]:
    """返回 user_id -> (submitted_weeks, expected_weeks, submit_rate)。"""
    if not user_ids or weeks <= 0:
        return {}
    starts = _week_starts(anchor or date.today(), weeks)
    expected = len(starts)
    result = await db.execute(
        select(ProgressReport.user_id, ProgressReport.period_start)
        .where(
            ProgressReport.period_type == period_type,
            ProgressReport.status == "submitted",
            ProgressReport.user_id.in_(user_ids),
            ProgressReport.period_start.in_(starts),
        )
    )
    submitted: dict[int, set[date]] = {uid: set() for uid in user_ids}
    for uid, ps in result.all():
        submitted.setdefault(uid, set()).add(ps)
    out: dict[int, tuple[int, int, float]] = {}
    for uid in user_ids:
        n = len(submitted.get(uid, set()))
        rate = round(n / expected, 4) if expected else 0.0
        out[uid] = (n, expected, rate)
    return out


def _phase1_out(row: ProgressReportReview | None) -> Phase1ReviewOut | None:
    if not row:
        return None
    return Phase1ReviewOut(
        id=row.id,
        report_id=row.report_id,
        phase=row.phase,
        summary=row.summary or "",
        strengths=list(row.strengths or []),
        risks=list(row.risks or []),
        completion_comment=row.completion_comment or "",
        source=row.source or "rule",
        model_name=row.model_name or "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_counts(report: ProgressReport) -> tuple[int, int, int, int, int]:
    """item_count, escalate, blocker, prior_yes, prior_no -> also prior_total derived."""
    escalate = 0
    blockers_n = 0
    prior_yes = 0
    prior_no = 0
    for it in report.items or []:
        if it.risk_level == "escalate":
            escalate += 1
        if it.stuck_status in ("blocker", "coordination"):
            blockers_n += 1
        if it.prior_done is True:
            prior_yes += 1
        elif it.prior_done is False:
            prior_no += 1
    return len(report.items or []), escalate, blockers_n, prior_yes, prior_no


def _resolve_period(
    period_type: str,
    period_start: date | None,
    period_end: date | None = None,
) -> tuple[date, date]:
    """返回 (start, end)。周报不再强制对齐周一；未传时默认本周一～本周日。"""
    ptype = period_type if period_type in ("week", "day") else "week"
    today = date.today()
    if ptype == "day":
        start = period_start or today
        return start, start
    start = period_start or monday_of(today)
    end = period_end or (sunday_of(start) if period_start is None else period_end)
    if end is None:
        end = sunday_of(start)
    if end < start:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
    return start, end


def _item_dict(it: ProgressItemIn | ProgressReportItem) -> dict:
    if isinstance(it, ProgressItemIn):
        data = it.model_dump()
        data["tags"] = normalize_work_tags(data.get("tags"))
        return data
    return {
        "project_id": it.project_id,
        "chain_id": it.chain_id,
        "linked_task_id": it.linked_task_id,
        "task_title": it.task_title,
        "no_action": it.no_action,
        "result_text": it.result_text,
        "quant_value": it.quant_value,
        "stuck_status": it.stuck_status,
        "stuck_detail": it.stuck_detail,
        "stuck_next_action": it.stuck_next_action,
        "stuck_follow_up": it.stuck_follow_up,
        "stuck_need_who": it.stuck_need_who,
        "linked_blocker_id": it.linked_blocker_id,
        "next_goal": it.next_goal,
        "next_due_date": it.next_due_date,
        "risk_level": it.risk_level,
        "risk_reason": it.risk_reason,
        "risk_impact_date": it.risk_impact_date,
        "risk_coordinator": it.risk_coordinator,
        "prior_done": it.prior_done,
        "tags": normalize_work_tags(getattr(it, "tags", None) or []),
        "sort_order": it.sort_order,
    }


def _item_content_key(it: ProgressItemIn) -> tuple:
    """判断两张卡片是不是同一件事（不含序号，避免复制粘贴后序号不同仍被当成两条）。"""
    tags = tuple(normalize_work_tags(getattr(it, "tags", None) or []))
    return (
        (it.task_title or "").strip(),
        bool(it.no_action),
        it.result_text or "",
        it.quant_value or "",
        it.stuck_status or "",
        it.stuck_detail or "",
        it.next_goal or "",
        it.next_due_date,
        it.project_id,
        it.risk_level or "",
        it.risk_reason or "",
        tags,
    )


def _dedupe_report_items(items: list[ProgressItemIn]) -> list[ProgressItemIn]:
    """同一份周报里内容完全相同的卡片只保留第一张。"""
    seen: set[tuple] = set()
    kept: list[ProgressItemIn] = []
    for it in items:
        key = _item_content_key(it)
        if key in seen:
            continue
        seen.add(key)
        kept.append(it)
    return kept


async def _role_maps(db: AsyncSession, project_ids: list[int]) -> tuple[dict, dict]:
    """project_id -> {user_id: role}; project_id -> lead name"""
    if not project_ids:
        return {}, {}
    result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.user))
        .where(ProjectMember.project_id.in_(project_ids))
    )
    roles: dict[int, dict[int, str]] = {}
    leads: dict[int, str] = {}
    for m in result.scalars().all():
        roles.setdefault(m.project_id, {})[m.user_id] = m.member_role
        if m.member_role == "lead":
            leads[m.project_id] = m.user.name
    return roles, leads


async def _report_out(
    db: AsyncSession,
    report: ProgressReport,
    soft: list[str] | None = None,
    *,
    next_plan_synced: int = 0,
    done_synced: int = 0,
    include_own_review: bool = False,
) -> ProgressReportOut:
    await db.refresh(report, attribute_names=["items", "author"])
    project_ids = [i.project_id for i in report.items if i.project_id]
    names: dict[int, str] = {}
    all_projects: list[dict] = []
    pr_all = await db.execute(select(Project.id, Project.name))
    for row in pr_all.all():
        names[row[0]] = row[1]
        all_projects.append({"project_id": row[0], "name": row[1]})
    roles, leads = await _role_maps(db, list({*project_ids, *(p["project_id"] for p in all_projects)}))
    attach_rows = (
        await db.execute(
            select(ProgressReportAttachment)
            .where(ProgressReportAttachment.report_id == report.id)
            .order_by(ProgressReportAttachment.id.desc())
        )
    ).scalars().all()
    uploader_cache: dict[int, str] = {}
    attachments_out: list[ProgressReportAttachmentOut] = []
    by_item: dict[int, list[ProgressReportAttachmentOut]] = {}
    for row in attach_rows:
        if row.uploader_id not in uploader_cache:
            uploader = await db.get(User, row.uploader_id)
            uploader_cache[row.uploader_id] = (
                (uploader.name or uploader.username or "") if uploader is not None else ""
            )
        att = ProgressReportAttachmentOut(
            id=row.id,
            report_id=row.report_id,
            item_id=row.item_id,
            original_filename=row.original_filename,
            mime_type=row.mime_type or "",
            file_size=row.file_size or 0,
            uploader_id=row.uploader_id,
            uploader_name=uploader_cache[row.uploader_id],
            created_at=row.created_at,
        )
        attachments_out.append(att)
        if row.item_id is not None:
            by_item.setdefault(row.item_id, []).append(att)

    items_out = []
    for i in sorted(report.items, key=lambda x: x.sort_order):
        inferred_id = None
        inferred_name = None
        project_inferred = False
        if i.project_id:
            pid = i.project_id
            pname = names.get(pid)
        else:
            pid = None
            pname = None
            guessed, _conf, gname = guess_project_id(
                i.task_title or "", i.result_text or "", all_projects
            )
            if guessed is not None:
                inferred_id = guessed
                inferred_name = gname
                project_inferred = True
        eff_for_role = i.project_id or inferred_id
        items_out.append(
            ProgressItemOut(
                id=i.id,
                project_id=pid,
                project_name=pname,
                inferred_project_id=inferred_id,
                inferred_project_name=inferred_name,
                project_inferred=project_inferred,
                member_role=roles.get(eff_for_role, {}).get(report.user_id) if eff_for_role else None,
                lead_name=leads.get(eff_for_role) if eff_for_role else None,
                chain_id=i.chain_id,
                linked_task_id=i.linked_task_id,
                task_title=i.task_title or "",
                no_action=i.no_action,
                result_text=i.result_text,
                quant_value=i.quant_value,
                stuck_status=i.stuck_status,
                stuck_detail=i.stuck_detail,
                stuck_next_action=i.stuck_next_action,
                stuck_follow_up=i.stuck_follow_up,
                stuck_need_who=i.stuck_need_who,
                linked_blocker_id=i.linked_blocker_id,
                next_goal=i.next_goal,
                next_due_date=i.next_due_date,
                risk_level=i.risk_level,
                risk_reason=i.risk_reason,
                risk_impact_date=i.risk_impact_date,
                risk_coordinator=i.risk_coordinator,
                prior_done=i.prior_done,
                tags=normalize_work_tags(i.tags),
                sort_order=i.sort_order,
                attachments=by_item.get(i.id, []),
            )
        )
    phase1 = None
    review_reason = None
    if include_own_review:
        if report.status == "submitted":
            review = await ensure_phase1_review(db, report.id, force=False)
            phase1 = _phase1_out(review)
            if not phase1:
                review_reason = "评价生成失败"
        else:
            review_reason = "周报提交后可查看评价"

    return ProgressReportOut(
        id=report.id,
        period_type=report.period_type,
        period_start=report.period_start,
        period_end=report.period_end or report.period_start,
        theme=report.theme,
        status=report.status,
        author=_user_brief(report.author),
        items=items_out,
        soft_warnings=soft or [],
        submitted_at=report.submitted_at,
        next_plan_synced=next_plan_synced,
        done_synced=done_synced,
        phase1_review=phase1,
        review_unavailable_reason=review_reason,
        attachments=attachments_out,
    )


async def _maybe_create_blocker(
    db: AsyncSession, user: User, item: ProgressItemIn
) -> int | None:
    if item.stuck_status not in ("blocker", "coordination"):
        return item.linked_blocker_id
    # 无项目时不写系统卡点表（后期 LLM 归类后再挂）
    if not item.project_id:
        return item.linked_blocker_id
    if item.linked_blocker_id:
        result = await db.execute(select(Blocker).where(Blocker.id == item.linked_blocker_id))
        blocker = result.scalar_one_or_none()
        if blocker:
            blocker.description = item.stuck_detail or blocker.description
            blocker.next_action = item.stuck_next_action or blocker.next_action
            if item.stuck_follow_up:
                blocker.next_follow_up = item.stuck_follow_up
            if item.stuck_need_who:
                blocker.responsible_party = item.stuck_need_who
            return blocker.id
        return None

    blocker = Blocker(
        project_id=item.project_id,
        assignee_id=user.id,
        description=item.stuck_detail or "周报申报卡点",
        responsible_party=item.stuck_need_who or "待确认",
        party_type="internal",
        found_date=date.today(),
        actions_taken="周报/日报登记",
        status="following",
        next_action=item.stuck_next_action or "跟进",
        next_follow_up=item.stuck_follow_up or date.today(),
    )
    db.add(blocker)
    await db.flush()

    lead = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == item.project_id,
            ProjectMember.member_role == "lead",
        )
    )
    lead_m = lead.scalar_one_or_none()
    if lead_m and lead_m.user_id != user.id:
        proj = await db.get(Project, item.project_id)
        lead_user = await db.get(User, lead_m.user_id)
        if lead_user:
            project_name = proj.name if proj else "项目"
            await dispatch_notification(
                db,
                event_key="blocker_declared",
                users=[lead_user],
                variables={
                    "project_name": project_name,
                    "blocker_desc": blocker.description,
                },
                payload={
                    "type": "blocker_declared",
                    "blocker_id": blocker.id,
                    "project_id": item.project_id,
                },
                ref_type="blocker",
                ref_id=blocker.id,
                fallback_title="辅助人申报了新卡点",
                fallback_body=f"「{project_name}」：{blocker.description}，请在周报看板确认。",
                respect_quiet=False,
            )
    return blocker.id


@router.post("/validate", response_model=ProgressValidateOut)
async def validate_only(
    body: ProgressReportSave,
    user: User = Depends(require_password_updated),
) -> ProgressValidateOut:
    ok, hard, soft = validate_report_items([_item_dict(i) for i in body.items])
    return ProgressValidateOut(ok=ok, hard_errors=hard, soft_warnings=soft)


@router.get("/prefill", response_model=ProgressPrefillOut)
async def prefill(
    period_type: str = Query("week"),
    period_start: date | None = None,
    period_end: date | None = None,
    user: User = Depends(require_permission("report.submit")),
    db: AsyncSession = Depends(get_db),
) -> ProgressPrefillOut:
    start, end = _resolve_period(period_type, period_start, period_end)

    memberships = await db.execute(
        select(ProjectMember).where(ProjectMember.user_id == user.id)
    )
    project_ids = [m.project_id for m in memberships.scalars().all()]

    tasks = []
    blockers = []
    if project_ids:
        t_res = await db.execute(
            select(Task).where(
                Task.assignee_id == user.id,
                Task.project_id.in_(project_ids),
                Task.status != "completed",
            )
        )
        tasks = t_res.scalars().all()
        b_res = await db.execute(
            select(Blocker).where(
                Blocker.assignee_id == user.id,
                Blocker.project_id.in_(project_ids),
                Blocker.status != "resolved",
            )
        )
        blockers = b_res.scalars().all()

    prior_start = (
        date.fromordinal(start.toordinal() - 7)
        if period_type == "week"
        else date.fromordinal(start.toordinal() - 1)
    )
    prior = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items))
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == period_type,
            ProgressReport.period_start == prior_start,
            ProgressReport.status == "submitted",
        )
    )
    prior_report = prior.scalar_one_or_none()
    prior_checks = []
    if prior_report:
        for it in prior_report.items:
            if it.no_action or not it.next_goal:
                continue
            prior_checks.append(
                {
                    "project_id": it.project_id,
                    "task_title": it.task_title or "",
                    "next_goal": it.next_goal,
                    "next_due_date": str(it.next_due_date) if it.next_due_date else None,
                }
            )

    # 预填为任务卡片（project 可空，供参考；用户可不选项目）
    items: list[ProgressItemIn] = []
    for t in tasks:
        items.append(
            ProgressItemIn(
                project_id=None,
                task_title=t.title[:120],
                result_text="",
            )
        )
    for b in blockers:
        items.append(
            ProgressItemIn(
                project_id=None,
                task_title=(b.description or "未关闭卡点")[:120],
                stuck_status="blocker",
                stuck_detail=b.description,
                stuck_next_action=b.next_action,
                stuck_follow_up=b.next_follow_up,
                linked_blocker_id=b.id,
            )
        )
    if not items:
        items = [ProgressItemIn()]

    return ProgressPrefillOut(
        period_type=period_type if period_type in ("week", "day") else "week",
        period_start=start,
        period_end=end,
        items=items,
        prior_checks=prior_checks,
    )


@router.get("/my-periods", response_model=list[MyReportPeriod])
async def list_my_periods(
    period_type: str = Query("week"),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[MyReportPeriod]:
    """当前用户已有周报/日报的周期列表（写周报页快捷切换用）。"""
    ptype = period_type if period_type in ("week", "day") else "week"
    rows = await db.execute(
        select(
            ProgressReport.period_start,
            ProgressReport.period_end,
            ProgressReport.status,
            func.count(ProgressReportItem.id),
        )
        .outerjoin(ProgressReportItem, ProgressReportItem.report_id == ProgressReport.id)
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == ptype,
        )
        .group_by(ProgressReport.period_start, ProgressReport.period_end, ProgressReport.status)
        .order_by(ProgressReport.period_start.desc())
        .limit(24)
    )
    return [
        MyReportPeriod(
            period_start=row[0],
            period_end=row[1] or row[0],
            status=row[2],
            item_count=int(row[3] or 0),
        )
        for row in rows.all()
    ]


@router.get("/mine", response_model=ProgressReportOut | None)
async def get_mine(
    period_type: str = Query("week"),
    period_start: date | None = None,
    period_end: date | None = None,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> ProgressReportOut | None:
    start, _end = _resolve_period(period_type, period_start, period_end)
    ptype = period_type if period_type in ("week", "day") else "week"
    result = await db.execute(
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == ptype,
            ProgressReport.period_start == start,
        )
    )
    report = result.scalar_one_or_none()
    # 选中日落在某份周报周期内时也能打开（避免必须精确点在 period_start）
    if not report:
        result2 = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.user_id == user.id,
                ProgressReport.period_type == ptype,
                ProgressReport.period_start <= start,
            )
            .order_by(ProgressReport.period_start.desc())
        )
        for r in result2.scalars().all():
            rend = r.period_end or r.period_start
            if r.period_start <= start <= rend:
                report = r
                break
    if not report:
        return None
    return await _report_out(db, report, include_own_review=True)


@router.post("", response_model=ProgressReportOut)
async def save_report(
    body: ProgressReportSave,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_permission("report.submit")),
    db: AsyncSession = Depends(get_db),
) -> ProgressReportOut:
    period_type = body.period_type if body.period_type in ("week", "day") else "week"
    start, end = _resolve_period(period_type, body.period_start, body.period_end)

    # 同一张卡片被重复提交时只留一份，避免看周报时出现整段复制
    body_items = _dedupe_report_items(list(body.items))
    item_dicts = [_item_dict(i) for i in body_items]
    ok, hard, soft = validate_report_items(item_dicts)
    if body.submit and not ok:
        raise HTTPException(status_code=400, detail={"hard_errors": hard, "soft_warnings": soft})

    # 行锁：同一人同一周期的并发保存排队，避免两套 clear+insert 叠出双倍卡片
    result = await db.execute(
        select(ProgressReport)
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == period_type,
            ProgressReport.period_start == start,
        )
        .with_for_update()
    )
    report = result.scalar_one_or_none()
    # 历史已交周期默认禁止静默覆盖（本周已交仍可直接改）
    if report and report.status == "submitted" and not body.force_edit_submitted:
        this_monday = monday_of(date.today())
        is_historical = (
            (period_type == "week" and start < this_monday)
            or (period_type == "day" and start < date.today())
        )
        if is_historical:
            raise HTTPException(
                status_code=409,
                detail="这是历史已交周期，确认修改后请勾选「解锁修改」再保存",
            )
    # 保存会重建条目：先记下「条目序号 → 附件 id」，清空后按序号挂回
    attach_by_sort: dict[int, list[int]] = {}
    if not report:
        report = ProgressReport(
            user_id=user.id,
            period_type=period_type,
            period_start=start,
            period_end=end,
        )
        db.add(report)
        await db.flush()
    else:
        await db.refresh(report, attribute_names=["items"])
        old_sort_by_id = {old.id: old.sort_order for old in report.items if old.id is not None}
        if old_sort_by_id:
            att_rows = (
                await db.execute(
                    select(ProgressReportAttachment.id, ProgressReportAttachment.item_id).where(
                        ProgressReportAttachment.report_id == report.id,
                        ProgressReportAttachment.item_id.is_not(None),
                    )
                )
            ).all()
            for att_id, item_id in att_rows:
                sort_ord = old_sort_by_id.get(item_id)
                if sort_ord is None:
                    continue
                attach_by_sort.setdefault(sort_ord, []).append(att_id)
        # 用集合清空触发 delete-orphan，避免旧卡片还留在内存里被再次写入
        report.items.clear()
        await db.flush()

    theme = compose_theme_from_titles(item_dicts) or (body.theme or "")
    report.period_end = end
    report.theme = theme
    report.status = "submitted" if body.submit else "draft"
    report.submitted_at = datetime.now(timezone.utc) if body.submit else None

    # 没选项目、或选了「日常任务」时，按项目名和同事近期任务上下文自动归类
    classify_bundle = await build_project_context(db)
    daily_id = classify_bundle["daily_id"]

    new_items: list[ProgressReportItem] = []
    for idx, raw in enumerate(body_items):
        linked_id = raw.linked_blocker_id
        if body.submit and raw.stuck_status in ("blocker", "coordination"):
            linked_id = await _maybe_create_blocker(db, user, raw)
        project_id = raw.project_id
        title = (raw.task_title or "").strip()
        result_text = raw.result_text or ""
        if project_id is None or project_id == daily_id:
            if not looks_like_daily_work(title, result_text):
                guessed, _conf, _name, _reason = guess_text_in_bundle(
                    classify_bundle, user.id, title, result_text
                )
                if guessed:
                    project_id = guessed
        item = ProgressReportItem(
            report_id=report.id,
            project_id=project_id,
            chain_id=(raw.chain_id or None),
            linked_task_id=raw.linked_task_id,
            task_title=(raw.task_title or "").strip()[:120],
            no_action=raw.no_action,
            result_text=raw.result_text,
            quant_value=raw.quant_value,
            stuck_status=raw.stuck_status,
            stuck_detail=raw.stuck_detail,
            stuck_next_action=raw.stuck_next_action,
            stuck_follow_up=raw.stuck_follow_up,
            stuck_need_who=raw.stuck_need_who,
            linked_blocker_id=linked_id,
            next_goal=raw.next_goal,
            next_due_date=raw.next_due_date,
            risk_level=raw.risk_level,
            risk_reason=raw.risk_reason,
            risk_impact_date=raw.risk_impact_date,
            risk_coordinator=raw.risk_coordinator,
            prior_done=raw.prior_done,
            tags=normalize_work_tags(raw.tags),
            sort_order=idx,
        )
        db.add(item)
        new_items.append(item)

    await db.flush()
    # 按卡片序号把附件挂回新条目（条目被删时 item_id 已 SET NULL）
    for idx, item in enumerate(new_items):
        for att_id in attach_by_sort.get(idx, []):
            att = await db.get(ProgressReportAttachment, att_id)
            if att and att.report_id == report.id:
                att.item_id = item.id

    # 先把周报正文落库并立刻返回；AI 待办同步放到后台，避免用户干等
    report_id = report.id
    await db.commit()

    if body.submit and period_type == "week":
        background_tasks.add_task(_sync_submitted_report_todos, report_id, user.id)

    result = await db.execute(
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(ProgressReport.id == report_id)
    )
    report = result.scalar_one()
    return await _report_out(
        db, report, soft=soft, next_plan_synced=0, done_synced=0
    )


async def _sync_submitted_report_todos(report_id: int, user_id: int) -> None:
    """提交成功后的后台任务：把下周计划 / 做成了什么同步到待办（可含 LLM）。"""
    async with async_session() as db:
        result = await db.execute(
            select(ProgressReport)
            .options(selectinload(ProgressReport.items))
            .where(ProgressReport.id == report_id)
        )
        report = result.scalar_one_or_none()
        if not report or report.status != "submitted" or report.period_type != "week":
            return
        author = await db.get(User, user_id)
        if not author:
            return
        try:
            await sync_next_plan_todos(db, report=report, author=author)
        except Exception:
            logging.getLogger(__name__).exception(
                "background sync_next_plan_todos failed report_id=%s", report_id
            )
        try:
            await sync_done_result_todos(db, report=report, author=author)
        except Exception:
            logging.getLogger(__name__).exception(
                "background sync_done_result_todos failed report_id=%s", report_id
            )
        await db.commit()


@router.post("/ai-coach", response_model=AiCoachOut)
async def coach(
    body: AiCoachRequest,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> AiCoachOut:
    refuse_legacy_ai()
    item_dicts = [_item_dict(i) for i in body.items]
    theme = compose_theme_from_titles(item_dicts) or body.theme
    data = await ai_coach(theme, item_dicts, db=db)
    return AiCoachOut(**data)


@router.post("/ai-structure", response_model=AiStructureOut)
async def structure_item(
    body: AiStructureRequest,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> AiStructureOut:
    """单卡结构化整理 + 历史链/项目/交办建议（确认后由前端写入）。"""
    refuse_legacy_ai()
    start, end = _resolve_period(body.period_type, body.period_start, body.period_end)
    draft = _item_dict(body.item)
    data = await ai_structure(
        db,
        user,
        draft,
        body.period_type if body.period_type in ("week", "day") else "week",
        start,
        end,
        body.chain_id or body.item.chain_id,
    )
    return AiStructureOut(**data)


@router.get("/chains/{chain_id}", response_model=ChainTimelineOut)
async def get_chain_timeline(
    chain_id: str,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> ChainTimelineOut:
    """跨周任务链时间线：本人可看；有 report.view.all 可看他人。"""
    cid = (chain_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="chain_id 无效")

    result = await db.execute(
        select(ProgressReportItem, ProgressReport)
        .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
        .options(selectinload(ProgressReport.author))
        .where(ProgressReportItem.chain_id == cid)
        .order_by(ProgressReport.period_start.asc(), ProgressReportItem.sort_order.asc())
    )
    rows = result.all()
    if not rows:
        raise HTTPException(status_code=404, detail="任务链不存在或尚无记录")

    owners = {report.user_id for _it, report in rows}
    perms = get_user_permissions(user)
    if user.id not in owners and "report.view.all" not in perms:
        raise HTTPException(status_code=403, detail="无权查看该任务链")

    project_ids = [it.project_id for it, _r in rows if it.project_id]
    names: dict[int, str] = {}
    if project_ids:
        pr = await db.execute(select(Project).where(Project.id.in_(project_ids)))
        names = {p.id: p.name for p in pr.scalars().all()}

    nodes: list[ChainTimelineNode] = []
    title = ""
    project_id = None
    linked_task_id = None
    for it, report in rows:
        if it.task_title:
            title = it.task_title
        if it.project_id:
            project_id = it.project_id
        if it.linked_task_id:
            linked_task_id = it.linked_task_id
        nodes.append(
            ChainTimelineNode(
                item_id=it.id,
                report_id=report.id,
                period_type=report.period_type,
                period_start=report.period_start,
                period_end=report.period_end or report.period_start,
                task_title=it.task_title or "",
                result_text=it.result_text or "",
                quant_value=it.quant_value or "",
                stuck_status=it.stuck_status or "none",
                stuck_detail=it.stuck_detail or "",
                next_goal=it.next_goal or "",
                next_due_date=it.next_due_date,
                project_id=it.project_id,
                project_name=names.get(it.project_id) if it.project_id else None,
                linked_task_id=it.linked_task_id,
                author_name=report.author.name if report.author else "",
            )
        )

    return ChainTimelineOut(
        chain_id=cid,
        title=title or "任务链",
        project_id=project_id,
        project_name=names.get(project_id) if project_id else None,
        linked_task_id=linked_task_id,
        nodes=nodes,
    )


@router.post("/chains/link-history")
async def link_history_to_chain(
    body: dict,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """采用任务链时：把历史卡片补上同一 chain_id（仅本人、且原为空）。"""
    chain_id = (body.get("chain_id") or "").strip()
    seed_item_id = body.get("seed_item_id")
    if not chain_id or not isinstance(seed_item_id, int):
        raise HTTPException(status_code=400, detail="需要 chain_id 与 seed_item_id")

    result = await db.execute(
        select(ProgressReportItem, ProgressReport)
        .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
        .where(ProgressReportItem.id == seed_item_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="历史卡片不存在")
    item, report = row
    if report.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能关联自己的历史卡片")
    if item.chain_id and item.chain_id != chain_id:
        raise HTTPException(status_code=400, detail="该卡片已属于其他任务链")
    item.chain_id = chain_id
    await db.commit()
    return {"ok": True, "chain_id": chain_id, "seed_item_id": seed_item_id}


@router.post("/blockers/{blocker_id}/ack")
async def ack_blocker(
    blocker_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(Blocker).where(Blocker.id == blocker_id))
    blocker = result.scalar_one_or_none()
    if not blocker:
        raise HTTPException(status_code=404, detail="卡点不存在")
    lead = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == blocker.project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.member_role == "lead",
        )
    )
    if not lead.scalar_one_or_none():
        perms = get_user_permissions(user)
        if "report.view.all" not in perms:
            raise HTTPException(status_code=403, detail="仅主负责人可确认已知悉")
    blocker.lead_acked_at = datetime.now(timezone.utc)
    blocker.lead_acked_by_id = user.id
    await db.commit()
    return {"ok": True}


@router.get("/board", response_model=ReportBoardOut)
async def manager_board(
    period_type: str = Query("week"),
    period_start: date | None = None,
    period_end: date | None = None,
    mode: str = Query("summary"),
    history_weeks: int = Query(12, ge=1, le=52),
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> ReportBoardOut:
    start, end = _resolve_period(period_type, period_start, period_end)
    ptype = period_type if period_type in ("week", "day") else "week"
    board_mode = mode if mode in ("summary", "full") else "summary"
    auto_switched = False
    # 用户显式点选周期时（含无数据周），不要自动跳走
    explicit_period = period_start is not None

    # 可选周期：从当年（或上一年）9 月 1 日到本周/下周（含 0 人提交）
    today = date.today()
    period_floor = _period_floor(today)
    future_cap = monday_of(today) + timedelta(days=7)  # 最多看到「下周」
    period_rows = await db.execute(
        select(
            ProgressReport.period_start,
            ProgressReport.period_end,
            func.count(ProgressReport.id),
        )
        .join(User, User.id == ProgressReport.user_id)
        .where(
            ProgressReport.period_type == ptype,
            ProgressReport.status == "submitted",
            func.lower(User.username).in_([u.lower() for u in REPORT_ROSTER_USERNAMES]),
            ProgressReport.period_start <= future_cap,
            ProgressReport.period_start >= period_floor - timedelta(days=6),  # 含跨 9/1 那一周
        )
        .group_by(ProgressReport.period_start, ProgressReport.period_end)
        .order_by(ProgressReport.period_start.desc())
    )
    # period_start -> (period_end, submitted_count)
    count_map: dict[date, tuple[date, int]] = {}
    for row in period_rows.all():
        ps, pe, cnt = row[0], row[1] or row[0], int(row[2])
        prev = count_map.get(ps)
        if prev is None:
            count_map[ps] = (pe, cnt)
        else:
            count_map[ps] = (max(prev[0], pe), prev[1] + cnt)

    available_periods: list[ReportBoardPeriod] = []
    if ptype == "week":
        until_monday = monday_of(today)
        next_monday = until_monday + timedelta(days=7)
        if next_monday <= future_cap:
            until_monday = next_monday
        week_starts = _week_starts_from_floor(period_floor, until_monday)
        seen: set[date] = set()
        for ps in reversed(week_starts):  # 最近的在前
            pe, cnt = count_map.get(ps, (sunday_of(ps), 0))
            available_periods.append(
                ReportBoardPeriod(period_start=ps, period_end=pe, submitted_count=cnt)
            )
            seen.add(ps)
        # 补上不在日历网格里、但库里确有提交且不早于 9/1 的周期
        for ps, (pe, cnt) in sorted(count_map.items(), key=lambda x: x[0], reverse=True):
            if ps not in seen and ps <= future_cap and (pe >= period_floor or ps >= period_floor):
                available_periods.append(
                    ReportBoardPeriod(period_start=ps, period_end=pe, submitted_count=cnt)
                )
        available_periods.sort(key=lambda p: p.period_start, reverse=True)
    else:
        # 日报：从 9 月 1 日到今天（含无提交）
        day_count = (today - period_floor).days + 1
        day_starts = [period_floor + timedelta(days=i) for i in range(max(day_count, 1))]
        seen_d: set[date] = set()
        for ps in reversed(day_starts):
            pe, cnt = count_map.get(ps, (ps, 0))
            available_periods.append(
                ReportBoardPeriod(period_start=ps, period_end=pe, submitted_count=cnt)
            )
            seen_d.add(ps)
        for ps, (pe, cnt) in sorted(count_map.items(), key=lambda x: x[0], reverse=True):
            if ps not in seen_d and ps <= future_cap and ps >= period_floor:
                available_periods.append(
                    ReportBoardPeriod(period_start=ps, period_end=pe, submitted_count=cnt)
                )
        available_periods.sort(key=lambda p: p.period_start, reverse=True)

    async def _load_reports(ps: date) -> list[ProgressReport]:
        # 精确匹配 period_start；若无，再按「日期落在周期内」兜底
        result = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.period_type == ptype,
                ProgressReport.period_start == ps,
                ProgressReport.status == "submitted",
            )
        )
        found = list(result.scalars().all())
        if found:
            return found
        # 选中日落在某份周报 [period_start, period_end] 内
        result2 = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.period_type == ptype,
                ProgressReport.status == "submitted",
                ProgressReport.period_start <= ps,
            )
        )
        covering: list[ProgressReport] = []
        for r in result2.scalars().all():
            rend = r.period_end or r.period_start
            if r.period_start <= ps <= rend:
                covering.append(r)
        return covering

    reports = await _load_reports(start)

    # 首次进入且本周无提交时，自动切到最近「有数据」的周期；用户点选空周期则保留
    if not reports and not explicit_period:
        with_data = [p for p in available_periods if p.submitted_count > 0]
        if with_data:
            start = with_data[0].period_start
            end = with_data[0].period_end or start
            reports = await _load_reports(start)
            auto_switched = True
    elif reports:
        # 统一展示该批报告的周期起止
        start = min(r.period_start for r in reports)
        end = max((r.period_end or r.period_start) for r in reports)

    users = await list_report_roster_users(db)
    stats_map = await _submit_stats_map(
        db,
        user_ids=[u.id for u in users],
        period_type=ptype if ptype == "week" else "week",
        weeks=history_weeks,
        anchor=start,
    )

    by_user = {r.user_id: r for r in reports}

    people: list[ReportBoardPerson] = []
    for u in users:
        r = by_user.get(u.id)
        escalate = 0
        blockers_n = 0
        report_out = None
        if r:
            for it in r.items:
                if it.risk_level == "escalate":
                    escalate += 1
                if it.stuck_status in ("blocker", "coordination"):
                    blockers_n += 1
            if board_mode == "full":
                _, _, soft = validate_report_items([_item_dict(i) for i in r.items])
                report_out = await _report_out(db, r, soft=soft)
        sw, ew, rate = stats_map.get(u.id, (0, history_weeks, 0.0))
        people.append(
            ReportBoardPerson(
                user=_user_brief(u),
                submitted=bool(r),
                escalate_count=escalate,
                blocker_count=blockers_n,
                report_id=r.id if r else None,
                report=report_out,
                submitted_weeks=sw,
                expected_weeks=ew,
                submit_rate=rate,
            )
        )
    # 已交的排前面，方便管理者先看有内容的
    people.sort(key=lambda p: (not p.submitted, p.user.name or ""))

    projects_result = await db.execute(select(Project))
    projects = projects_result.scalars().all()
    project_guess_list = [{"project_id": p.id, "name": p.name} for p in projects]

    def _item_effective_project_id(it: ProgressReportItem) -> tuple[int | None, bool]:
        if it.project_id:
            return it.project_id, False
        guessed, _c, _n = guess_project_id(
            it.task_title or "", it.result_text or "", project_guess_list
        )
        return guessed, guessed is not None

    board_projects: list[ReportBoardProject] = []
    unassigned_items: list[dict] = []
    # 项目视图仍需要全文切片；summary 模式下也加载项目区（管理者次级 Tab）
    for p in projects:
        mem = await db.execute(
            select(ProjectMember)
            .options(selectinload(ProjectMember.user))
            .where(ProjectMember.project_id == p.id)
        )
        members = list(mem.scalars().all())
        lead_u = next((m.user for m in members if m.member_role == "lead"), None)
        assistants = [_user_brief(m.user) for m in members if m.member_role == "assistant"]
        member_ids = {m.user_id for m in members}
        related = []
        escalate = 0
        for r in reports:
            hit = False
            for it in r.items:
                eff_id, _inf = _item_effective_project_id(it)
                if eff_id == p.id:
                    hit = True
                    if it.risk_level == "escalate":
                        escalate += 1
            if hit:
                related.append(await _report_out(db, r))
        bl = await db.execute(
            select(Blocker).where(Blocker.project_id == p.id, Blocker.status != "resolved")
        )
        open_bs = list(bl.scalars().all())
        unacked = sum(1 for b in open_bs if b.lead_acked_at is None)
        lead_aligned = True
        if lead_u:
            lead_report = by_user.get(lead_u.id)
            assist_escalate = any(
                r.user_id in member_ids
                and r.user_id != lead_u.id
                and any(
                    (
                        (_item_effective_project_id(it)[0] == p.id)
                        and (it.risk_level == "escalate" or it.stuck_status != "none")
                    )
                    for it in r.items
                )
                for r in reports
            )
            if assist_escalate and (not lead_report or unacked > 0):
                lead_aligned = False

        if not related and not open_bs:
            continue
        board_projects.append(
            ReportBoardProject(
                project_id=p.id,
                project_name=p.name,
                lead=_user_brief(lead_u) if lead_u else None,
                assistants=assistants,
                reports=related,
                open_blockers=len(open_bs),
                unacked_blockers=unacked,
                escalate_count=escalate,
                lead_aligned=lead_aligned,
            )
        )

    board_projects.sort(key=lambda x: (x.lead_aligned, -x.escalate_count, -x.unacked_blockers))

    for r in reports:
        for it in r.items:
            eff_id, inferred = _item_effective_project_id(it)
            # 显式无项目且推断失败 → 未归类；已显式绑定或推断成功不算未归类
            if it.project_id or inferred:
                continue
            unassigned_items.append(
                {
                    "report_id": r.id,
                    "author_name": r.author.name if r.author else "",
                    "task_title": it.task_title or "",
                    "no_action": it.no_action,
                    "result_text": it.result_text,
                    "quant_value": it.quant_value,
                    "next_goal": it.next_goal,
                    "next_due_date": str(it.next_due_date) if it.next_due_date else None,
                    "stuck_status": it.stuck_status,
                    "stuck_detail": it.stuck_detail,
                    "stuck_next_action": it.stuck_next_action,
                    "stuck_follow_up": str(it.stuck_follow_up) if it.stuck_follow_up else None,
                    "risk_level": it.risk_level,
                    "risk_reason": it.risk_reason,
                    "risk_coordinator": it.risk_coordinator,
                    "tags": normalize_work_tags(it.tags),
                }
            )

    return ReportBoardOut(
        period_type=ptype,
        period_start=start,
        period_end=end,
        people=people,
        projects=board_projects,
        unassigned_items=unassigned_items,
        available_periods=available_periods,
        auto_switched=auto_switched,
        mode=board_mode,
    )


@router.get("/by-project/{project_id}", response_model=ProjectRelatedReportsOut)
async def project_related_reports(
    project_id: int,
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> ProjectRelatedReportsOut:
    """项目详情：跨周关联周报（已挂 / 日常·空推断 / 任务关联），按周期时间节点返回。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    daily = await ensure_daily_project(db)
    daily_id = daily.id

    projects_result = await db.execute(select(Project))
    all_projects = list(projects_result.scalars().all())
    project_guess_list = [{"project_id": p.id, "name": p.name} for p in all_projects]

    result = await db.execute(
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(
            ProgressReport.period_type == "week",
            ProgressReport.status == "submitted",
        )
        .order_by(ProgressReport.period_start.desc())
    )
    reports = list(result.scalars().all())

    linked_task_ids = {
        it.linked_task_id
        for r in reports
        for it in r.items
        if it.linked_task_id is not None
    }
    task_project_map: dict[int, int] = {}
    if linked_task_ids:
        tres = await db.execute(
            select(Task.id, Task.project_id).where(Task.id.in_(linked_task_ids))
        )
        task_project_map = {tid: pid for tid, pid in tres.all()}

    # period_start -> list of (report_out_partial)
    period_map: dict[date, list[ProjectRelatedReportOut]] = {}
    period_end_map: dict[date, date] = {}

    for r in reports:
        matched_items: list[tuple[ProgressReportItem, list[str]]] = []
        for it in r.items:
            linked_pid = (
                task_project_map.get(it.linked_task_id)
                if it.linked_task_id is not None
                else None
            )
            reasons = match_item_to_project(
                item_project_id=it.project_id,
                task_title=it.task_title or "",
                result_text=it.result_text or "",
                linked_task_project_id=linked_pid,
                target_project_id=project_id,
                daily_project_id=daily_id,
                project_guess_list=project_guess_list,
            )
            if reasons:
                matched_items.append((it, reasons))
        if not matched_items:
            continue

        full = await _report_out(db, r)
        reasons_by_id = {it.id: reasons for it, reasons in matched_items}
        related_items: list[ProjectRelatedItemOut] = []
        for item_out in full.items:
            reasons = reasons_by_id.get(item_out.id)
            if not reasons:
                continue
            data = item_out.model_dump()
            # 推断命中时补齐展示字段（空/日常条目）
            if "inferred" in reasons and not data.get("project_inferred"):
                data["inferred_project_id"] = project_id
                data["inferred_project_name"] = project.name
                data["project_inferred"] = True
            related_items.append(
                ProjectRelatedItemOut(**data, match_reasons=reasons)
            )
        if not related_items:
            continue

        ps = r.period_start
        pe = r.period_end or r.period_start
        period_end_map[ps] = max(period_end_map.get(ps, pe), pe)
        period_map.setdefault(ps, []).append(
            ProjectRelatedReportOut(
                id=full.id,
                period_type=full.period_type,
                period_start=full.period_start,
                period_end=full.period_end,
                theme=full.theme,
                status=full.status,
                author=full.author,
                items=related_items,
                submitted_at=full.submitted_at,
            )
        )

    periods: list[ProjectRelatedPeriodOut] = []
    for ps in sorted(period_map.keys(), reverse=True):
        periods.append(
            ProjectRelatedPeriodOut(
                period_start=ps,
                period_end=period_end_map.get(ps, ps),
                reports=period_map[ps],
            )
        )

    return ProjectRelatedReportsOut(project_id=project_id, periods=periods)


@router.get("/submission-matrix", response_model=SubmissionMatrixOut)
async def submission_matrix(
    period_type: str = Query("week"),
    weeks: int = Query(12, ge=1, le=52),
    anchor: date | None = None,
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> SubmissionMatrixOut:
    """近 N 周 × 人员提交矩阵，便于考核与催办。"""
    ptype = period_type if period_type in ("week", "day") else "week"
    # 矩阵按周统计更有考核意义；日报模式也按周对齐
    matrix_type = "week"
    starts = _week_starts(anchor or date.today(), weeks)
    users = await list_report_roster_users(db)
    user_ids = [u.id for u in users]
    submitted_map: dict[tuple[int, date], int] = {}
    if user_ids and starts:
        result = await db.execute(
            select(ProgressReport.user_id, ProgressReport.period_start, ProgressReport.id)
            .where(
                ProgressReport.period_type == matrix_type,
                ProgressReport.status == "submitted",
                ProgressReport.user_id.in_(user_ids),
                ProgressReport.period_start.in_(starts),
            )
        )
        for uid, ps, rid in result.all():
            submitted_map[(uid, ps)] = rid

    periods: list[SubmissionMatrixPeriod] = []
    for ps in reversed(starts):  # 最近的在前
        pe = sunday_of(ps)
        cells = [
            SubmissionMatrixCell(
                user_id=u.id,
                submitted=(u.id, ps) in submitted_map,
                report_id=submitted_map.get((u.id, ps)),
            )
            for u in users
        ]
        periods.append(SubmissionMatrixPeriod(period_start=ps, period_end=pe, cells=cells))

    return SubmissionMatrixOut(
        period_type=ptype,
        weeks=weeks,
        people=[_user_brief(u) for u in users],
        periods=periods,
    )


@router.get("/users/{user_id}/workbench", response_model=PersonWorkbenchOut)
async def person_workbench(
    user_id: int,
    period_type: str = Query("week"),
    period_start: date | None = None,
    period_end: date | None = None,
    history_limit: int = Query(16, ge=1, le=52),
    history_weeks: int = Query(12, ge=1, le=52),
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> PersonWorkbenchOut:
    """人员详情：本期/历史摘要、完成度、自动一阶段评价。"""
    start, end = _resolve_period(period_type, period_start, period_end)
    ptype = period_type if period_type in ("week", "day") else "week"

    target = await db.execute(select(User).where(User.id == user_id))
    target_user = target.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 本期已提交
    result = await db.execute(
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(
            ProgressReport.user_id == user_id,
            ProgressReport.period_type == ptype,
            ProgressReport.period_start == start,
            ProgressReport.status == "submitted",
        )
    )
    current = result.scalar_one_or_none()
    if not current:
        # 覆盖周期兜底
        result2 = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.user_id == user_id,
                ProgressReport.period_type == ptype,
                ProgressReport.status == "submitted",
                ProgressReport.period_start <= start,
            )
            .order_by(ProgressReport.period_start.desc())
        )
        for r in result2.scalars().all():
            rend = r.period_end or r.period_start
            if r.period_start <= start <= rend:
                current = r
                start = r.period_start
                end = rend
                break

    hist_rows = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items))
        .where(
            ProgressReport.user_id == user_id,
            ProgressReport.period_type == ptype,
        )
        .order_by(ProgressReport.period_start.desc())
        .limit(history_limit)
    )
    history: list[PersonHistoryPeriod] = []
    prior_yes_all = 0
    prior_total_all = 0
    for r in hist_rows.scalars().all():
        ic, esc, blk, py, pn = _item_counts(r)
        prior_yes_all += py
        prior_total_all += py + pn
        history.append(
            PersonHistoryPeriod(
                period_start=r.period_start,
                period_end=r.period_end or r.period_start,
                status=r.status,
                report_id=r.id if r.status == "submitted" else None,
                item_count=ic,
                escalate_count=esc,
                blocker_count=blk,
                prior_done_yes=py,
                prior_done_no=pn,
                prior_done_total=py + pn,
            )
        )

    sw, ew, rate = (
        await _submit_stats_map(
            db,
            user_ids=[user_id],
            period_type="week",
            weeks=history_weeks,
            anchor=start,
        )
    ).get(user_id, (0, history_weeks, 0.0))
    prior_rate = round(prior_yes_all / prior_total_all, 4) if prior_total_all else 0.0
    completion = PersonCompletionStats(
        submitted_weeks=sw,
        expected_weeks=ew,
        submit_rate=rate,
        prior_done_yes=prior_yes_all,
        prior_done_total=prior_total_all,
        prior_done_rate=prior_rate,
    )

    report_out = None
    phase1 = None
    reason = None
    if current:
        report_out = await _report_out(db, current)
        review = await ensure_phase1_review(db, current.id, force=False)
        phase1 = _phase1_out(review)
    else:
        reason = "本期无周报，无法评价"

    return PersonWorkbenchOut(
        user=_user_brief(target_user),
        period_type=ptype,
        period_start=start,
        period_end=end,
        submitted=bool(current),
        report=report_out,
        history=history,
        completion_stats=completion,
        phase1_review=phase1,
        review_unavailable_reason=reason,
    )


@router.get("/users/{user_id}/timelines", response_model=PersonTimelinesOut)
async def person_timelines(
    user_id: int,
    period_type: str = Query("week"),
    period_start: date | None = None,
    include_history: bool = Query(True),
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> PersonTimelinesOut:
    """人员进度链（默认）+ 关联任务事件链。"""
    start, _end = _resolve_period(period_type, period_start, None)
    ptype = period_type if period_type in ("week", "day") else "week"

    target = await db.execute(select(User).where(User.id == user_id))
    if not target.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="用户不存在")

    q = (
        select(ProgressReportItem, ProgressReport)
        .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
        .where(
            ProgressReport.user_id == user_id,
            ProgressReport.period_type == ptype,
            ProgressReport.status == "submitted",
            ProgressReportItem.chain_id.is_not(None),
            ProgressReportItem.chain_id != "",
        )
    )
    if not include_history:
        q = q.where(ProgressReport.period_start == start)
    result = await db.execute(q.order_by(ProgressReport.period_start.asc()))
    rows = result.all()

    chain_ids: list[str] = []
    seen: set[str] = set()
    linked_task_by_chain: dict[str, int] = {}
    title_by_chain: dict[str, str] = {}
    project_by_chain: dict[str, int] = {}
    for it, _r in rows:
        cid = (it.chain_id or "").strip()
        if not cid:
            continue
        if cid not in seen:
            seen.add(cid)
            chain_ids.append(cid)
        if it.task_title:
            title_by_chain[cid] = it.task_title
        if it.project_id:
            project_by_chain[cid] = it.project_id
        if it.linked_task_id:
            linked_task_by_chain[cid] = it.linked_task_id

    # 若当期无 chain，但有 linked_task，也用单节点进度提示
    if not chain_ids:
        q2 = (
            select(ProgressReportItem, ProgressReport)
            .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
            .where(
                ProgressReport.user_id == user_id,
                ProgressReport.period_type == ptype,
                ProgressReport.status == "submitted",
                ProgressReport.period_start == start,
            )
        )
        cur_items = (await db.execute(q2)).all()
        # 无链则返回空列表（前端提示暂无进度链）
        _ = cur_items

    project_ids = list(set(project_by_chain.values()))
    names: dict[int, str] = {}
    if project_ids:
        pr = await db.execute(select(Project).where(Project.id.in_(project_ids)))
        names = {p.id: p.name for p in pr.scalars().all()}

    # 批量拉各链节点
    chains_out: list[PersonTimelineChain] = []
    if chain_ids:
        all_nodes = await db.execute(
            select(ProgressReportItem, ProgressReport)
            .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
            .options(selectinload(ProgressReport.author))
            .where(ProgressReportItem.chain_id.in_(chain_ids))
            .order_by(ProgressReport.period_start.asc(), ProgressReportItem.sort_order.asc())
        )
        nodes_by_chain: dict[str, list[ChainTimelineNode]] = {c: [] for c in chain_ids}
        for it, report in all_nodes.all():
            cid = (it.chain_id or "").strip()
            if cid not in nodes_by_chain:
                continue
            nodes_by_chain[cid].append(
                ChainTimelineNode(
                    item_id=it.id,
                    report_id=report.id,
                    period_type=report.period_type,
                    period_start=report.period_start,
                    period_end=report.period_end or report.period_start,
                    task_title=it.task_title or "",
                    result_text=it.result_text or "",
                    quant_value=it.quant_value or "",
                    stuck_status=it.stuck_status or "none",
                    stuck_detail=it.stuck_detail or "",
                    next_goal=it.next_goal or "",
                    next_due_date=it.next_due_date,
                    project_id=it.project_id,
                    project_name=names.get(it.project_id) if it.project_id else None,
                    linked_task_id=it.linked_task_id,
                    author_name=report.author.name if report.author else "",
                )
            )

        task_ids = list(set(linked_task_by_chain.values()))
        events_by_task: dict[int, list[TaskEventBrief]] = {tid: [] for tid in task_ids}
        if task_ids:
            ev = await db.execute(
                select(TaskEvent)
                .options(selectinload(TaskEvent.actor))
                .where(TaskEvent.task_id.in_(task_ids))
                .order_by(TaskEvent.created_at.asc())
            )
            for e in ev.scalars().all():
                events_by_task.setdefault(e.task_id, []).append(
                    TaskEventBrief(
                        id=e.id,
                        action=e.action,
                        payload=e.payload,
                        actor_name=e.actor.name if e.actor else "",
                        created_at=e.created_at,
                    )
                )

        for cid in chain_ids:
            pid = project_by_chain.get(cid)
            tid = linked_task_by_chain.get(cid)
            chains_out.append(
                PersonTimelineChain(
                    chain_id=cid,
                    title=title_by_chain.get(cid) or "任务链",
                    project_id=pid,
                    project_name=names.get(pid) if pid else None,
                    linked_task_id=tid,
                    progress_nodes=nodes_by_chain.get(cid, []),
                    task_events=events_by_task.get(tid, []) if tid else [],
                )
            )

    return PersonTimelinesOut(user_id=user_id, period_start=start, chains=chains_out)


@router.post("/{report_id}/ai-review", response_model=Phase1ReviewOut)
async def force_ai_review(
    report_id: int,
    force: bool = Query(True),
    user: User = Depends(require_permission("report.view.all")),
    db: AsyncSession = Depends(get_db),
) -> Phase1ReviewOut:
    """强制（或首次）生成一阶段评价并落库。"""
    review = await ensure_phase1_review(db, report_id, force=force)
    if not review:
        raise HTTPException(status_code=404, detail="周报不存在或未提交，无法评价")
    out = _phase1_out(review)
    assert out is not None
    return out


@router.get("/{report_id}/my-review", response_model=Phase1ReviewOut)
async def get_my_report_review(
    report_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> Phase1ReviewOut:
    """员工查看自己周报的一阶段评价（无需 report.view.all）。"""
    result = await db.execute(select(ProgressReport).where(ProgressReport.id == report_id))
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    if report.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能查看自己的周报评价")
    if report.status != "submitted":
        raise HTTPException(status_code=400, detail="周报提交后可查看评价")
    review = await ensure_phase1_review(db, report_id, force=False)
    out = _phase1_out(review)
    if not out:
        raise HTTPException(status_code=404, detail="评价不可用")
    return out


@router.get("/{report_id}/attachments", response_model=list[ProgressReportAttachmentOut])
async def list_report_attachments(
    report_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[ProgressReportAttachmentOut]:
    report = await db.get(ProgressReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    perms = get_user_permissions(user)
    if report.user_id != user.id and "report.view.all" not in perms:
        raise HTTPException(status_code=403, detail="无权查看")
    rows = (
        await db.execute(
            select(ProgressReportAttachment)
            .where(ProgressReportAttachment.report_id == report_id)
            .order_by(ProgressReportAttachment.id.desc())
        )
    ).scalars().all()
    out: list[ProgressReportAttachmentOut] = []
    for row in rows:
        uploader = await db.get(User, row.uploader_id)
        uploader_name = ""
        if uploader is not None:
            uploader_name = uploader.name or uploader.username or ""
        out.append(
            ProgressReportAttachmentOut(
                id=row.id,
                report_id=row.report_id,
                item_id=row.item_id,
                original_filename=row.original_filename,
                mime_type=row.mime_type or "",
                file_size=row.file_size or 0,
                uploader_id=row.uploader_id,
                uploader_name=uploader_name,
                created_at=row.created_at,
            )
        )
    return out


@router.post("/{report_id}/attachments", response_model=ProgressReportAttachmentOut)
async def upload_report_attachment(
    report_id: int,
    file: UploadFile = File(...),
    item_id: int | None = Form(None),
    user: User = Depends(require_permission("report.submit")),
    db: AsyncSession = Depends(get_db),
) -> ProgressReportAttachmentOut:
    report = await db.get(ProgressReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    if report.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能给自己的周报/日报上传附件")
    bind_item_id: int | None = None
    if item_id is not None:
        item = await db.get(ProgressReportItem, item_id)
        if not item or item.report_id != report_id:
            raise HTTPException(status_code=400, detail="任务条目不属于该周报")
        bind_item_id = item.id
    report_attachment_upload_root()
    original, stored, mime, size = await report_attachment_save_upload(file)
    row = ProgressReportAttachment(
        report_id=report.id,
        item_id=bind_item_id,
        uploader_id=user.id,
        original_filename=original,
        stored_path=stored,
        mime_type=mime,
        file_size=size,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return ProgressReportAttachmentOut(
        id=row.id,
        report_id=row.report_id,
        item_id=row.item_id,
        original_filename=row.original_filename,
        mime_type=row.mime_type or "",
        file_size=row.file_size or 0,
        uploader_id=row.uploader_id,
        uploader_name=user.name or user.username or "",
        created_at=row.created_at,
    )


@router.get("/{report_id}/attachments/{attachment_id}/download")
async def download_report_attachment(
    report_id: int,
    attachment_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    report = await db.get(ProgressReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    perms = get_user_permissions(user)
    if report.user_id != user.id and "report.view.all" not in perms:
        raise HTTPException(status_code=403, detail="无权下载")
    row = await db.get(ProgressReportAttachment, attachment_id)
    if not row or row.report_id != report_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    try:
        path = report_attachment_absolute_path(row.stored_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="附件文件已丢失") from None
    return FileResponse(
        path,
        filename=row.original_filename,
        media_type=row.mime_type or "application/octet-stream",
    )


@router.delete("/{report_id}/attachments/{attachment_id}")
async def delete_report_attachment(
    report_id: int,
    attachment_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    report = await db.get(ProgressReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    if report.user_id != user.id:
        raise HTTPException(status_code=403, detail="只能删除自己周报/日报上的附件")
    row = await db.get(ProgressReportAttachment, attachment_id)
    if not row or row.report_id != report_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    stored = row.stored_path
    await db.delete(row)
    await db.commit()
    report_attachment_delete_stored(stored)
    return {"ok": True}


@router.get("/{report_id}", response_model=ProgressReportOut)
async def get_report(
    report_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> ProgressReportOut:
    result = await db.execute(
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(ProgressReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="周报不存在")
    perms = get_user_permissions(user)
    if report.user_id != user.id and "report.view.all" not in perms:
        raise HTTPException(status_code=403, detail="无权查看")
    return await _report_out(
        db, report, include_own_review=(report.user_id == user.id)
    )
