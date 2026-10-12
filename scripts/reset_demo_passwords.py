"""重置演示账号为初始密码，并标记须首次改密。"""

import asyncio

from app.core.database import async_session
from app.core.migrations import run_migrations
from app.services.init_service import ensure_demo_users


async def main() -> None:
    await run_migrations()
    async with async_session() as db:
        await ensure_demo_users(db, reset_passwords=True)
    print("演示账号已重置为初始密码（首次登录须修改）")


if __name__ == "__main__":
    asyncio.run(main())
