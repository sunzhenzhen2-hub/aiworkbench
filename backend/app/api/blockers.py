from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models import Blocker, BlockerHistory, User
from app.rbac.deps import require_password_updated, require_permission
from app.schemas import BlockerCreate, BlockerOut, BlockerUpdate
from app.services.project_service import _blocker_out

router = APIRouter(prefix="/blockers", tags=["卡点"])


@router.get("/project/{project_id}", response_model=list[BlockerOut])
async def list_blockers(
    project_id: int,
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[BlockerOut]:
    result = await db.execute(
        select(Blocker)
        .options(selectinload(Blocker.assignee))
        .where(Blocker.project_id == project_id)
        .order_by(Blocker.updated_at.desc())
    )
    return [_blocker_out(b) for b in result.scalars().all()]


@router.post("/project/{project_id}", response_model=BlockerOut)
async def create_blocker(
    project_id: int,
    body: BlockerCreate,
    user: User = Depends(require_permission("blocker.edit")),
    db: AsyncSession = Depends(get_db),
) -> BlockerOut:
    blocker = Blocker(
        project_id=project_id,
        assignee_id=body.assignee_id or user.id,
        description=body.description,
        responsible_party=body.responsible_party,
        party_type=body.party_type,
        found_date=body.found_date or date.today(),
        actions_taken=body.actions_taken,
        synced_to=body.synced_to,
        response=body.response,
        impact=body.impact,
        status=body.status,
        next_action=body.next_action,
        next_follow_up=body.next_follow_up,
    )
    db.add(blocker)
    await db.commit()
    result = await db.execute(
        select(Blocker).options(selectinload(Blocker.assignee)).where(Blocker.id == blocker.id)
    )
    return _blocker_out(result.scalar_one())


@router.patch("/{blocker_id}", response_model=BlockerOut)
@router.post("/{blocker_id}", response_model=BlockerOut)
async def update_blocker(
    blocker_id: int,
    body: BlockerUpdate,
    user: User = Depends(require_permission("blocker.edit")),
    db: AsyncSession = Depends(get_db),
) -> BlockerOut:
    result = await db.execute(
        select(Blocker).options(selectinload(Blocker.assignee)).where(Blocker.id == blocker_id)
    )
    blocker = result.scalar_one_or_none()
    if not blocker:
        raise HTTPException(status_code=404, detail="卡点不存在")
    data = body.model_dump(exclude_unset=True)
    if "status" in data and data["status"] != blocker.status:
        db.add(
            BlockerHistory(
                blocker_id=blocker.id,
                changed_by_id=user.id,
                old_status=blocker.status,
                new_status=data["status"],
            )
        )
    for field, value in data.items():
        setattr(blocker, field, value)
    await db.commit()
    result = await db.execute(
        select(Blocker).options(selectinload(Blocker.assignee)).where(Blocker.id == blocker_id)
    )
    return _blocker_out(result.scalar_one())


@router.get("/my", response_model=list[BlockerOut])
async def my_blockers(
    user: User = Depends(require_password_updated),
    db: AsyncSession = Depends(get_db),
) -> list[BlockerOut]:
    result = await db.execute(
        select(Blocker)
        .options(selectinload(Blocker.assignee))
        .where(Blocker.assignee_id == user.id, Blocker.status != "resolved")
        .order_by(Blocker.next_follow_up)
    )
    return [_blocker_out(b) for b in result.scalars().all()]
