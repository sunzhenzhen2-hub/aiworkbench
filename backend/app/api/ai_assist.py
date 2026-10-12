"""统一 AI 辅助入口：项目/卡点/总览/方案检索（草稿确认后由业务 API 落库）。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.rbac.deps import get_user_permissions, require_password_updated, require_permission
from app.schemas import AiGuidanceSaveIn, AiNudgeIn, AiTextIn
from app.services import assist_ai, assist_analyze
from app.services.agent_native import refuse_legacy_ai
from app.services.project_service import list_projects

router = APIRouter(prefix="/ai", tags=["AI辅助"])


@router.get("/overview/brief")
async def ai_overview_brief(
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    refuse_legacy_ai()
    projects = await list_projects(db, user, get_user_permissions(user))
    out = await assist_ai.overview_brief(db, project_ids=[p.id for p in projects])
    return out


@router.get("/overview/brief/stream")
async def ai_overview_brief_stream(
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE：今天该看什么，边生成边推送。"""
    refuse_legacy_ai()
    projects = await list_projects(db, user, get_user_permissions(user))
    project_ids = [p.id for p in projects]

    async def event_gen() -> AsyncIterator[str]:
        async for item in assist_ai.overview_brief_stream(db, project_ids=project_ids):
            event = item.get("event") or "message"
            data = item.get("data") if isinstance(item.get("data"), dict) else {}
            payload = json.dumps(data, ensure_ascii=False)
            yield f"event: {event}\ndata: {payload}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/project/draft")
async def ai_project_draft(
    body: AiTextIn,
    user: User = Depends(require_permission("project.create")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    refuse_legacy_ai()
    out = await assist_ai.draft_project_from_text(db, body.text.strip())
    return out


@router.post("/blocker/draft")
async def ai_blocker_draft(
    body: AiTextIn,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> dict:
    refuse_legacy_ai()
    out = await assist_ai.draft_blocker_from_text(
        db, text=body.text.strip(), project_id=body.project_id
    )
    return out


@router.post("/solution/search")
async def ai_solution_search(
    body: AiTextIn,
    user: User = Depends(require_permission("solution.view")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    refuse_legacy_ai()
    out = await assist_ai.search_solutions_from_query(db, body.text.strip())
    return out


def _sse_response(event_gen: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        event_gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _pack_sse(items) -> AsyncIterator[str]:
    async for item in items:
        event = item.get("event") or "message"
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        payload = json.dumps(data, ensure_ascii=False, default=str)
        yield f"event: {event}\ndata: {payload}\n\n"


@router.get("/analyze/team")
async def ai_analyze_team(
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """管理员：团队本周进度/质量分析 + 催办清单。"""
    refuse_legacy_ai()
    return await assist_analyze.analyze_team(db, user, get_user_permissions(user))


@router.get("/analyze/team/stream")
async def ai_analyze_team_stream(
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE：先出统计/清单，再流式结论。"""
    refuse_legacy_ai()
    return _sse_response(
        _pack_sse(assist_analyze.analyze_team_stream(db, user, get_user_permissions(user)))
    )


@router.get("/analyze/actions")
async def ai_analyze_actions(
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """管理员：催办与指导动作清单。"""
    refuse_legacy_ai()
    return await assist_analyze.analyze_actions(db, user, get_user_permissions(user))


@router.get("/analyze/project/{project_id}")
async def ai_analyze_project(
    project_id: int,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """管理员：单项目串联分析。"""
    refuse_legacy_ai()
    out = await assist_analyze.analyze_project(
        db, user, get_user_permissions(user), project_id
    )
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="项目不存在")
    return out


@router.get("/analyze/project/{project_id}/stream")
async def ai_analyze_project_stream(
    project_id: int,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    refuse_legacy_ai()
    return _sse_response(
        _pack_sse(
            assist_analyze.analyze_project_stream(
                db, user, get_user_permissions(user), project_id
            )
        )
    )


@router.get("/analyze/person/{user_id}")
async def ai_analyze_person(
    user_id: int,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """管理员：单人进度质量分析 + 指导话术草稿。"""
    refuse_legacy_ai()
    out = await assist_analyze.analyze_person(
        db, user, get_user_permissions(user), user_id
    )
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="用户不存在")
    return out


@router.get("/analyze/person/{user_id}/stream")
async def ai_analyze_person_stream(
    user_id: int,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    refuse_legacy_ai()
    return _sse_response(
        _pack_sse(
            assist_analyze.analyze_person_stream(
                db, user, get_user_permissions(user), user_id
            )
        )
    )


@router.post("/analyze/guidance")
async def ai_analyze_save_guidance(
    body: AiGuidanceSaveIn,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """管理员：保存轻量指导备注（可通知本人）。"""
    period: date | None = body.period_start
    out = await assist_analyze.save_guidance(
        db,
        admin=user,
        target_user_id=body.target_user_id,
        content=body.content,
        action_items=body.action_items,
        project_id=body.project_id,
        period_start=period,
        source=body.source or "admin",
        notify=bool(body.notify),
    )
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="用户不存在")
    if out.get("error") == "empty":
        raise HTTPException(status_code=400, detail="指导内容不能为空")
    return out


@router.post("/analyze/nudge")
async def ai_analyze_nudge(
    body: AiNudgeIn,
    user: User = Depends(require_permission("assist.analyze")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """分析管控：对催办清单条目一键发送催办通知。"""
    out = await assist_analyze.nudge_action(
        db,
        actor=user,
        action_type=body.type,
        user_id=body.user_id,
        task_id=body.task_id,
        project_id=body.project_id,
        title=body.title or "",
        detail=body.detail or "",
    )
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail=out.get("detail") or "对象不存在")
    if out.get("error") == "bad_request":
        raise HTTPException(status_code=400, detail=out.get("detail") or "参数无效")
    return out
