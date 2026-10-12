#!/usr/bin/env python3
"""历史「继续推进…」类周报卡片补任务链（只写 chain_id）。

用法（在 backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/backfill_continuation_chains.py
  PYTHONPATH=. .venv/bin/python scripts/backfill_continuation_chains.py --apply

默认 dry-run：只打印拟改动，不写库。改库前请先：
  bash /home/aiworkbench/scripts/backup.sh
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.models import ProgressReport, ProgressReportItem, User  # noqa: E402
from app.services.report_ai_structure import (  # noqa: E402
    _chain_compare_text,
    _similarity,
    is_continuation_text,
    is_soft_continuation_text,
    strip_continuation_prefix,
)

# 明确「继续推进」类：中等相似度即可自动写
_HARD_AUTO_MIN = 0.45
# 仅套话「继续…」下一步：须高相似标题（跨周同一件事）才自动写
_SOFT_AUTO_MIN = 0.70
_REVIEW_MIN = 0.40


@dataclass
class LinkPlan:
    current_id: int
    seed_id: int
    user_name: str
    current_title: str
    seed_title: str
    current_period: str
    seed_period: str
    score: float
    chain_id: str
    seed_needs_chain: bool
    auto: bool
    mode: str  # hard | soft


def _score_pair(cur: ProgressReportItem, seed: ProgressReportItem) -> float:
    cur_title = (cur.task_title or "").strip()
    seed_title = (seed.task_title or "").strip()
    cur_cmp = _chain_compare_text(cur_title, cur.result_text or "", cur.next_goal or "")
    seed_cmp = _chain_compare_text(seed_title, seed.result_text or "", seed.next_goal or "")
    return max(
        _similarity(cur_title, seed_title),
        _similarity(strip_continuation_prefix(cur_title), strip_continuation_prefix(seed_title)),
        _similarity(cur_cmp, seed_cmp) if cur_cmp and seed_cmp else 0.0,
    )


async def collect_plans() -> tuple[list[LinkPlan], list[dict[str, Any]]]:
    async with async_session() as db:
        reports = list(
            (
                await db.execute(
                    select(ProgressReport)
                    .options(selectinload(ProgressReport.items))
                    .where(ProgressReport.status == "submitted")
                    .order_by(ProgressReport.period_start.asc())
                )
            ).scalars().all()
        )

        by_user: dict[int, list[tuple[ProgressReport, ProgressReportItem]]] = defaultdict(list)
        for report in reports:
            for it in report.items:
                if it.no_action:
                    continue
                title = (it.task_title or "").strip()
                if not title and not (it.result_text or "").strip():
                    continue
                by_user[report.user_id].append((report, it))

        users = {
            u.id: u
            for u in (
                await db.execute(select(User).where(User.id.in_(list(by_user.keys()) or [-1])))
            ).scalars().all()
        }

        plans: list[LinkPlan] = []
        skipped: list[dict[str, Any]] = []

        for user_id, rows in by_user.items():
            user = users.get(user_id)
            uname = (user.name if user else f"uid:{user_id}") or f"uid:{user_id}"
            for idx, (report, it) in enumerate(rows):
                title = (it.task_title or "").strip()
                next_goal = (it.next_goal or "").strip()
                hard = is_continuation_text(title) or is_continuation_text(next_goal)
                soft = (not hard) and (
                    is_soft_continuation_text(title) or is_soft_continuation_text(next_goal)
                )
                if not hard and not soft:
                    continue
                if (it.chain_id or "").strip():
                    skipped.append(
                        {
                            "item_id": it.id,
                            "reason": "已有 chain_id",
                            "title": title[:80],
                            "user": uname,
                        }
                    )
                    continue

                best_score = 0.0
                best: tuple[ProgressReport, ProgressReportItem] | None = None
                for j in range(idx - 1, -1, -1):
                    prev_report, prev = rows[j]
                    if prev_report.period_start >= report.period_start:
                        continue
                    score = _score_pair(it, prev)
                    if (
                        it.project_id
                        and prev.project_id
                        and it.project_id == prev.project_id
                        and score > 0
                    ):
                        score = min(1.0, score + 0.08)
                    if score > best_score:
                        best_score = score
                        best = (prev_report, prev)

                auto_min = _HARD_AUTO_MIN if hard else _SOFT_AUTO_MIN
                if best is None or best_score < _REVIEW_MIN:
                    skipped.append(
                        {
                            "item_id": it.id,
                            "reason": f"无足够相似前序(best={best_score:.2f})",
                            "title": title[:80],
                            "user": uname,
                        }
                    )
                    continue

                seed_report, seed = best
                existing = (seed.chain_id or "").strip()
                chain_id = existing or str(uuid.uuid4())
                plans.append(
                    LinkPlan(
                        current_id=it.id,
                        seed_id=seed.id,
                        user_name=uname,
                        current_title=title[:100],
                        seed_title=(seed.task_title or "")[:100],
                        current_period=str(report.period_start),
                        seed_period=str(seed_report.period_start),
                        score=best_score,
                        chain_id=chain_id,
                        seed_needs_chain=not bool(existing),
                        auto=best_score >= auto_min,
                        mode="hard" if hard else "soft",
                    )
                )

        return plans, skipped


async def apply_plans(plans: list[LinkPlan]) -> int:
    auto_plans = [p for p in plans if p.auto]
    if not auto_plans:
        return 0
    async with async_session() as db:
        ids = {p.current_id for p in auto_plans} | {
            p.seed_id for p in auto_plans if p.seed_needs_chain
        }
        items = {
            it.id: it
            for it in (
                await db.execute(select(ProgressReportItem).where(ProgressReportItem.id.in_(ids)))
            ).scalars().all()
        }
        changed = 0
        for p in auto_plans:
            cur = items.get(p.current_id)
            if not cur:
                continue
            if (cur.chain_id or "").strip():
                continue
            cur.chain_id = p.chain_id
            changed += 1
            if p.seed_needs_chain:
                seed = items.get(p.seed_id)
                if seed and not (seed.chain_id or "").strip():
                    seed.chain_id = p.chain_id
                    changed += 1
        await db.commit()
        return changed


def _print_plans(plans: list[LinkPlan], skipped: list[dict[str, Any]]) -> None:
    auto = [p for p in plans if p.auto]
    review = [p for p in plans if not p.auto]
    print(
        f"拟自动补链: {len(auto)} 条；低置信待确认: {len(review)} 条；跳过: {len(skipped)} 条"
    )
    print(
        f"\n==== 自动（hard≥{_HARD_AUTO_MIN:.2f} / soft≥{_SOFT_AUTO_MIN:.2f}）===="
    )
    for p in auto:
        print(
            f"  [{p.user_name}] ({p.mode}) {p.current_period}「{p.current_title}」"
            f" ← {p.seed_period}「{p.seed_title}」"
            f" score={p.score:.2f} chain={p.chain_id[:8]}…"
            f"{' +seed' if p.seed_needs_chain else ''}"
        )
    if review:
        print("\n==== 待确认（不自动写）====")
        for p in review:
            print(
                f"  [{p.user_name}] ({p.mode}) {p.current_period}「{p.current_title}」"
                f" ← {p.seed_period}「{p.seed_title}」 score={p.score:.2f}"
            )
    if skipped:
        print("\n==== 跳过样例（最多 20）====")
        for s in skipped[:20]:
            print(f"  [{s['user']}] #{s['item_id']} {s['reason']} | {s['title']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="延续类周报卡片补任务链")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入数据库（仅高置信）；默认 dry-run",
    )
    args = parser.parse_args()

    plans, skipped = await collect_plans()
    _print_plans(plans, skipped)

    if not args.apply:
        print("\n[dry-run] 未写库。确认后加 --apply；改库前请先 bash scripts/backup.sh")
        return

    n = await apply_plans(plans)
    print(f"\n[apply] 已写入 chain_id 字段变更约 {n} 处（含种子卡）")


if __name__ == "__main__":
    asyncio.run(main())
