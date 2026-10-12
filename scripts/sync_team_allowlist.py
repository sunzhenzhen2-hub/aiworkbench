#!/usr/bin/env python3
"""按姓名白名单从钉钉同步团队，孙振振设为管理员。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models import User
from app.services.user_sync import TEAM_SYNC_NAMES, sync_team_allowlist


async def main() -> int:
    async with async_session() as db:
        admin = (
            await db.execute(select(User).where(User.username == "admin"))
        ).scalar_one_or_none()
        if not admin:
            print(json.dumps({"ok": False, "error": "缺少 admin"}, ensure_ascii=False))
            return 1
        result = await sync_team_allowlist(
            db,
            actor=admin,
            department_name="政企市场部",
            allow_names=TEAM_SYNC_NAMES,
            admin_name="孙振振",
        )

        users = (
            await db.execute(
                select(User).options(selectinload(User.roles)).order_by(User.id)
            )
        ).scalars().all()
        roster = [
            {
                "id": u.id,
                "username": u.username,
                "name": u.name,
                "active": u.is_active,
                "dingtalk": bool(u.dingtalk_user_id),
                "roles": [r.code for r in (u.roles or [])],
            }
            for u in users
        ]
        out = {"ok": True, "sync": result, "users_now": roster}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if not result.get("missing_in_dingtalk") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
