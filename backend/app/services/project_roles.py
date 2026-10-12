"""项目成员人选：项目经理按姓名，其余同事默认可当项目支撑（管理员除外）。"""

from __future__ import annotations

from app.models import User

# 每个项目的项目经理只能从这两人里选一个
PROJECT_MANAGER_NAMES = frozenset({"许金斌", "周永祺"})
# 管理员：不当项目经理，也不当项目支撑
SUPPORT_EXCLUDED_NAMES = frozenset({"孙振振"})


def is_project_manager_name(name: str | None) -> bool:
    return (name or "").strip() in PROJECT_MANAGER_NAMES


def is_project_support_user(user: User) -> bool:
    """许金斌/周永祺为项目经理，孙振振为管理员；其余在职同事都可当项目支撑。"""
    name = (user.name or "").strip()
    if not name:
        return False
    if name in SUPPORT_EXCLUDED_NAMES:
        return False
    if name in PROJECT_MANAGER_NAMES:
        return False
    return True


def default_roles_for_creator(user: User) -> tuple[int | None, list[int]]:
    """
    创建项目时按创建人身份给出默认角色：
    - 项目经理创建 → 本人为项目经理
    - 项目支撑创建 → 本人为项目支撑
    - 管理员等 → 不默认
    """
    name = (user.name or "").strip()
    if is_project_manager_name(name):
        return user.id, []
    if is_project_support_user(user):
        return None, [user.id]
    return None, []
