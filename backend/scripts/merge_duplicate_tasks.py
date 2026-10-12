#!/usr/bin/env python3
"""合并同负责人下高度相似的未完成交办任务。

用法（在 backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/merge_duplicate_tasks.py
  PYTHONPATH=. .venv/bin/python scripts/merge_duplicate_tasks.py --apply

默认 dry-run。改库前请先：
  bash /home/aiworkbench/scripts/backup.sh

规则：
- 仅未完成（status != completed）
- 同 assignee_id
- 标题相似 ≥ 0.85（去续推前缀后）
- 保留最早 created_at 为主任务；其余标为 completed，result_text 追加合并说明
- ProgressReportItem.linked_task_id 从重复改挂到主任务
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.models import ProgressReportItem, Task, User  # noqa: E402
from app.services.task_dedupe import HARD_SIMILARITY, score_task_similarity  # noqa: E402


@dataclass
class MergePair:
    keeper_id: int
    dup_id: int
    assignee_name: str
    keeper_title: str
    dup_title: str
    score: float


async def collect_pairs() -> list[MergePair]:
    async with async_session() as db:
        tasks = list(
            (
                await db.execute(select(Task).where(Task.status != "completed"))
            ).scalars().all()
        )
        by_assignee: dict[int, list[Task]] = defaultdict(list)
        for t in tasks:
            by_assignee[t.assignee_id].append(t)

        user_ids = list(by_assignee.keys())
        users = {
            u.id: u
            for u in (
                await db.execute(select(User).where(User.id.in_(user_ids or [-1])))
            ).scalars().all()
        }

        pairs: list[MergePair] = []
        absorbed: set[int] = set()

        for aid, rows in by_assignee.items():
            rows = sorted(rows, key=lambda t: (t.created_at or t.id, t.id))
            uname = (users.get(aid).name if users.get(aid) else f"uid:{aid}") or f"uid:{aid}"
            for i, a in enumerate(rows):
                if a.id in absorbed:
                    continue
                for b in rows[i + 1 :]:
                    if b.id in absorbed:
                        continue
                    score = score_task_similarity(
                        a.title or "", a.goal or "", b.title or "", b.goal or ""
                    )
                    if a.project_id == b.project_id and score > 0:
                        score = min(1.0, score + 0.05)
                    if score < HARD_SIMILARITY:
                        continue
                    # a 更早 → keeper；b 为重复
                    pairs.append(
                        MergePair(
                            keeper_id=a.id,
                            dup_id=b.id,
                            assignee_name=uname,
                            keeper_title=(a.title or "")[:80],
                            dup_title=(b.title or "")[:80],
                            score=score,
                        )
                    )
                    absorbed.add(b.id)
        return pairs


async def apply_pairs(pairs: list[MergePair]) -> dict[str, int]:
    if not pairs:
        return {"merged": 0, "relinked": 0}
    async with async_session() as db:
        merged = 0
        relinked = 0
        for p in pairs:
            dup = (
                await db.execute(select(Task).where(Task.id == p.dup_id))
            ).scalar_one_or_none()
            keeper = (
                await db.execute(select(Task).where(Task.id == p.keeper_id))
            ).scalar_one_or_none()
            if not dup or not keeper:
                continue
            if dup.status == "completed":
                continue
            note = f"已与任务 #{keeper.id} 合并（相似 {p.score:.0%}）"
            prev = (dup.result_text or "").strip()
            dup.result_text = f"{prev}\n{note}".strip() if prev else note
            dup.status = "completed"
            merged += 1

            result = await db.execute(
                update(ProgressReportItem)
                .where(ProgressReportItem.linked_task_id == dup.id)
                .values(linked_task_id=keeper.id)
            )
            relinked += result.rowcount or 0

        await db.commit()
        return {"merged": merged, "relinked": relinked}


def _print(pairs: list[MergePair]) -> None:
    print(f"拟合并重复交办: {len(pairs)} 组（相似≥{HARD_SIMILARITY}）")
    for p in pairs:
        print(
            f"  [{p.assignee_name}] 保留 #{p.keeper_id}「{p.keeper_title}」"
            f" ← 合并 #{p.dup_id}「{p.dup_title}」 score={p.score:.2f}"
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description="合并高度相似未完成交办")
    parser.add_argument("--apply", action="store_true", help="写入数据库；默认 dry-run")
    args = parser.parse_args()

    pairs = await collect_pairs()
    _print(pairs)

    if not args.apply:
        print("\n[dry-run] 未写库。确认后加 --apply；改库前请先 bash scripts/backup.sh")
        print("周报补链可另跑: PYTHONPATH=. .venv/bin/python scripts/backfill_continuation_chains.py --apply")
        return

    stats = await apply_pairs(pairs)
    print(f"\n[apply] 已完成合并 {stats['merged']} 条；周报 linked_task_id 改挂 {stats['relinked']} 处")


if __name__ == "__main__":
    asyncio.run(main())
