"""任务创建/完成后同步写入周报草稿（按 linked_task_id 去重）。"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import ProgressReport, ProgressReportItem, Task, User
from app.services.report_quality import monday_of, sunday_of

DELEGATED_DONE_TAG = "交办完成"


async def append_task_to_week_draft(
    db: AsyncSession,
    *,
    user: User,
    task: Task,
) -> ProgressReportItem | None:
    """将任务追加到负责人本周周报草稿。

    - 仅处理本周 draft；若本周已提交则跳过（不改已交周报）
    - 按 linked_task_id 去重
    - 不处理 source=report_next / report_done（避免与周报同步打架）
    """
    if getattr(task, "source", None) in ("report_next", "report_done"):
        return None
    assignee_id = task.assignee_id
    if not assignee_id:
        return None

    today = date.today()
    week_start = monday_of(today)
    week_end = sunday_of(week_start)

    result = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items))
        .where(
            ProgressReport.user_id == assignee_id,
            ProgressReport.period_type == "week",
            ProgressReport.period_start == week_start,
        )
    )
    report = result.scalar_one_or_none()
    if report and report.status == "submitted":
        return None

    existing_items: list[ProgressReportItem] = []
    if not report:
        report = ProgressReport(
            user_id=assignee_id,
            period_type="week",
            period_start=week_start,
            period_end=week_end,
            theme="",
            status="draft",
        )
        db.add(report)
        await db.flush()
    else:
        existing_items = list(report.items or [])

    for it in existing_items:
        if it.linked_task_id == task.id:
            # 更新标题/目标/项目（草稿可跟任务走）
            it.task_title = (task.title or "")[:120]
            it.project_id = task.project_id
            goal = (getattr(task, "goal", None) or "").strip()
            if goal:
                it.next_goal = goal[:2000]
            if task.due_date:
                it.next_due_date = task.due_date
            return it

    max_order = (
        await db.execute(
            select(func.coalesce(func.max(ProgressReportItem.sort_order), -1)).where(
                ProgressReportItem.report_id == report.id
            )
        )
    ).scalar_one()
    goal = (getattr(task, "goal", None) or "").strip()
    item = ProgressReportItem(
        report_id=report.id,
        project_id=task.project_id,
        linked_task_id=task.id,
        task_title=(task.title or "")[:120],
        result_text="",
        next_goal=goal[:2000] if goal else "",
        next_due_date=task.due_date,
        stuck_status="none",
        risk_level="normal",
        tags=[],
        sort_order=int(max_order) + 1,
    )
    db.add(item)
    await db.flush()
    return item


async def _get_or_create_week_draft(
    db: AsyncSession,
    *,
    user_id: int,
    week_start: date,
) -> ProgressReport | None:
    """取指定周草稿；若不存在则创建；若已提交则返回 None。"""
    week_end = sunday_of(week_start)
    result = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items))
        .where(
            ProgressReport.user_id == user_id,
            ProgressReport.period_type == "week",
            ProgressReport.period_start == week_start,
        )
    )
    report = result.scalar_one_or_none()
    if report and report.status == "submitted":
        return None
    if not report:
        report = ProgressReport(
            user_id=user_id,
            period_type="week",
            period_start=week_start,
            period_end=week_end,
            theme="",
            status="draft",
        )
        db.add(report)
        await db.flush()
    return report


def _ensure_tag(tags: list | None, tag: str) -> list:
    current = list(tags or [])
    if tag not in current:
        current.append(tag)
    return current


async def _upsert_completion_item(
    db: AsyncSession,
    *,
    report: ProgressReport,
    task: Task,
    result_text: str,
    add_delegated_tag: bool,
) -> ProgressReportItem:
    existing = (
        await db.execute(
            select(ProgressReportItem).where(
                ProgressReportItem.report_id == report.id,
                ProgressReportItem.linked_task_id == task.id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.task_title = (task.title or "")[:120]
        existing.project_id = task.project_id
        existing.result_text = result_text[:5000]
        if add_delegated_tag:
            existing.tags = _ensure_tag(existing.tags, DELEGATED_DONE_TAG)
        return existing

    max_order = (
        await db.execute(
            select(func.coalesce(func.max(ProgressReportItem.sort_order), -1)).where(
                ProgressReportItem.report_id == report.id
            )
        )
    ).scalar_one()
    goal = (getattr(task, "goal", None) or "").strip()
    tags: list = [DELEGATED_DONE_TAG] if add_delegated_tag else []
    item = ProgressReportItem(
        report_id=report.id,
        project_id=task.project_id,
        linked_task_id=task.id,
        task_title=(task.title or "")[:120],
        result_text=result_text[:5000],
        next_goal=goal[:2000] if goal else "",
        next_due_date=task.due_date,
        stuck_status="none",
        risk_level="normal",
        tags=tags,
        sort_order=int(max_order) + 1,
    )
    db.add(item)
    await db.flush()
    return item


async def sync_task_completion_to_week_draft(
    db: AsyncSession,
    *,
    task: Task,
) -> ProgressReportItem | None:
    """任务完成时把结果写回负责人周报草稿。

    - 优先本周草稿；本周已提交则写入下周草稿，并打「交办完成」标签
    - 跳过 source=report_next / report_done
    """
    if getattr(task, "source", None) in ("report_next", "report_done"):
        return None
    assignee_id = task.assignee_id
    if not assignee_id:
        return None

    result_text = (task.result_text or "").strip()
    if not result_text:
        return None

    today = date.today()
    this_week = monday_of(today)
    report = await _get_or_create_week_draft(db, user_id=assignee_id, week_start=this_week)
    add_tag = False
    if report is None:
        next_week = this_week + timedelta(days=7)
        report = await _get_or_create_week_draft(
            db, user_id=assignee_id, week_start=next_week
        )
        add_tag = True
    if report is None:
        return None

    return await _upsert_completion_item(
        db,
        report=report,
        task=task,
        result_text=result_text,
        add_delegated_tag=add_tag,
    )
