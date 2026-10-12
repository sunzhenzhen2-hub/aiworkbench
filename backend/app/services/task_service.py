"""任务交办：负责人=执行人；自建/交办；辅助人；流水账。"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Phase, Project, ProjectMember, Task, TaskAssistant, TaskEvent, User
from app.rbac.deps import get_user_permissions
from app.schemas import TaskEventOut, TaskListOut, TaskOut, UserBrief

TASK_STATUSES = {"not_started", "in_progress", "completed"}
ASSIGNMENT_TYPES = {"self", "delegated"}
TASK_PRIORITIES = {"p0", "p1", "p2", "p3", "p4"}
KEY_TASK_PRIORITIES = {"p0", "p1"}
NOTIFY_TASK_PRIORITIES = {"p0"}

_PRIORITY_ALIASES = {
    "critical": "p0",
    "watch": "p1",
    "normal": "p3",
}


def normalize_priority(value: str | None) -> str:
    raw = (value or "p3").strip().lower()
    p = _PRIORITY_ALIASES.get(raw, raw)
    if p not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail="无效的任务级别（P0～P4）")
    return p


def is_key_task_priority(value: str | None) -> bool:
    try:
        return normalize_priority(value) in KEY_TASK_PRIORITIES
    except HTTPException:
        return False


def should_notify_priority(value: str | None) -> bool:
    try:
        return normalize_priority(value) in NOTIFY_TASK_PRIORITIES
    except HTTPException:
        return False


async def resolve_phase_for_project(
    db: AsyncSession, project_id: int, phase_id: int | None
) -> Phase | None:
    """校验阶段属于该项目；phase_id 为空则返回 None。"""
    if phase_id is None:
        return None
    phase = (
        await db.execute(
            select(Phase).where(Phase.id == phase_id, Phase.project_id == project_id)
        )
    ).scalar_one_or_none()
    if not phase:
        raise HTTPException(status_code=400, detail="所属阶段不存在或不属于该项目")
    return phase


def require_accepted(task: Task) -> None:
    pending = (task.assignment_type or "self") == "delegated" and (
        getattr(task, "acceptance_status", "accepted") or "accepted"
    ) == "pending"
    if pending:
        raise HTTPException(status_code=400, detail="请先接收任务后再更新进度")


def _user_brief(user: User) -> UserBrief:
    return UserBrief(
        id=user.id,
        name=user.name,
        username=user.username,
        avatar_url=user.avatar_url,
    )


def my_role_on_task(task: Task, user_id: int) -> str:
    if task.assignee_id == user_id:
        return "owner"
    for row in task.assistants or []:
        if row.user_id == user_id:
            return "assistant"
    return "none"


def task_to_out(
    task: Task,
    *,
    project_name: str | None = None,
    viewer_id: int | None = None,
    include_events: bool = False,
) -> TaskOut:
    events: list[TaskEventOut] = []
    if include_events:
        for ev in task.events or []:
            events.append(
                TaskEventOut(
                    id=ev.id,
                    action=ev.action,
                    actor=_user_brief(ev.actor),
                    payload=ev.payload,
                    created_at=ev.created_at,
                )
            )
    created_by = _user_brief(task.created_by) if task.created_by else None
    phase = getattr(task, "phase", None)
    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        project_name=project_name,
        phase_id=getattr(task, "phase_id", None),
        phase_name=phase.name if phase else None,
        title=task.title,
        goal=getattr(task, "goal", None) or "",
        due_date=task.due_date,
        status=task.status,
        priority=getattr(task, "priority", None) or "p3",
        acceptance_status=getattr(task, "acceptance_status", None) or "accepted",
        accept_by=getattr(task, "accept_by", None),
        accepted_at=getattr(task, "accepted_at", None),
        checklist=getattr(task, "checklist", None),
        assignment_type=task.assignment_type or "self",
        result_text=task.result_text or "",
        my_role=my_role_on_task(task, viewer_id) if viewer_id is not None else None,
        assignee=_user_brief(task.assignee),
        created_by=created_by,
        assistants=[_user_brief(a.user) for a in (task.assistants or []) if a.user],
        events=events,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


TASK_LOAD_OPTS = (
    selectinload(Task.assignee),
    selectinload(Task.created_by),
    selectinload(Task.assistants).selectinload(TaskAssistant.user),
    selectinload(Task.phase),
)

TASK_DETAIL_OPTS = TASK_LOAD_OPTS + (
    selectinload(Task.events).selectinload(TaskEvent.actor),
)


async def load_task(
    db: AsyncSession, task_id: int, *, detail: bool = False
) -> Task | None:
    opts = TASK_DETAIL_OPTS if detail else TASK_LOAD_OPTS
    result = await db.execute(select(Task).options(*opts).where(Task.id == task_id))
    return result.scalar_one_or_none()


async def project_member_ids(db: AsyncSession, project_id: int) -> set[int]:
    result = await db.execute(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project_id)
    )
    return {row[0] for row in result.all()}


async def get_member_role(
    db: AsyncSession, project_id: int, user_id: int
) -> str | None:
    result = await db.execute(
        select(ProjectMember.member_role).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def can_view_project(db: AsyncSession, user: User, project_id: int) -> bool:
    perms = get_user_permissions(user)
    if "project.view.all" in perms:
        return True
    role = await get_member_role(db, project_id, user.id)
    return role is not None


async def can_delegate_on_project(db: AsyncSession, user: User, project_id: int) -> bool:
    """部门负责人（全局）或该项目主负责人（lead）可交办。"""
    perms = get_user_permissions(user)
    if "task.assign" not in perms:
        return False
    if "project.view.all" in perms:
        return True
    role = await get_member_role(db, project_id, user.id)
    return role == "lead"


async def can_manage_owner(db: AsyncSession, user: User, task: Task) -> bool:
    """换负责人：交办权限且是全局或项目 lead，或原交办创建人。"""
    if await can_delegate_on_project(db, user, task.project_id):
        return True
    return task.created_by_id == user.id and task.assignment_type == "delegated"


async def can_edit_task(db: AsyncSession, user: User, task: Task) -> bool:
    """负责人、辅助人、创建人，或可交办的人可改。"""
    if task.created_by_id == user.id:
        return True
    role = my_role_on_task(task, user.id)
    if role in {"owner", "assistant"}:
        return True
    return await can_delegate_on_project(db, user, task.project_id)


async def can_manage_assistants(db: AsyncSession, user: User, task: Task) -> bool:
    if task.assignee_id == user.id:
        return True
    return await can_delegate_on_project(db, user, task.project_id)


def add_event(
    db: AsyncSession,
    task: Task,
    actor_id: int,
    action: str,
    payload: dict[str, Any] | None = None,
) -> TaskEvent:
    event = TaskEvent(task_id=task.id, actor_id=actor_id, action=action, payload=payload)
    db.add(event)
    return event


async def ensure_users_are_members(
    db: AsyncSession, project_id: int, user_ids: list[int], *, label: str
) -> None:
    if not user_ids:
        return
    members = await project_member_ids(db, project_id)
    missing = [uid for uid in user_ids if uid not in members]
    if missing:
        raise HTTPException(status_code=400, detail=f"{label}必须是项目成员")


async def list_project_tasks(
    db: AsyncSession, project_id: int, viewer: User
) -> list[TaskOut]:
    result = await db.execute(
        select(Task)
        .options(*TASK_LOAD_OPTS)
        .where(Task.project_id == project_id)
        .order_by(Task.due_date.nulls_last(), Task.id.desc())
    )
    tasks = result.scalars().all()
    proj = await db.execute(select(Project.name).where(Project.id == project_id))
    name = proj.scalar_one_or_none()
    return [task_to_out(t, project_name=name, viewer_id=viewer.id) for t in tasks]


# 可看「部门待办」：系统管理员、部门负责人（团队管理员按 dept_head）
DEPT_TODO_ROLE_CODES = frozenset({"admin", "dept_head"})


def can_view_department_todos(user: User) -> bool:
    return any(r.code in DEPT_TODO_ROLE_CODES for r in (user.roles or []))


async def _tasks_to_out(db: AsyncSession, tasks: list[Task], viewer_id: int) -> list[TaskOut]:
    project_names: dict[int, str] = {}
    if tasks:
        ids = {t.project_id for t in tasks}
        rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(ids)))
        project_names = {row[0]: row[1] for row in rows.all()}
    return [
        task_to_out(t, project_name=project_names.get(t.project_id), viewer_id=viewer_id)
        for t in tasks
    ]


async def list_my_open_tasks(db: AsyncSession, user: User) -> list[TaskOut]:
    """兼容旧逻辑：我负责或辅助的未完成任务。"""
    assist_ids = select(TaskAssistant.task_id).where(TaskAssistant.user_id == user.id)
    result = await db.execute(
        select(Task)
        .options(*TASK_LOAD_OPTS)
        .where(
            Task.status != "completed",
            or_(Task.assignee_id == user.id, Task.id.in_(assist_ids)),
        )
        .order_by(Task.due_date.nulls_last(), Task.id.desc())
    )
    tasks = list(result.scalars().unique().all())
    return await _tasks_to_out(db, tasks, user.id)


async def list_my_personal_tasks(db: AsyncSession, user: User) -> list[TaskOut]:
    """
    个人待办：
    - 自己建的（assignment_type=self 且负责人为自己）
    - 交办给我的（assignment_type=delegated 且负责人为自己，必须能干活）
    - 我作为辅助人的未完成任务
    """
    assist_ids = select(TaskAssistant.task_id).where(TaskAssistant.user_id == user.id)
    result = await db.execute(
        select(Task)
        .options(*TASK_LOAD_OPTS)
        .where(
            Task.status != "completed",
            or_(
                (Task.assignee_id == user.id) & (Task.assignment_type == "self"),
                (Task.assignee_id == user.id) & (Task.assignment_type == "delegated"),
                Task.id.in_(assist_ids),
            ),
        )
        .order_by(Task.due_date.nulls_last(), Task.id.desc())
    )
    tasks = list(result.scalars().unique().all())
    return await _tasks_to_out(db, tasks, user.id)


async def list_related_tasks(
    db: AsyncSession,
    user: User,
    status: str = "all",
    *,
    search: str | None = None,
    assignee_id: int | None = None,
    priority: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> TaskListOut:
    """
    任务管理列表。
    系统管理员、部门负责人：全部任务。
    其他人：只看自己创建的、自己负责的、自己当辅助人的。
    status：open 未完成 / completed 已完成 / all 全部
    可选：search 标题关键词、assignee_id 负责人、priority 级别（p0–p4）
    分页：page / page_size（默认第 1 页、每页 10 条）
    """
    if status not in {"open", "completed", "all"}:
        raise HTTPException(status_code=400, detail="无效的状态筛选")
    if priority is not None and priority not in TASK_PRIORITIES:
        raise HTTPException(status_code=400, detail="无效的级别筛选")
    if page < 1:
        raise HTTPException(status_code=400, detail="页码无效")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="每页条数无效")
    conds = []
    if not can_view_department_todos(user):
        assist_ids = select(TaskAssistant.task_id).where(TaskAssistant.user_id == user.id)
        conds.append(
            or_(
                Task.created_by_id == user.id,
                Task.assignee_id == user.id,
                Task.id.in_(assist_ids),
            )
        )
    if status == "open":
        conds.append(Task.status != "completed")
    elif status == "completed":
        conds.append(Task.status == "completed")
    keyword = (search or "").strip()
    if keyword:
        conds.append(Task.title.ilike(f"%{keyword}%"))
    if assignee_id is not None:
        conds.append(Task.assignee_id == assignee_id)
    if priority is not None:
        conds.append(Task.priority == priority)

    count_stmt = select(func.count(Task.id))
    if conds:
        count_stmt = count_stmt.where(*conds)
    total = int((await db.execute(count_stmt)).scalar_one() or 0)

    stmt = select(Task).options(*TASK_LOAD_OPTS)
    if conds:
        stmt = stmt.where(*conds)
    # 任务管理默认排序（三类都靠前，避免大量逾期普通任务淹没重点）：
    # 1) 逾期且重点  2) 重点  3) 逾期  4) 最近更新  5) 截止日期 / id
    today = date.today()
    is_overdue = and_(
        Task.due_date.is_not(None),
        Task.due_date < today,
        Task.status != "completed",
    )
    is_key = Task.priority.in_(tuple(KEY_TASK_PRIORITIES))
    overdue_key_rank = case((and_(is_overdue, is_key), 0), else_=1)
    key_rank = case((is_key, 0), else_=1)
    overdue_rank = case((is_overdue, 0), else_=1)
    result = await db.execute(
        stmt.order_by(
            overdue_key_rank,
            key_rank,
            overdue_rank,
            Task.updated_at.desc().nulls_last(),
            Task.due_date.nulls_last(),
            Task.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    tasks = list(result.scalars().unique().all())
    items = await _tasks_to_out(db, tasks, user.id)
    return TaskListOut(items=items, total=total, page=page, page_size=page_size)


async def list_department_open_tasks(db: AsyncSession, viewer: User) -> list[TaskOut]:
    """
    部门待办：全体在职成员的未完成待办汇总（不含查看者自己负责的，避免与个人待办重复）。
    无部门表时，范围=全部 is_active 用户。
    """
    if not can_view_department_todos(viewer):
        return []
    active_ids = select(User.id).where(User.is_active.is_(True))
    result = await db.execute(
        select(Task)
        .options(*TASK_LOAD_OPTS)
        .where(
            Task.status != "completed",
            Task.assignee_id.in_(active_ids),
            Task.assignee_id != viewer.id,
        )
        .order_by(Task.due_date.nulls_last(), Task.id.desc())
    )
    tasks = list(result.scalars().unique().all())
    return await _tasks_to_out(db, tasks, viewer.id)
