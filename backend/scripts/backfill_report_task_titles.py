#!/usr/bin/env python3
"""回填周报同步待办的不清晰标题（source=report_next，未完成）。

用法（在 backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/backfill_report_task_titles.py
  PYTHONPATH=. .venv/bin/python scripts/backfill_report_task_titles.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.models import ProgressReportItem, Project, Task  # noqa: E402
from app.services.report_next_plan import (  # noqa: E402
    SOURCE_REPORT_NEXT,
    _is_template_continuation,
    _rule_normalize,
    compose_clear_task_title,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description="回填周报待办清晰标题")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async with async_session() as db:
        tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.status != "completed",
                    )
                )
            ).scalars().all()
        )
        project_ids = {t.project_id for t in tasks}
        names = {
            p.id: p.name
            for p in (
                await db.execute(select(Project).where(Project.id.in_(project_ids or [-1])))
            ).scalars().all()
        }

        # linked item via linked_task_id
        linked = {
            it.linked_task_id: it
            for it in (
                await db.execute(
                    select(ProgressReportItem).where(
                        ProgressReportItem.linked_task_id.in_([t.id for t in tasks] or [-1])
                    )
                )
            ).scalars().all()
        }

        changed = 0
        for t in tasks:
            it = linked.get(t.id)
            task_title = (it.task_title if it else "") or ""
            next_goal = (it.next_goal if it else "") or (t.goal or "")
            if not task_title and not next_goal:
                next_goal = t.title or ""
            pname = names.get(t.project_id, "")
            new_title = compose_clear_task_title(
                task_title=task_title,
                next_goal=next_goal or t.title or "",
                project_name=pname,
                result_text=(it.result_text if it else "") or "",
            )
            new_goal = t.goal or ""
            if it and (
                _is_template_continuation(next_goal, task_title)
                or _is_template_continuation(t.title or "", task_title)
            ):
                norm = _rule_normalize(
                    task_title=task_title,
                    next_goal=next_goal,
                    next_due_date=t.due_date,
                    project_name=pname,
                    result_text=it.result_text or "",
                )
                new_title = norm.title
                new_goal = norm.success_criteria
            if new_title == (t.title or "").strip() and new_goal == (t.goal or "").strip():
                continue
            print(f"  #{t.id}: 「{(t.title or '')[:50]}」 → 「{new_title[:60]}」")
            if args.apply:
                t.title = new_title[:256]
                if new_goal:
                    t.goal = new_goal[:2000]
            changed += 1

        if args.apply:
            await db.commit()
            print(f"\n[apply] 已更新 {changed} 条标题")
        else:
            await db.rollback()
            print(f"\n[dry-run] 拟更新 {changed} 条；加 --apply 写入")


if __name__ == "__main__":
    asyncio.run(main())
