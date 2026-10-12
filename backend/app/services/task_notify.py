"""任务交办相关推送：经统一管道（站内 + 钉钉 + 日志）。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task, User
from app.services import agent_native
from app.services.notify_pipeline import dispatch_notification

PRIORITY_LABEL = {
    "p0": "P0",
    "p1": "P1",
    "p2": "P2",
    "p3": "P3",
    "p4": "P4",
    "critical": "P0",
    "watch": "P1",
    "normal": "P3",
}


async def notify_users(
    db: AsyncSession,
    users: list[User],
    *,
    title: str,
    content: str,
    payload: dict | None = None,
    event_key: str | None = None,
    variables: dict | None = None,
    ref_type: str = "",
    ref_id: str | int = "",
) -> dict[str, bool]:
    """兼容旧调用：无 event_key 时用 payload.type，否则走管道。"""
    key = event_key or (payload or {}).get("type") or "generic"
    return await dispatch_notification(
        db,
        event_key=str(key),
        users=users,
        variables=variables or {},
        payload=payload,
        ref_type=ref_type,
        ref_id=ref_id,
        fallback_title=title,
        fallback_body=content,
        respect_quiet=False,  # 即时业务通知不受安静时段限制
    )


async def notify_critical_task(
    db: AsyncSession, task: Task, *, actor_name: str
) -> None:
    """P0 任务：提醒责任人。"""
    from app.services.task_service import should_notify_priority

    if not should_notify_priority(getattr(task, "priority", None)):
        return
    assignee: User | None = getattr(task, "assignee", None)
    if assignee is None and task.assignee_id:
        assignee = await db.get(User, task.assignee_id)
    if not assignee:
        return

    label = PRIORITY_LABEL.get(task.priority or "p0", "P0")
    await notify_users(
        db,
        [assignee],
        title="收到 P0 重点任务",
        content=f"{actor_name} 将任务「{task.title}」设为 {label}，请优先跟进。",
        payload={
            "type": "task_priority_p0",
            "task_id": task.id,
            "project_id": task.project_id,
            "priority": task.priority or "p0",
        },
        event_key="task_priority_p0",
        variables={
            "actor_name": actor_name,
            "task_title": task.title,
            "priority_label": label,
        },
        ref_type="task",
        ref_id=task.id,
    )


async def notify_project_role(
    db: AsyncSession,
    person: User,
    *,
    role_label: str,
    project_name: str,
    project_id: int,
    actor_name: str,
) -> bool:
    result = await notify_users(
        db,
        [person],
        title=f"你被指定为{role_label}",
        content=f"{actor_name} 新建了项目「{project_name}」，你是{role_label}。请打开工作台查看。",
        payload={
            "type": "project_role_assigned",
            "project_id": project_id,
            "role": role_label,
        },
        event_key="project_role_assigned",
        variables={
            "actor_name": actor_name,
            "project_name": project_name,
            "role_label": role_label,
        },
        ref_type="project",
        ref_id=project_id,
    )
    return bool(result.get("dingtalk_notified"))


async def notify_task_assigned(db: AsyncSession, task: Task, *, actor_name: str) -> bool:
    assignee = getattr(task, "assignee", None) or await db.get(User, task.assignee_id)
    if not assignee:
        return False
    result = await notify_users(
        db,
        [assignee],
        title="收到交办任务，请接收",
        content=f"{actor_name} 交办了「{task.title}」，请打开工作台点「接收」后再更新进度。",
        payload={"type": "task_assigned", "task_id": task.id, "project_id": task.project_id},
        event_key="task_assigned",
        variables={"actor_name": actor_name, "task_title": task.title},
        ref_type="task",
        ref_id=task.id,
    )
    try:
        await agent_native.nudge(db, assignee, event="assigned", task_id=task.id)
    except Exception:
        pass
    return bool(result.get("dingtalk_notified"))


async def notify_task_accepted(db: AsyncSession, task: Task, *, actor_name: str) -> None:
    if not task.created_by_id:
        return
    creator = getattr(task, "created_by", None) or await db.get(User, task.created_by_id)
    if not creator:
        return
    await notify_users(
        db,
        [creator],
        title="交办任务已接收",
        content=f"{actor_name} 已接收「{task.title}」。",
        payload={"type": "task_accepted", "task_id": task.id, "project_id": task.project_id},
        event_key="task_accepted",
        variables={"actor_name": actor_name, "task_title": task.title},
        ref_type="task",
        ref_id=task.id,
    )


async def notify_task_remind(db: AsyncSession, task: Task, *, actor_name: str) -> None:
    assignee = getattr(task, "assignee", None) or await db.get(User, task.assignee_id)
    if not assignee:
        return
    await notify_users(
        db,
        [assignee],
        title="请尽快接收交办任务",
        content=f"{actor_name} 催办：请尽快接收「{task.title}」。",
        payload={"type": "task_remind", "task_id": task.id, "project_id": task.project_id},
        event_key="task_remind",
        variables={"actor_name": actor_name, "task_title": task.title},
        ref_type="task",
        ref_id=task.id,
    )


async def notify_task_completed(
    db: AsyncSession, task: Task, *, actor_name: str
) -> bool:
    if (task.assignment_type or "self") != "delegated":
        return False
    if not task.created_by_id or task.created_by_id == task.assignee_id:
        return False
    creator = getattr(task, "created_by", None) or await db.get(User, task.created_by_id)
    if not creator:
        return False
    summary = (task.result_text or "").strip()
    if len(summary) > 80:
        summary = summary[:80] + "…"
    result_summary = f" 结果：{summary}" if summary else ""
    content = f"{actor_name} 已完成「{task.title}」。{result_summary}".strip()
    result = await notify_users(
        db,
        [creator],
        title="交办任务已完成",
        content=content,
        payload={
            "type": "task_completed",
            "task_id": task.id,
            "project_id": task.project_id,
        },
        event_key="task_completed",
        variables={
            "actor_name": actor_name,
            "task_title": task.title,
            "result_summary": result_summary,
        },
        ref_type="task",
        ref_id=task.id,
    )
    return bool(result.get("dingtalk_notified"))
