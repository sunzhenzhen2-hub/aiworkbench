from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models import Role, User
from app.schemas import UserOut

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_access_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效令牌")
    user_id = int(payload["sub"])
    result = await db.execute(
        select(User)
        .options(
            selectinload(User.roles).selectinload(Role.permissions),
        )
        .where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用")
    return user


async def require_password_updated(user: User = Depends(get_current_user)) -> User:
    if user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="首次登录须修改初始密码",
            headers={"X-Password-Change-Required": "1"},
        )
    return user


def get_user_permissions(user: User) -> set[str]:
    codes: set[str] = set()
    for role in user.roles:
        for perm in role.permissions:
            codes.add(perm.code)
    return codes


def get_user_role_codes(user: User) -> set[str]:
    return {r.code for r in (user.roles or [])}


def is_project_manager(user: User) -> bool:
    """管理员：系统管理员或部门负责人。"""
    return bool(get_user_role_codes(user) & {"admin", "dept_head"})


def can_edit_project(user: User, *, owner_id: int) -> bool:
    """可改项目基本信息：该项目负责人，或管理员。"""
    if "project.edit" not in get_user_permissions(user):
        return False
    if is_project_manager(user):
        return True
    return owner_id == user.id


def to_user_out(user: User) -> UserOut:
    perms = get_user_permissions(user)
    return UserOut(
        id=user.id,
        name=user.name,
        username=user.username,
        job_number=user.username,
        email=user.email,
        avatar_url=user.avatar_url,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        roles=[r.code for r in user.roles],
        permissions=sorted(perms),
        dingtalk_user_id=user.dingtalk_user_id,
        dingtalk_bound=bool(user.dingtalk_user_id),
        job_title=(user.job_title or ""),
    )


def require_permission(code: str):
    async def checker(user: User = Depends(require_password_updated)) -> User:
        if code not in get_user_permissions(user):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="权限不足")
        return user

    return checker
