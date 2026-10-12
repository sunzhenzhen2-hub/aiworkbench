from datetime import date, datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import (
    Blocker,
    ProgressReport,
    ProgressReportItem,
    ProgressReportReview,
    Project,
    Task,
    TaskAssistant,
    TaskReview,
    User,
    WeeklyReport,
)
from app.rbac.deps import require_password_updated, get_user_permissions
from app.schemas import (
    GuidanceImproveIn,
    PersonalReportReviewItem,
    PersonalReviews,
    PersonalSummary,
    PersonalTaskReviewItem,
    PersonalWorkspace,
    Phase1ReviewOut,
    TaskOut,
    TaskReviewOut,
    TaskStatusUpdate,
    UserBrief,
    WeeklyReportCreate,
    WeeklyReportOut,
)
from app.services.project_service import _blocker_out, get_overview_stats, list_projects
from app.services.report_quality import monday_of, sunday_of
from app.services.report_task_sync import sync_task_completion_to_week_draft
from app.services import assist_analyze, task_service as svc
from app.services.task_notify import notify_task_completed

router = APIRouter(prefix="/personal", tags=["个人工作台"])


def _build_personal_summary(
    my_tasks: list[TaskOut],
    blockers: list,
    projects: list,
    week_report: ProgressReport | None,
    week_item_count: int,
    week_start: date,
    week_end: date,
) -> PersonalSummary:
    """从已加载数据汇总个人概览数字。"""
    today = date.today()
    task_total = len(my_tasks)
    task_done = sum(1 for t in my_tasks if t.status == "completed")
    task_open = task_total - task_done
    task_overdue = sum(
        1
        for t in my_tasks
        if t.status != "completed" and t.due_date is not None and t.due_date < today
    )
    rate = (task_done / task_total) if task_total else 0.0
    blocker_open = len(blockers)
    if week_report is None:
        report_status = "none"
    elif week_report.status == "submitted":
        report_status = "submitted"
    else:
        report_status = "draft"
    return PersonalSummary(
        task_total=task_total,
        task_done=task_done,
        task_open=task_open,
        task_overdue=task_overdue,
        task_completion_rate=round(rate, 4),
        project_count=len(projects),
        blocker_open=blocker_open,
        todo_open=task_open + blocker_open,
        week_report_status=report_status,
        week_report_period_start=week_start,
        week_report_period_end=week_end,
        week_report_item_count=week_item_count,
    )


async def _build_all_summary(
    db: AsyncSession,
    user: User,
    perms: set[str],
    projects: list,
    week_start: date,
    week_end: date,
) -> PersonalSummary:
    """部门负责人 / 超级管理员：全量任务、项目、卡点与团队周报提交。"""
    today = date.today()
    stats = await get_overview_stats(db, user, perms)
    task_total = stats.task_total
    task_done = stats.task_done
    task_open = max(task_total - task_done, 0)

    overdue_result = await db.execute(
        select(func.count())
        .select_from(Task)
        .where(
            Task.status != "completed",
            Task.due_date.is_not(None),
            Task.due_date < today,
        )
    )
    task_overdue = int(overdue_result.scalar_one() or 0)

    blocker_result = await db.execute(
        select(func.count()).select_from(Blocker).where(Blocker.status != "resolved")
    )
    blocker_open = int(blocker_result.scalar_one() or 0)

    submitted = stats.report_submitted
    expected = stats.report_expected
    if expected <= 0:
        report_status = "none"
    elif submitted >= expected:
        report_status = "submitted"
    elif submitted > 0:
        report_status = "draft"
    else:
        report_status = "none"

    period_start = stats.report_period_start or week_start
    period_end = stats.report_period_end or week_end

    return PersonalSummary(
        task_total=task_total,
        task_done=task_done,
        task_open=task_open,
        task_overdue=task_overdue,
        task_completion_rate=stats.task_completion_rate,
        project_count=len(projects),
        blocker_open=blocker_open,
        todo_open=task_open + blocker_open,
        week_report_status=report_status,
        week_report_period_start=period_start,
        week_report_period_end=period_end,
        week_report_item_count=0,
        report_submitted=submitted,
        report_expected=expected,
        key_project_count=int(stats.key_count or 0),
        key_task_count=int(stats.key_task_count or 0),
        report_quality_alerts=int(stats.report_quality_alerts or 0),
    )


@router.get("/workspace", response_model=PersonalWorkspace)
async def workspace(
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> PersonalWorkspace:
    perms = get_user_permissions(user)
    my_tasks = await svc.list_my_personal_tasks(db, user)
    can_dept = svc.can_view_department_todos(user)
    department_tasks = await svc.list_department_open_tasks(db, user) if can_dept else []

    blockers_result = await db.execute(
        select(Blocker)
        .options(selectinload(Blocker.assignee))
        .where(Blocker.assignee_id == user.id, Blocker.status != "resolved")
        .order_by(Blocker.next_follow_up)
    )
    blockers = [_blocker_out(b) for b in blockers_result.scalars().all()]
    projects = await list_projects(db, user, perms)

    week_start = monday_of(date.today())
    week_end = sunday_of(week_start)
    report_result = await db.execute(
        select(ProgressReport).where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == "week",
            ProgressReport.period_start == week_start,
        )
    )
    week_report = report_result.scalar_one_or_none()
    week_item_count = 0
    if week_report is not None:
        cnt_result = await db.execute(
            select(func.count())
            .select_from(ProgressReportItem)
            .where(ProgressReportItem.report_id == week_report.id)
        )
        week_item_count = int(cnt_result.scalar_one() or 0)

    if can_dept:
        summary_scope = "all"
        summary = await _build_all_summary(
            db, user, perms, projects, week_start, week_end
        )
    else:
        summary_scope = "self"
        summary = _build_personal_summary(
            my_tasks, blockers, projects, week_report, week_item_count, week_start, week_end
        )
    return PersonalWorkspace(
        my_tasks=my_tasks,
        department_tasks=department_tasks,
        can_view_department_todos=can_dept,
        my_blockers=blockers,
        my_projects=projects,
        summary_scope=summary_scope,
        summary=summary,
    )


def _phase1_brief(row: ProgressReportReview) -> Phase1ReviewOut:
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


@router.get("/reviews", response_model=PersonalReviews)
async def my_reviews(
    weeks: int = Query(8, ge=1, le=26),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> PersonalReviews:
    """本人可读评价聚合：最近若干周已提交周报评价 + 负责任务/辅助任务评价。"""
    today = date.today()
    week_start = monday_of(today)
    earliest = week_start - timedelta(days=7 * (weeks - 1))

    report_rows = await db.execute(
        select(ProgressReport, ProgressReportReview)
        .outerjoin(
            ProgressReportReview,
            (ProgressReportReview.report_id == ProgressReport.id)
            & (ProgressReportReview.phase == 1),
        )
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.period_type == "week",
            ProgressReport.status == "submitted",
            ProgressReport.period_start >= earliest,
        )
        .order_by(ProgressReport.period_start.desc())
    )
    report_reviews: list[PersonalReportReviewItem] = []
    for report, review in report_rows.all():
        report_reviews.append(
            PersonalReportReviewItem(
                report_id=report.id,
                period_type=report.period_type or "week",
                period_start=report.period_start,
                period_end=report.period_end,
                status=report.status,
                review=_phase1_brief(review) if review else None,
            )
        )

    assist_ids = select(TaskAssistant.task_id).where(TaskAssistant.user_id == user.id)
    task_rows = await db.execute(
        select(TaskReview, Task, Project.name)
        .join(Task, Task.id == TaskReview.task_id)
        .outerjoin(Project, Project.id == Task.project_id)
        .options(selectinload(Task.assistants))
        .where(
            or_(Task.assignee_id == user.id, Task.id.in_(assist_ids)),
        )
        .order_by(TaskReview.updated_at.desc())
        .limit(50)
    )
    task_reviews: list[PersonalTaskReviewItem] = []
    for treview, task, project_name in task_rows.all():
        task_reviews.append(
            PersonalTaskReviewItem(
                task_id=task.id,
                task_title=task.title or "",
                project_id=task.project_id,
                project_name=project_name,
                my_role=svc.my_role_on_task(task, user.id),
                review=TaskReviewOut(
                    id=treview.id,
                    task_id=treview.task_id,
                    summary=treview.summary or "",
                    strengths=list(treview.strengths or []),
                    risks=list(treview.risks or []),
                    suggestions=list(treview.suggestions or []),
                    source=treview.source or "rule",
                    model_name=treview.model_name or "",
                    created_at=treview.created_at,
                    updated_at=treview.updated_at,
                ),
            )
        )

    return PersonalReviews(report_reviews=report_reviews, task_reviews=task_reviews)


@router.get("/guidance")
async def my_guidance(
    limit: int = Query(30, ge=1, le=100),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """本人收到的轻量指导备注（只读）。"""
    items = await assist_analyze.list_my_guidance(db, user, limit=limit)
    return {"items": items}


@router.post("/guidance/{note_id}/read")
async def mark_guidance_read(
    note_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """标记指导已读。"""
    out = await assist_analyze.mark_guidance_read(db, user, note_id)
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="指导不存在")
    return out


@router.post("/guidance/{note_id}/improved")
async def mark_guidance_improved(
    note_id: int,
    body: GuidanceImproveIn | None = Body(default=None),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """标记「我已改进」回执。"""
    note_text = (body.improve_note if body else "") or ""
    out = await assist_analyze.mark_guidance_improved(
        db, user, note_id, improve_note=note_text
    )
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="指导不存在")
    return out


@router.post("/tasks/{task_id}", response_model=TaskOut)
@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def update_my_task(
    task_id: int,
    body: TaskStatusUpdate,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    """兼容旧路径：改状态（负责人或辅助人）。"""
    if body.status not in svc.TASK_STATUSES:
        raise HTTPException(status_code=400, detail="无效的任务状态")
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_edit_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权更新该任务")
    svc.require_accepted(task)
    becoming_completed = body.status == "completed" and body.status != task.status
    if body.status == "completed":
        result = (body.result_text if body.result_text is not None else task.result_text) or ""
        if not result.strip():
            raise HTTPException(status_code=400, detail="完成任务须填写结果说明")
        if body.result_text is not None:
            task.result_text = body.result_text
            svc.add_event(
                db,
                task,
                user.id,
                "result_updated",
                {"field": "result_text", "result_text": body.result_text},
            )
    if body.status != task.status:
        svc.add_event(
            db,
            task,
            user.id,
            "status_changed",
            {"old_status": task.status, "new_status": body.status},
        )
        task.status = body.status
    synced_to_week_draft = False
    if becoming_completed:
        draft_item = await sync_task_completion_to_week_draft(db, task=task)
        synced_to_week_draft = draft_item is not None
    await db.commit()
    dingtalk_notified = False
    if becoming_completed:
        loaded = await svc.load_task(db, task_id, detail=True)
        if loaded:
            dingtalk_notified = await notify_task_completed(
                db, loaded, actor_name=user.name
            )
    task = await svc.load_task(db, task_id, detail=True)
    assert task is not None
    name_row = await db.execute(select(Project.name).where(Project.id == task.project_id))
    project_name = name_row.scalar_one_or_none()
    out = svc.task_to_out(task, project_name=project_name, viewer_id=user.id)
    if becoming_completed:
        return out.model_copy(
            update={
                "synced_to_week_draft": synced_to_week_draft,
                "dingtalk_notified": dingtalk_notified,
            }
        )
    return out


@router.get("/reports", response_model=list[WeeklyReportOut])
async def my_reports(
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[WeeklyReportOut]:
    result = await db.execute(
        select(WeeklyReport, Project.name)
        .join(Project, Project.id == WeeklyReport.project_id)
        .where(WeeklyReport.user_id == user.id)
        .order_by(WeeklyReport.week_start.desc())
    )
    items = []
    for report, project_name in result.all():
        author_result = await db.execute(select(User).where(User.id == report.user_id))
        author = author_result.scalar_one()
        items.append(
            WeeklyReportOut(
                id=report.id,
                project_id=report.project_id,
                project_name=project_name,
                week_start=report.week_start,
                completed=report.completed,
                next_plan=report.next_plan,
                issues=report.issues,
                author=UserBrief(
                    id=author.id, name=author.name, username=author.username, avatar_url=author.avatar_url
                ),
                submitted_at=report.submitted_at,
            )
        )
    return items


@router.post("/reports", response_model=WeeklyReportOut)
async def submit_report(
    body: WeeklyReportCreate,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> WeeklyReportOut:
    result = await db.execute(
        select(WeeklyReport).where(
            WeeklyReport.project_id == body.project_id,
            WeeklyReport.user_id == user.id,
            WeeklyReport.week_start == body.week_start,
        )
    )
    report = result.scalar_one_or_none()
    if report:
        report.completed = body.completed
        report.next_plan = body.next_plan
        report.issues = body.issues
        report.submitted_at = datetime.now(timezone.utc)
    else:
        report = WeeklyReport(
            project_id=body.project_id,
            user_id=user.id,
            week_start=body.week_start,
            completed=body.completed,
            next_plan=body.next_plan,
            issues=body.issues,
            submitted_at=datetime.now(timezone.utc),
        )
        db.add(report)
    await db.commit()
    await db.refresh(report)
    proj = await db.execute(select(Project.name).where(Project.id == body.project_id))
    project_name = proj.scalar_one()
    return WeeklyReportOut(
        id=report.id,
        project_id=report.project_id,
        project_name=project_name,
        week_start=report.week_start,
        completed=report.completed,
        next_plan=report.next_plan,
        issues=report.issues,
        author=UserBrief(id=user.id, name=user.name, username=user.username, avatar_url=user.avatar_url),
        submitted_at=report.submitted_at,
    )
