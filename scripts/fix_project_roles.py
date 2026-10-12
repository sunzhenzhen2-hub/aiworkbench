#!/usr/bin/env python3
"""存量项目成员角色纠偏：非法项目经理降为支撑；合法经理保留；无支撑时用归属人补。

用法（在 backend 目录）:
  PYTHONPATH=. .venv/bin/python ../scripts/fix_project_roles.py --dry-run
  PYTHONPATH=. .venv/bin/python ../scripts/fix_project_roles.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models import AuditLog, Project, ProjectMember, User
from app.services.project_roles import (
    is_project_manager_name,
    is_project_support_user,
)
from app.services.project_service import DAILY_PROJECT_NAME
from app.services.user_sync import _get_admin


async def run(*, dry_run: bool) -> None:
    async with async_session() as db:
        projects = list(
            (
                await db.execute(
                    select(Project).options(selectinload(Project.members).selectinload(ProjectMember.user))
                )
            ).scalars().all()
        )
        users_by_id = {
            u.id: u
            for u in (await db.execute(select(User))).scalars().all()
        }

        demote_lead_to_support = 0
        demote_lead_to_member = 0
        promote_owner_to_support = 0
        kept_valid_lead = 0
        skipped_daily = 0
        touched_projects = 0

        details: list[str] = []

        for project in projects:
            if project.name == DAILY_PROJECT_NAME:
                skipped_daily += 1
                continue

            members = list(project.members or [])
            if not members:
                continue

            changed = False
            leads = [m for m in members if m.member_role == "lead"]
            supports = [m for m in members if m.member_role == "support"]

            for m in leads:
                user = m.user or users_by_id.get(m.user_id)
                name = (user.name if user else "") or ""
                if is_project_manager_name(name):
                    kept_valid_lead += 1
                    continue
                # 非法「项目经理」：能当支撑的降为支撑，否则降为普通成员
                if user is not None and is_project_support_user(user):
                    m.member_role = "support"
                    demote_lead_to_support += 1
                    details.append(
                        f"project#{project.id} {project.name!r}: {name} lead→support"
                    )
                else:
                    m.member_role = "member"
                    demote_lead_to_member += 1
                    details.append(
                        f"project#{project.id} {project.name!r}: {name or m.user_id} lead→member"
                    )
                changed = True

            # 刷新支撑集合
            supports = [m for m in members if m.member_role == "support"]
            lead_ids = {m.user_id for m in members if m.member_role == "lead"}

            if not supports:
                owner = users_by_id.get(project.owner_id)
                if (
                    owner is not None
                    and is_project_support_user(owner)
                    and owner.id not in lead_ids
                ):
                    owner_mem = next((m for m in members if m.user_id == owner.id), None)
                    if owner_mem is None:
                        members.append(
                            ProjectMember(
                                project_id=project.id,
                                user_id=owner.id,
                                member_role="support",
                            )
                        )
                        db.add(members[-1])
                        promote_owner_to_support += 1
                        details.append(
                            f"project#{project.id} {project.name!r}: "
                            f"add owner {owner.name} as support"
                        )
                        changed = True
                    elif owner_mem.member_role != "support":
                        prev = owner_mem.member_role
                        owner_mem.member_role = "support"
                        promote_owner_to_support += 1
                        details.append(
                            f"project#{project.id} {project.name!r}: "
                            f"owner {owner.name} {prev}→support"
                        )
                        changed = True

            if changed:
                touched_projects += 1

        print(f"跳过日常任务: {skipped_daily}")
        print(f"将改动的项目数: {touched_projects}")
        print(f"非法经理→支撑: {demote_lead_to_support}")
        print(f"非法经理→成员: {demote_lead_to_member}")
        print(f"保留合法经理: {kept_valid_lead}")
        print(f"归属人补为支撑: {promote_owner_to_support}")
        print("--- 明细 ---")
        for line in details:
            print(line)

        if dry_run:
            await db.rollback()
            print("dry-run：未写入数据库")
            return

        admin = await _get_admin(db)
        db.add(
            AuditLog(
                user_id=admin.id if admin else None,
                action="fix_project_roles",
                resource="system",
                resource_id=None,
                detail=(
                    f"projects={touched_projects};"
                    f"lead_to_support={demote_lead_to_support};"
                    f"lead_to_member={demote_lead_to_member};"
                    f"owner_to_support={promote_owner_to_support}"
                ),
            )
        )
        await db.commit()
        print("已写入数据库")


def main() -> None:
    parser = argparse.ArgumentParser(description="存量项目成员角色纠偏")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    args = parser.parse_args()
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
