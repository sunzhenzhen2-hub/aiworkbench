#!/usr/bin/env python3
"""清理自动化测试 / 演示产生的脏账号与假项目，保留真实员工与业务项目。

用法（在 backend 目录）:
  PYTHONPATH=. .venv/bin/python ../scripts/purge_test_data.py
  PYTHONPATH=. .venv/bin/python ../scripts/purge_test_data.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 允许从 scripts/ 直接运行
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select

from app.core.database import async_session
from app.models import AuditLog, Project, User
from app.services.project_service import (
    cleanup_project_attachment_files,
    delete_projects_cascade,
)
from app.services.user_sync import _detach_and_delete_users, _get_admin


def _is_test_username(username: str) -> bool:
    u = username.lower()
    if u in {"manager", "owner1", "member1", "member2"}:
        return True
    # pv_/pd_：可见性 / 删除联调测试账号
    # pr_/ra_：成员角色、附件可见性等单测造的假账号（名字可能撞真员工）
    # cl_/af_/pf_：交办闭环、附件可见性、列表筛选等单测造的假账号
    return u.startswith(
        ("t_", "ta_", "test_", "e2e_", "pv_", "pd_", "pr_", "ra_", "cl_", "af_", "pf_")
    )


def _is_test_project_name(name: str) -> bool:
    n = name.strip()
    if n.startswith(
        ("proj_", "role_", "phase_", "新建角色_", "无经理_", "双支撑_", "经理自建_", "cl_proj_", "pf_proj_")
    ):
        return True
    # 可见性/改名联调造的假项目
    if n.startswith("可见项目_"):
        return True
    # 任务链单测造的假项目（带随机后缀）
    if n.startswith("天网接入专项-"):
        return True
    if n in {"管理员改名", "负责人改名"}:
        return True
    # 「测试」二字：仅当整名像单测/联调假名，避免误伤真实立项（如「…测试反馈分析」）
    if n.startswith("测试") or n.endswith("测试") or "任务周报联调" in n:
        return True
    if "联调测试" in n or "单测" in n:
        return True
    low = n.lower()
    return low.startswith(("test", "e2e", "demo_", "cl_proj"))


async def run(*, dry_run: bool) -> None:
    async with async_session() as db:
        users = (await db.execute(select(User))).scalars().all()
        test_users = [u for u in users if _is_test_username(u.username)]
        projects = (await db.execute(select(Project))).scalars().all()
        test_projects = [p for p in projects if _is_test_project_name(p.name)]

        print("将删除测试账号:")
        for u in test_users:
            print(f"  - {u.id} {u.username} ({u.name})")
        print("将删除测试项目:")
        for p in test_projects:
            print(f"  - {p.id} {p.name}")

        if dry_run:
            print("dry-run：未写入数据库")
            return

        admin = await _get_admin(db)
        proj_counts, stored_paths = await delete_projects_cascade(
            db, [p.id for p in test_projects]
        )
        deleted_users = await _detach_and_delete_users(
            db, [u.id for u in test_users], reassign_to=admin
        )
        db.add(
            AuditLog(
                user_id=admin.id,
                action="purge_test_data",
                resource="system",
                resource_id=None,
                detail=(
                    f"users={deleted_users}; "
                    f"projects={proj_counts.get('projects', 0)}; "
                    f"tasks={proj_counts.get('tasks', 0)}"
                ),
            )
        )
        await db.commit()
        cleanup_project_attachment_files(stored_paths)
        print("已清理:", {"users": deleted_users, **proj_counts})


def main() -> None:
    parser = argparse.ArgumentParser(description="清理测试账号与测试项目")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不删除")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
