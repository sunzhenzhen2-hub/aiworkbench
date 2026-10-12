"""142：钉钉确认身份后，用工号开账号或自动绑定。"""
from __future__ import annotations

import re

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AuditLog, Role, User, UserRole

_SAFE_JOB = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

RESERVED_USERNAMES = {"admin"}
SYSTEM_USERNAMES = {"admin"}
NO_JOB_MSG = "钉钉通讯录没有工号，请联系管理员"
BAD_JOB_MSG = "工号不能当文件夹名，请联系管理员"
RESERVED_MSG = "工号不能用作登录名，请联系管理员"
OCCUPIED_MSG = "该工号已被其他人占用，请联系管理员"


def job_number_as_username(raw: str) -> str:
    job = (raw or "").strip()
    if not job:
        raise HTTPException(status_code=403, detail=NO_JOB_MSG)
    if job.lower() in RESERVED_USERNAMES:
        raise HTTPException(status_code=403, detail=RESERVED_MSG)
    if job in {".", ".."} or "/" in job or "\\" in job or not _SAFE_JOB.fullmatch(job):
        raise HTTPException(status_code=403, detail=BAD_JOB_MSG)
    return job


async def release_system_dingtalk_binding(
    db: AsyncSession,
    *,
    dingtalk_user_id: str,
    job_number: str,
) -> None:
    """系统账号（如 admin）占用钉钉身份时让开，工号账号才能当门牌。"""
    job = job_number_as_username(job_number)
    result = await db.execute(
        select(User).where(User.dingtalk_user_id == dingtalk_user_id)
    )
    bound = result.scalar_one_or_none()
    if bound is None:
        return
    if bound.username.lower() == job.lower():
        return
    if bound.username.lower() not in {n.lower() for n in SYSTEM_USERNAMES}:
        raise HTTPException(status_code=403, detail=OCCUPIED_MSG)
    bound.dingtalk_user_id = None
    db.add(
        AuditLog(
            user_id=bound.id,
            action="dingtalk_jit_unbind_system",
            resource="user",
            resource_id=bound.id,
            detail=f"username={bound.username}",
        )
    )
    await db.flush()


async def provision_user_by_job(
    db: AsyncSession,
    *,
    dingtalk_user_id: str,
    job_number: str,
    name: str,
    title: str = "",
) -> User:
    job = job_number_as_username(job_number)
    user = await _load_by_username_ci(db, job)
    if user:
        if not user.is_active:
            raise HTTPException(status_code=403, detail="账号已禁用")
        bound = (user.dingtalk_user_id or "").strip()
        if bound and bound != dingtalk_user_id:
            raise HTTPException(status_code=403, detail=OCCUPIED_MSG)
        if not bound:
            user.dingtalk_user_id = dingtalk_user_id
            if title and not (user.job_title or "").strip():
                user.job_title = title[:64]
            db.add(
                AuditLog(
                    user_id=user.id,
                    action="dingtalk_jit_bind",
                    resource="user",
                    resource_id=user.id,
                    detail=f"username={user.username}",
                )
            )
            await db.commit()
        return await _reload(db, user.id)

    member = (
        await db.execute(select(Role).where(Role.code == "member"))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=500, detail="系统未配置成员角色")
    user = User(
        username=job,
        name=(name or job)[:64],
        hashed_password=None,
        dingtalk_user_id=dingtalk_user_id,
        job_title=(title or "")[:64],
        must_change_password=False,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    db.add(UserRole(user_id=user.id, role_id=member.id))
    db.add(
        AuditLog(
            user_id=user.id,
            action="dingtalk_jit_create",
            resource="user",
            resource_id=user.id,
            detail=f"username={job}",
        )
    )
    await db.commit()
    return await _reload(db, user.id)


async def _load_by_username_ci(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(func.lower(User.username) == username.lower())
    )
    return result.scalar_one_or_none()


async def _reload(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id)
    )
    user = result.scalar_one()
    return user
