"""方案库 API：上传、列表、详情、下载、编辑标签、重新整理、删除。"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import AuditLog, Solution, SolutionTag, User
from app.rbac.deps import get_user_permissions, require_permission
from app.schemas import (
    SolutionListOut,
    SolutionOut,
    SolutionTagOut,
    SolutionTagStat,
    SolutionUpdate,
)
from app.services.solution_ai_tag import process_solution
from app.services.solution_storage import (
    absolute_path,
    delete_stored,
    save_upload,
    upload_root,
)

router = APIRouter(prefix="/solutions", tags=["方案库"])


def _solution_out(sol: Solution) -> SolutionOut:
    uploader_name = ""
    if sol.uploader is not None:
        uploader_name = sol.uploader.name or sol.uploader.username or ""
    return SolutionOut(
        id=sol.id,
        title=sol.title or sol.original_filename,
        summary=sol.summary or "",
        original_filename=sol.original_filename,
        mime_type=sol.mime_type or "",
        file_size=sol.file_size or 0,
        uploader_id=sol.uploader_id,
        uploader_name=uploader_name,
        status=sol.status,
        error_message=sol.error_message or "",
        tags=[
            SolutionTagOut(id=t.id, name=t.name, source=t.source)
            for t in sorted(sol.tags, key=lambda x: x.name)
        ],
        created_at=sol.created_at,
        updated_at=sol.updated_at,
    )


async def _load_solution(db: AsyncSession, solution_id: int) -> Solution:
    result = await db.execute(
        select(Solution)
        .options(selectinload(Solution.tags), selectinload(Solution.uploader))
        .where(Solution.id == solution_id)
    )
    sol = result.scalar_one_or_none()
    if not sol:
        raise HTTPException(status_code=404, detail="方案不存在")
    return sol


def _can_edit(user: User, sol: Solution) -> bool:
    perms = get_user_permissions(user)
    return sol.uploader_id == user.id or "admin.user" in perms


def _can_delete(user: User, sol: Solution) -> bool:
    return _can_edit(user, sol)


@router.get("/tags", response_model=list[SolutionTagStat])
async def list_tag_stats(
    user: User = Depends(require_permission("solution.view")),
    db: AsyncSession = Depends(get_db),
) -> list[SolutionTagStat]:
    _ = user
    result = await db.execute(
        select(SolutionTag.name, func.count(SolutionTag.id))
        .group_by(SolutionTag.name)
        .order_by(func.count(SolutionTag.id).desc(), SolutionTag.name)
        .limit(100)
    )
    return [SolutionTagStat(name=row[0], count=row[1]) for row in result.all()]


@router.get("", response_model=SolutionListOut)
async def list_solutions(
    q: str | None = Query(None, description="搜索标题/摘要/文件名"),
    tag: str | None = Query(None, description="按标签精确筛选"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    user: User = Depends(require_permission("solution.view")),
    db: AsyncSession = Depends(get_db),
) -> SolutionListOut:
    _ = user
    stmt = select(Solution).options(
        selectinload(Solution.tags), selectinload(Solution.uploader)
    )
    count_stmt = select(func.count(Solution.id))

    if tag:
        tag_sub = select(SolutionTag.solution_id).where(SolutionTag.name == tag)
        stmt = stmt.where(Solution.id.in_(tag_sub))
        count_stmt = count_stmt.where(Solution.id.in_(tag_sub))

    if q and q.strip():
        like = f"%{q.strip()}%"
        cond = or_(
            Solution.title.ilike(like),
            Solution.summary.ilike(like),
            Solution.original_filename.ilike(like),
        )
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    total = (await db.execute(count_stmt)).scalar_one()
    result = await db.execute(
        stmt.order_by(Solution.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = [_solution_out(s) for s in result.scalars().all()]
    return SolutionListOut(items=items, total=total, page=page, page_size=page_size)


@router.post("/upload", response_model=SolutionOut)
async def upload_solution(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(require_permission("solution.upload")),
    db: AsyncSession = Depends(get_db),
) -> SolutionOut:
    upload_root()  # 确保目录存在
    original, stored, mime, size = await save_upload(file)
    stem = original.rsplit(".", 1)[0] if "." in original else original
    sol = Solution(
        title=stem[:256],
        summary="",
        original_filename=original,
        stored_path=stored,
        mime_type=mime,
        file_size=size,
        uploader_id=user.id,
        status="processing",
        error_message="",
    )
    db.add(sol)
    await db.flush()
    db.add(
        AuditLog(
            user_id=user.id,
            action="upload",
            resource="solution",
            resource_id=sol.id,
            detail=original,
        )
    )
    await db.commit()

    background_tasks.add_task(process_solution, sol.id)
    sol = await _load_solution(db, sol.id)
    return _solution_out(sol)


@router.get("/{solution_id}", response_model=SolutionOut)
async def get_solution(
    solution_id: int,
    user: User = Depends(require_permission("solution.view")),
    db: AsyncSession = Depends(get_db),
) -> SolutionOut:
    _ = user
    sol = await _load_solution(db, solution_id)
    return _solution_out(sol)


@router.get("/{solution_id}/download")
async def download_solution(
    solution_id: int,
    user: User = Depends(require_permission("solution.view")),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    _ = user
    sol = await _load_solution(db, solution_id)
    try:
        path = absolute_path(sol.stored_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    return FileResponse(
        path=str(path),
        filename=sol.original_filename,
        media_type=sol.mime_type or "application/octet-stream",
    )


@router.patch("/{solution_id}", response_model=SolutionOut)
async def update_solution(
    solution_id: int,
    body: SolutionUpdate,
    user: User = Depends(require_permission("solution.upload")),
    db: AsyncSession = Depends(get_db),
) -> SolutionOut:
    sol = await _load_solution(db, solution_id)
    if not _can_edit(user, sol):
        raise HTTPException(status_code=403, detail="只能编辑自己上传的方案")

    if body.title is not None:
        title = body.title.strip()[:256]
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        sol.title = title

    if body.tags is not None:
        # 全量替换为手动标签
        for t in list(sol.tags):
            await db.delete(t)
        await db.flush()
        seen: set[str] = set()
        for raw in body.tags:
            name = str(raw).strip()[:64]
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            db.add(SolutionTag(solution_id=sol.id, name=name, source="manual"))

    db.add(
        AuditLog(
            user_id=user.id,
            action="update",
            resource="solution",
            resource_id=sol.id,
        )
    )
    await db.commit()
    sol = await _load_solution(db, solution_id)
    return _solution_out(sol)


@router.post("/{solution_id}/reprocess", response_model=SolutionOut)
async def reprocess(
    solution_id: int,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_permission("solution.upload")),
    db: AsyncSession = Depends(get_db),
) -> SolutionOut:
    sol = await _load_solution(db, solution_id)
    if not _can_edit(user, sol):
        raise HTTPException(status_code=403, detail="只能重新整理自己上传的方案")
    sol.status = "processing"
    sol.error_message = ""
    await db.commit()
    background_tasks.add_task(process_solution, solution_id)
    sol = await _load_solution(db, solution_id)
    return _solution_out(sol)


@router.delete("/{solution_id}")
async def delete_solution(
    solution_id: int,
    user: User = Depends(require_permission("solution.upload")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    sol = await _load_solution(db, solution_id)
    if not _can_delete(user, sol):
        raise HTTPException(status_code=403, detail="只能删除自己上传的方案")
    stored = sol.stored_path
    db.add(
        AuditLog(
            user_id=user.id,
            action="delete",
            resource="solution",
            resource_id=sol.id,
            detail=sol.original_filename,
        )
    )
    await db.delete(sol)
    await db.commit()
    delete_stored(stored)
    return {"ok": True}
