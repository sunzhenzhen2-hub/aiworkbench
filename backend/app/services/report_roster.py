"""周报应提交名单：仅统计指定团队成员（不含 admin）。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User

# 钱波 + 罗琳 + 凌亮 + 徐初 + 孙聪 + 俞烨涛 + 朱高见 + 王捷 + 金滨 + 许金斌 + 周永祺
# 不含管理员孙振振（FY0354 / 系统 admin）
REPORT_ROSTER_USERNAMES: tuple[str, ...] = (
    "FY0395",  # 钱波
    "FY0383",  # 罗琳
    "FY0317",  # 凌亮
    "fy0583",  # 徐初
    "fy0584",  # 孙聪
    "FY0611",  # 俞烨涛
    "FY0614",  # 朱高见
    "FY0357",  # 王捷
    "FY0407",  # 金滨
    "FY0406",  # 许金斌
    "FY0623",  # 周永祺
)

_ROSTER_LOWER = frozenset(u.lower() for u in REPORT_ROSTER_USERNAMES)


def is_report_roster_username(username: str | None) -> bool:
    return bool(username) and username.strip().lower() in _ROSTER_LOWER


async def list_report_roster_users(db: AsyncSession) -> list[User]:
    """按固定名单拉取用户（保持名单顺序）。不存在的工号会跳过。"""
    result = await db.execute(
        select(User).where(func.lower(User.username).in_(list(_ROSTER_LOWER)))
    )
    by_lower = {u.username.lower(): u for u in result.scalars().all()}
    ordered: list[User] = []
    for uname in REPORT_ROSTER_USERNAMES:
        u = by_lower.get(uname.lower())
        if u:
            ordered.append(u)
    return ordered
