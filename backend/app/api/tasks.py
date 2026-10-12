"""任务交办 API：自建 / 管理者交办 / 辅助人 / 流水账。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import verify_password
from app.models import (
    AuditLog,
    ProgressReportItem,
    Project,
    Task,
    TaskAssistant,
    TaskAttachment,
    TaskEvent,
    TaskReview,
    User,
)
from app.rbac.deps import get_user_permissions, is_project_manager, require_password_updated, require_permission
from app.schemas import (
    TaskAiDraftIn,
    TaskAiPolishIn,
    TaskAssistantAdd,
    TaskAttachmentOut,
    TaskCheckSimilarIn,
    TaskCheckSimilarOut,
    TaskChecklistUpdate,
    TaskCreate,
    TaskDeleteIn,
    TaskGuessProjectIn,
    TaskGuessProjectOut,
    TaskListOut,
    TaskOut,
    TaskOwnerUpdate,
    TaskPriorityUpdate,
    TaskReviewOut,
    TaskSimilarItem,
    TaskUpdate,
)
from app.services import task_service as svc
from app.services.project_service import guess_project_for_user
from app.services.report_task_sync import (
    append_task_to_week_draft,
    sync_task_completion_to_week_draft,
)
from app.services.task_ai import breakdown_task, draft_task_from_text, polish_task_field
from app.services.agent_native import refuse_legacy_ai
from app.services.task_ai_review import (
    ensure_task_review,
    try_mark_upgrade_scheduled,
    upgrade_task_review_llm,
)
from app.services.task_dedupe import HARD_MESSAGE, VAGUE_MESSAGE, check_task_duplicate
from app.services.task_attachment_storage import (
    absolute_path as attachment_absolute_path,
    delete_stored as attachment_delete_stored,
    save_upload as attachment_save_upload,
    upload_root as attachment_upload_root,
)
from app.services.task_notify import (
    notify_critical_task,
    notify_task_accepted,
    notify_task_assigned,
    notify_task_completed,
    notify_task_remind,
)

router = APIRouter(tags=["任务交办"])


async def _task_response(db: AsyncSession, task_id: int, viewer_id: int) -> TaskOut:
    db.expunge_all()
    task = await svc.load_task(db, task_id, detail=True)
    assert task is not None
    name = (
        await db.execute(select(Project.name).where(Project.id == task.project_id))
    ).scalar_one_or_none()
    return svc.task_to_out(
        task, project_name=name, viewer_id=viewer_id, include_events=True
    )


@router.post("/tasks", response_model=TaskOut)
async def create_task(
    body: TaskCreate,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    perms = get_user_permissions(user)
    if body.assignment_type not in svc.ASSIGNMENT_TYPES:
        raise HTTPException(status_code=400, detail="无效的任务类型")

    project = (
        await db.execute(select(Project).where(Project.id == body.project_id))
    ).scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not await svc.can_view_project(db, user, body.project_id):
        raise HTTPException(status_code=403, detail="无权在该项目创建任务")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")

    priority = svc.normalize_priority(body.priority)
    assistants = list(dict.fromkeys(body.assistant_ids or []))

    if body.assignment_type == "self":
        if "task.create.self" not in perms:
            raise HTTPException(status_code=403, detail="无自建任务权限")
        member_role = await svc.get_member_role(db, body.project_id, user.id)
        if member_role is None and "project.view.all" not in perms:
            raise HTTPException(status_code=403, detail="须先加入项目才能自建任务")
        # 全局管理者自建：自动加入可见性即可，负责人仍是自己
        assignee_id = user.id
        if user.id in assistants:
            raise HTTPException(status_code=400, detail="负责人不能同时是辅助人")
    else:
        if not await svc.can_delegate_on_project(db, user, body.project_id):
            raise HTTPException(status_code=403, detail="仅部门负责人或项目主负责人可交办")
        if not body.assignee_id:
            raise HTTPException(status_code=400, detail="交办须指定负责人")
        assignee_id = body.assignee_id
        await svc.ensure_users_are_members(
            db, body.project_id, [assignee_id], label="负责人"
        )
        if assignee_id in assistants:
            raise HTTPException(status_code=400, detail="负责人不能同时是辅助人")

    await svc.ensure_users_are_members(
        db, body.project_id, assistants, label="辅助人"
    )

    dup = await check_task_duplicate(
        db,
        assignee_id=assignee_id,
        title=title,
        goal=(body.goal or "").strip(),
        project_id=body.project_id,
    )
    if dup["vague"]:
        raise HTTPException(status_code=400, detail=dup["message"] or VAGUE_MESSAGE)
    if dup["level"] == "hard" and not body.force_create:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "similar_task",
                "message": dup["message"] or HARD_MESSAGE,
                "similar": dup["similar"],
            },
        )

    phase = await svc.resolve_phase_for_project(db, body.project_id, body.phase_id)

    is_delegated = body.assignment_type == "delegated"
    now = datetime.now(timezone.utc)
    task = Task(
        project_id=body.project_id,
        phase_id=phase.id if phase else None,
        assignee_id=assignee_id,
        created_by_id=user.id,
        assignment_type=body.assignment_type,
        title=title,
        goal=(body.goal or "").strip(),
        due_date=body.due_date,
        status="not_started",
        priority=priority,
        acceptance_status="pending" if is_delegated else "accepted",
        accept_by=body.accept_by if is_delegated else None,
        accepted_at=None if is_delegated else now,
        checklist=[],
        result_text="",
        source="manual",
    )
    db.add(task)
    await db.flush()

    draft_item = await append_task_to_week_draft(db, user=user, task=task)
    synced_to_week_draft = draft_item is not None

    for uid in assistants:
        db.add(TaskAssistant(task_id=task.id, user_id=uid))

    svc.add_event(
        db,
        task,
        user.id,
        "created",
        {
            "assignment_type": body.assignment_type,
            "assignee_id": assignee_id,
            "assistant_ids": assistants,
            "title": title,
            "priority": priority,
            "acceptance_status": task.acceptance_status,
        },
    )
    if priority != "p3":
        svc.add_event(
            db,
            task,
            user.id,
            "priority_set",
            {"priority": priority},
        )
    for uid in assistants:
        svc.add_event(
            db,
            task,
            user.id,
            "assistant_added",
            {"user_id": uid},
        )

    task_id = task.id
    await db.commit()
    dingtalk_notified = False
    loaded = await svc.load_task(db, task_id, detail=True)
    if loaded:
        if is_delegated:
            dingtalk_notified = await notify_task_assigned(db, loaded, actor_name=user.name)
        if svc.should_notify_priority(priority):
            await notify_critical_task(db, loaded, actor_name=user.name)
    out = await _task_response(db, task_id, user.id)
    return out.model_copy(
        update={
            "synced_to_week_draft": synced_to_week_draft,
            "dingtalk_notified": dingtalk_notified,
        }
    )


@router.get("/projects/{project_id}/tasks", response_model=list[TaskOut])
async def list_project_tasks(
    project_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[TaskOut]:
    if not await svc.can_view_project(db, user, project_id):
        raise HTTPException(status_code=403, detail="无权查看该项目任务")
    return await svc.list_project_tasks(db, project_id, user)


@router.get("/tasks/related", response_model=TaskListOut)
async def list_related_tasks(
    status: str = Query(default="all", description="open 未完成 / completed 已完成 / all 全部"),
    search: str | None = Query(default=None, description="按任务标题模糊搜索"),
    assignee_id: int | None = Query(default=None, description="按负责人筛选"),
    priority: str | None = Query(default=None, description="按级别筛选：p0–p4"),
    page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(default=10, ge=1, le=100, description="每页条数，默认 10"),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskListOut:
    """任务管理：管理员和部门负责人看全部；其他人只看自己创建、负责或辅助的任务。"""
    return await svc.list_related_tasks(
        db,
        user,
        status,
        search=search,
        assignee_id=assignee_id,
        priority=priority,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_view_project(db, user, task.project_id):
        # 负责人/辅助人即使不在成员表也应能看（极端情况）
        if svc.my_role_on_task(task, user.id) == "none":
            raise HTTPException(status_code=403, detail="无权查看该任务")
    name = (
        await db.execute(select(Project.name).where(Project.id == task.project_id))
    ).scalar_one_or_none()
    return svc.task_to_out(
        task, project_name=name, viewer_id=user.id, include_events=True
    )


@router.get("/tasks/{task_id}/review", response_model=TaskReviewOut)
async def get_task_review(
    task_id: int,
    background_tasks: BackgroundTasks,
    force: bool = False,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskReviewOut:
    """任务评价：执行人/辅助人本人可读；有全量项目权限的管理者也可读。

    默认先返回规则评价（不堵 LLM）；若仍是规则结果，后台尝试升级为 AI。
    force=true 时同步走 LLM。
    """
    task = await svc.load_task(db, task_id, detail=False)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    perms = get_user_permissions(user)
    role = svc.my_role_on_task(task, user.id)
    if role == "none" and "project.view.all" not in perms:
        raise HTTPException(status_code=403, detail="无权查看该任务评价")
    review = await ensure_task_review(db, task_id, force=force)
    if not review:
        raise HTTPException(status_code=404, detail="评价不可用")
    # 首开已写规则评价时，后台静默升级；同一任务去重，避免刷新连发
    if not force and (review.source or "rule") != "llm":
        if try_mark_upgrade_scheduled(task_id):
            background_tasks.add_task(upgrade_task_review_llm, task_id)
    return TaskReviewOut(
        id=review.id,
        task_id=review.task_id,
        summary=review.summary or "",
        strengths=list(review.strengths or []),
        risks=list(review.risks or []),
        suggestions=list(review.suggestions or []),
        source=review.source or "rule",
        model_name=review.model_name or "",
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


@router.post("/tasks/guess-project", response_model=TaskGuessProjectOut)
async def guess_task_project(
    body: TaskGuessProjectIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskGuessProjectOut:
    """写任务时：标题像某个项目，或和同事近期任务上下文相近，就建议归到那个项目。"""
    title = (body.title or "").strip()
    goal = (body.goal or "").strip()
    if len(title) < 2 and len(goal) < 2:
        return TaskGuessProjectOut()
    pid, conf, name, reason = await guess_project_for_user(
        db, user.id, title, goal, member_only=True
    )
    return TaskGuessProjectOut(
        project_id=pid,
        project_name=name,
        confidence=conf,
        reason=reason,
    )


@router.post("/tasks/check-similar", response_model=TaskCheckSimilarOut)
async def check_similar_task(
    body: TaskCheckSimilarIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskCheckSimilarOut:
    """写任务前：空泛续推标题 / 同人未完成相似任务检测。"""
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    if body.assignment_type == "delegated" and body.assignee_id:
        assignee_id = body.assignee_id
    else:
        assignee_id = user.id
    dup = await check_task_duplicate(
        db,
        assignee_id=assignee_id,
        title=title,
        goal=(body.goal or "").strip(),
        project_id=body.project_id,
    )
    return TaskCheckSimilarOut(
        vague=bool(dup["vague"]),
        similar=[TaskSimilarItem(**s) for s in dup["similar"]],
        level=dup["level"],
        message=dup["message"] or "",
        continuation_warning=dup.get("continuation_warning") or "",
    )


@router.post("/tasks/ai/draft")
async def ai_task_draft(
    body: TaskAiDraftIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    refuse_legacy_ai()
    out = await draft_task_from_text(db, user=user, text=body.text.strip())
    return out


@router.post("/tasks/{task_id}/accept", response_model=TaskOut)
async def accept_task(
    task_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="仅责任人可接收")
    if (task.acceptance_status or "accepted") == "accepted":
        return await _task_response(db, task_id, user.id)
    task.acceptance_status = "accepted"
    task.accepted_at = datetime.now(timezone.utc)
    svc.add_event(db, task, user.id, "accepted", {})
    await db.commit()
    loaded = await svc.load_task(db, task_id, detail=True)
    if loaded:
        await notify_task_accepted(db, loaded, actor_name=user.name)
    return await _task_response(db, task_id, user.id)


@router.post("/tasks/{task_id}/remind", response_model=TaskOut)
async def remind_task(
    task_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    can = task.created_by_id == user.id or await svc.can_delegate_on_project(
        db, user, task.project_id
    )
    if not can:
        raise HTTPException(status_code=403, detail="无权催办")
    if (task.acceptance_status or "accepted") != "pending":
        raise HTTPException(status_code=400, detail="任务已接收，无需催办")
    svc.add_event(db, task, user.id, "reminded", {})
    await db.commit()
    loaded = await svc.load_task(db, task_id, detail=True)
    if loaded:
        await notify_task_remind(db, loaded, actor_name=user.name)
    return await _task_response(db, task_id, user.id)


@router.post("/tasks/{task_id}/ai/breakdown")
async def ai_task_breakdown(
    task_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_edit_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权拆解该任务")
    svc.require_accepted(task)
    refuse_legacy_ai()
    return await breakdown_task(db, task)


@router.post("/tasks/{task_id}/ai/polish")
async def ai_task_polish(
    task_id: int,
    body: TaskAiPolishIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """润色目标或结果说明；只返回建议文本，不自动保存。"""
    refuse_legacy_ai()
    field = (body.field or "").strip()
    if field not in {"goal", "result_text"}:
        raise HTTPException(status_code=400, detail="field 须为 goal 或 result_text")
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_edit_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权润色该任务")
    if field == "result_text":
        svc.require_accepted(task)
    try:
        return await polish_task_field(
            db, task=task, field=field, text=body.text
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/tasks/{task_id}/checklist", response_model=TaskOut)
async def update_checklist(
    task_id: int,
    body: TaskChecklistUpdate,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_edit_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权更新")
    svc.require_accepted(task)
    task.checklist = body.checklist or []
    svc.add_event(db, task, user.id, "checklist_updated", {"count": len(task.checklist)})
    await db.commit()
    return await _task_response(db, task_id, user.id)


@router.patch("/tasks/{task_id}/priority", response_model=TaskOut)
async def update_task_priority(
    task_id: int,
    body: TaskPriorityUpdate,
    user: User = Depends(require_permission("task.level.change")),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_view_project(db, user, task.project_id):
        raise HTTPException(status_code=403, detail="无权调整该任务级别")

    new_priority = svc.normalize_priority(body.priority)
    old = task.priority or "p3"
    if new_priority == old:
        return await _task_response(db, task.id, user.id)

    task.priority = new_priority
    svc.add_event(
        db,
        task,
        user.id,
        "priority_changed",
        {"old": old, "priority": new_priority},
    )
    await db.commit()
    if svc.should_notify_priority(new_priority) and not svc.should_notify_priority(old):
        loaded = await svc.load_task(db, task_id, detail=True)
        if loaded:
            await notify_critical_task(db, loaded, actor_name=user.name)
    return await _task_response(db, task_id, user.id)


@router.patch("/tasks/{task_id}", response_model=TaskOut)
@router.post("/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_edit_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权更新该任务")

    touching_progress = (
        body.status is not None
        or body.result_text is not None
        or body.checklist is not None
    )
    if touching_progress:
        svc.require_accepted(task)

    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        if title != task.title:
            svc.add_event(
                db, task, user.id, "result_updated", {"field": "title", "title": title}
            )
            task.title = title

    if body.goal is not None:
        goal = body.goal.strip()
        if goal != (getattr(task, "goal", None) or ""):
            svc.add_event(
                db, task, user.id, "result_updated", {"field": "goal", "goal": goal}
            )
            task.goal = goal

    if body.clear_due_date:
        if task.due_date is not None:
            svc.add_event(
                db,
                task,
                user.id,
                "result_updated",
                {"field": "due_date", "due_date": None},
            )
            task.due_date = None
    elif body.due_date is not None and body.due_date != task.due_date:
        svc.add_event(
            db,
            task,
            user.id,
            "result_updated",
            {"field": "due_date", "due_date": str(body.due_date)},
        )
        task.due_date = body.due_date

    if body.clear_phase_id:
        if task.phase_id is not None:
            svc.add_event(
                db,
                task,
                user.id,
                "result_updated",
                {"field": "phase_id", "phase_id": None},
            )
            task.phase_id = None
    elif body.phase_id is not None and body.phase_id != task.phase_id:
        phase = await svc.resolve_phase_for_project(db, task.project_id, body.phase_id)
        svc.add_event(
            db,
            task,
            user.id,
            "result_updated",
            {"field": "phase_id", "phase_id": phase.id if phase else None},
        )
        task.phase_id = phase.id if phase else None

    becoming_completed = False
    if body.status is not None:
        if body.status not in svc.TASK_STATUSES:
            raise HTTPException(status_code=400, detail="无效的任务状态")
        if body.status == "completed":
            result = (body.result_text if body.result_text is not None else task.result_text) or ""
            if not result.strip():
                raise HTTPException(status_code=400, detail="完成任务须填写结果说明")
        if body.status != task.status:
            becoming_completed = body.status == "completed"
            svc.add_event(
                db,
                task,
                user.id,
                "status_changed",
                {"old_status": task.status, "new_status": body.status},
            )
            task.status = body.status

    if body.result_text is not None and body.result_text != (task.result_text or ""):
        svc.add_event(
            db,
            task,
            user.id,
            "result_updated",
            {"field": "result_text", "result_text": body.result_text},
        )
        task.result_text = body.result_text

    if body.checklist is not None:
        task.checklist = body.checklist
        svc.add_event(
            db, task, user.id, "checklist_updated", {"count": len(body.checklist)}
        )

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
    out = await _task_response(db, task_id, user.id)
    if becoming_completed:
        return out.model_copy(
            update={
                "synced_to_week_draft": synced_to_week_draft,
                "dingtalk_notified": dingtalk_notified,
            }
        )
    return out


@router.patch("/tasks/{task_id}/owner", response_model=TaskOut)
async def change_owner(
    task_id: int,
    body: TaskOwnerUpdate,
    user: User = Depends(require_permission("task.assign")),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_manage_owner(db, user, task):
        raise HTTPException(status_code=403, detail="无权更换负责人")
    await svc.ensure_users_are_members(
        db, task.project_id, [body.assignee_id], label="负责人"
    )
    assist_ids = {a.user_id for a in task.assistants}
    if body.assignee_id in assist_ids:
        raise HTTPException(status_code=400, detail="请先移除该辅助人再设为负责人")
    if body.assignee_id == task.assignee_id:
        name = (
            await db.execute(select(Project.name).where(Project.id == task.project_id))
        ).scalar_one_or_none()
        return svc.task_to_out(
            task, project_name=name, viewer_id=user.id, include_events=True
        )

    old_id = task.assignee_id
    task.assignee_id = body.assignee_id
    if (task.assignment_type or "self") == "delegated":
        task.acceptance_status = "pending"
        task.accepted_at = None
        if body.accept_by is not None:
            task.accept_by = body.accept_by
    svc.add_event(
        db,
        task,
        user.id,
        "owner_changed",
        {
            "old_assignee_id": old_id,
            "new_assignee_id": body.assignee_id,
            "acceptance_reset": True,
        },
    )
    await db.commit()
    loaded = await svc.load_task(db, task_id, detail=True)
    if loaded and (loaded.acceptance_status or "") == "pending":
        await notify_task_assigned(db, loaded, actor_name=user.name)
    return await _task_response(db, task_id, user.id)


@router.post("/tasks/{task_id}/assistants", response_model=TaskOut)
async def add_assistant(
    task_id: int,
    body: TaskAssistantAdd,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_manage_assistants(db, user, task):
        raise HTTPException(status_code=403, detail="仅负责人或管理者可添加辅助人")
    if body.user_id == task.assignee_id:
        raise HTTPException(status_code=400, detail="负责人不能同时是辅助人")
    await svc.ensure_users_are_members(
        db, task.project_id, [body.user_id], label="辅助人"
    )
    existing = {a.user_id for a in task.assistants}
    if body.user_id not in existing:
        db.add(TaskAssistant(task_id=task.id, user_id=body.user_id))
        svc.add_event(
            db, task, user.id, "assistant_added", {"user_id": body.user_id}
        )
        await db.commit()
    return await _task_response(db, task_id, user.id)


@router.delete("/tasks/{task_id}/assistants/{user_id}", response_model=TaskOut)
async def remove_assistant(
    task_id: int,
    user_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await svc.can_manage_assistants(db, user, task):
        raise HTTPException(status_code=403, detail="仅负责人或管理者可移除辅助人")
    target = next((a for a in task.assistants if a.user_id == user_id), None)
    if target:
        await db.delete(target)
        svc.add_event(
            db, task, user.id, "assistant_removed", {"user_id": user_id}
        )
        await db.commit()
    return await _task_response(db, task_id, user.id)


def _attachment_out(row: TaskAttachment) -> TaskAttachmentOut:
    uploader_name = ""
    if row.uploader is not None:
        uploader_name = row.uploader.name or row.uploader.username or ""
    return TaskAttachmentOut(
        id=row.id,
        task_id=row.task_id,
        original_filename=row.original_filename,
        mime_type=row.mime_type or "",
        file_size=row.file_size or 0,
        uploader_id=row.uploader_id,
        uploader_name=uploader_name,
        created_at=row.created_at,
    )


async def _can_view_task(db: AsyncSession, user: User, task: Task) -> bool:
    if await svc.can_view_project(db, user, task.project_id):
        return True
    return svc.my_role_on_task(task, user.id) != "none"


async def _can_upload_attachment(db: AsyncSession, user: User, task: Task) -> bool:
    if await svc.can_edit_task(db, user, task):
        return True
    return await svc.can_delegate_on_project(db, user, task.project_id)


@router.get("/tasks/{task_id}/attachments", response_model=list[TaskAttachmentOut])
async def list_attachments(
    task_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[TaskAttachmentOut]:
    task = await svc.load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await _can_view_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权查看该任务附件")
    result = await db.execute(
        select(TaskAttachment)
        .where(TaskAttachment.task_id == task_id)
        .order_by(TaskAttachment.id.desc())
    )
    rows = result.scalars().all()
    out: list[TaskAttachmentOut] = []
    for row in rows:
        uploader = await db.get(User, row.uploader_id)
        uploader_name = ""
        if uploader is not None:
            uploader_name = uploader.name or uploader.username or ""
        out.append(
            TaskAttachmentOut(
                id=row.id,
                task_id=row.task_id,
                original_filename=row.original_filename,
                mime_type=row.mime_type or "",
                file_size=row.file_size or 0,
                uploader_id=row.uploader_id,
                uploader_name=uploader_name,
                created_at=row.created_at,
            )
        )
    return out


@router.post("/tasks/{task_id}/attachments", response_model=TaskAttachmentOut)
async def upload_attachment(
    task_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> TaskAttachmentOut:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await _can_upload_attachment(db, user, task):
        raise HTTPException(status_code=403, detail="无权上传该任务附件")
    svc.require_accepted(task)
    attachment_upload_root()
    original, stored, mime, size = await attachment_save_upload(file)
    row = TaskAttachment(
        task_id=task.id,
        uploader_id=user.id,
        original_filename=original,
        stored_path=stored,
        mime_type=mime,
        file_size=size,
    )
    db.add(row)
    svc.add_event(
        db,
        task,
        user.id,
        "attachment_added",
        {"filename": original, "size": size},
    )
    await db.commit()
    await db.refresh(row)
    row.uploader = user
    return _attachment_out(row)


@router.get("/tasks/{task_id}/attachments/{attachment_id}/download")
async def download_attachment(
    task_id: int,
    attachment_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    task = await svc.load_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if not await _can_view_task(db, user, task):
        raise HTTPException(status_code=403, detail="无权下载该任务附件")
    row = await db.get(TaskAttachment, attachment_id)
    if not row or row.task_id != task_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    try:
        path = attachment_absolute_path(row.stored_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="附件文件已丢失") from None
    return FileResponse(
        path,
        filename=row.original_filename,
        media_type=row.mime_type or "application/octet-stream",
    )


@router.delete("/tasks/{task_id}/attachments/{attachment_id}")
async def delete_attachment(
    task_id: int,
    attachment_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    row = await db.get(TaskAttachment, attachment_id)
    if not row or row.task_id != task_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    can_delete = row.uploader_id == user.id or await svc.can_delegate_on_project(
        db, user, task.project_id
    )
    if not can_delete:
        raise HTTPException(status_code=403, detail="无权删除该附件")
    stored = row.stored_path
    await db.delete(row)
    svc.add_event(
        db,
        task,
        user.id,
        "attachment_removed",
        {"filename": row.original_filename, "attachment_id": attachment_id},
    )
    await db.commit()
    attachment_delete_stored(stored)
    return {"ok": True}


@router.delete("/tasks/{task_id}")
async def delete_task(
    task_id: int,
    body: TaskDeleteIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """创建人或管理员（admin/dept_head）可删。须校验登录密码。先写审计日志，再清关联。"""
    task = await svc.load_task(db, task_id, detail=True)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.created_by_id != user.id and not is_project_manager(user):
        raise HTTPException(status_code=403, detail="仅创建人或管理员可删除该任务")
    try:
        password_ok = verify_password(body.password, user.hashed_password)
    except ValueError:
        password_ok = False
    if not password_ok:
        raise HTTPException(status_code=400, detail="密码不正确")

    project_name = (
        await db.execute(select(Project.name).where(Project.id == task.project_id))
    ).scalar_one_or_none() or ""
    assignee_name = ""
    if task.assignee is not None:
        assignee_name = task.assignee.name or task.assignee.username or ""
    kind = "交办" if (task.assignment_type or "self") == "delegated" else "自建"
    title = task.title or ""
    db.add(
        AuditLog(
            user_id=user.id,
            action="delete",
            resource="task",
            resource_id=task.id,
            detail=f"标题={title}；类型={kind}；项目={project_name}；负责人={assignee_name}",
        )
    )

    attach_rows = (
        await db.execute(
            select(TaskAttachment.stored_path).where(TaskAttachment.task_id == task_id)
        )
    ).scalars().all()
    stored_paths = list(attach_rows)

    await db.execute(
        update(ProgressReportItem)
        .where(ProgressReportItem.linked_task_id == task_id)
        .values(linked_task_id=None)
    )
    await db.execute(delete(TaskEvent).where(TaskEvent.task_id == task_id))
    await db.execute(delete(TaskAssistant).where(TaskAssistant.task_id == task_id))
    await db.execute(delete(TaskReview).where(TaskReview.task_id == task_id))
    await db.execute(delete(TaskAttachment).where(TaskAttachment.task_id == task_id))
    # 已加载的任务和流水还在会话里，先移出，避免和批量删除打架
    for ev in list(task.events or []):
        db.expunge(ev)
    for row in list(task.assistants or []):
        db.expunge(row)
    db.expunge(task)
    await db.execute(delete(Task).where(Task.id == task_id))
    await db.commit()
    for path in stored_paths:
        attachment_delete_stored(path)
    return {"ok": True}
