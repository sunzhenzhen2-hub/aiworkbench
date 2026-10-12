"""数据分析 API（stats.view）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models import User
from app.rbac.deps import get_user_permissions, require_permission
from app.services import stats_service

router = APIRouter(prefix="/stats", tags=["数据分析"])


@router.get("/people")
async def stats_people(
    user: User = Depends(require_permission("stats.view")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """人员工作视图：近 4 周周报提交率、逾期任务、未关卡点。"""
    return await stats_service.people_stats(db, user, get_user_permissions(user))


@router.get("/projects")
async def stats_projects(
    user: User = Depends(require_permission("stats.view")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """项目数据分析：延期倾向、卡点类型分布、外部拖期排行。"""
    return await stats_service.projects_stats(db, user, get_user_permissions(user))
