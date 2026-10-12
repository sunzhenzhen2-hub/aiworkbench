#!/usr/bin/env python3
"""把因写周报 auto-switch 误写入「上一周期」的周报，迁回本周。

判定规则（上海时区）：
- 周报 period_start = 上周一
- 且 submitted_at / updated_at 落在本周一及之后
- 跳过测试账号（username 以 t_ / wb_ 开头）

迁移策略：
- 本周尚无周报：直接改 period_start/period_end 为本周一～周日
- 本周已有周报：把误落条目（去重后）并入本周周报，并清空上一周期误写条目

用法：
  cd backend && PYTHONPATH=. .venv/bin/python ../scripts/migrate_misplaced_week_reports.py
  cd backend && PYTHONPATH=. .venv/bin/python ../scripts/migrate_misplaced_week_reports.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

# 允许从 scripts/ 直接跑
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models import ProgressReport, ProgressReportItem, ProgressReportReview, Task, User
from app.services.report_quality import compose_theme_from_titles, monday_of, sunday_of


def _mask_name(name: str | None) -> str:
    if not name:
        return "?"
    return name[0] + "**"


def _is_test_user(username: str | None) -> bool:
    u = (username or "").lower()
    return u.startswith("t_") or u.startswith("wb_") or "test" in u


def _shanghai_date(dt) -> date | None:
    if dt is None:
        return None
    # DB 存 UTC；转上海日历日
    from zoneinfo import ZoneInfo

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("Asia/Shanghai")).date()


def _item_fingerprint(it: ProgressReportItem) -> str:
    return f"{(it.task_title or '').strip()}||{(it.result_text or '').strip()[:80]}"


async def find_candidates(db) -> list[ProgressReport]:
    this_monday = monday_of(date.today())
    last_monday = this_monday - timedelta(days=7)
    rows = (
        await db.execute(
            select(ProgressReport)
            .options(selectinload(ProgressReport.items), selectinload(ProgressReport.author))
            .where(
                ProgressReport.period_type == "week",
                ProgressReport.period_start == last_monday,
            )
            .order_by(ProgressReport.id)
        )
    ).scalars().all()

    out: list[ProgressReport] = []
    for report in rows:
        author = report.author
        if _is_test_user(getattr(author, "username", None)):
            continue
        touch_dates = [
            d
            for d in (
                _shanghai_date(report.submitted_at),
                _shanghai_date(report.updated_at),
            )
            if d is not None
        ]
        if not touch_dates:
            continue
        touch = max(touch_dates)
        if touch >= this_monday and report.items:
            out.append(report)
    return out


async def get_this_week_report(db, user_id: int, this_monday: date) -> ProgressReport | None:
    return (
        await db.execute(
            select(ProgressReport)
            .options(selectinload(ProgressReport.items))
            .where(
                ProgressReport.user_id == user_id,
                ProgressReport.period_type == "week",
                ProgressReport.period_start == this_monday,
            )
        )
    ).scalar_one_or_none()


async def migrate_one(db, report: ProgressReport, *, apply: bool) -> dict:
    this_monday = monday_of(date.today())
    this_sunday = sunday_of(this_monday)
    author = report.author
    info = {
        "report_id": report.id,
        "user": _mask_name(getattr(author, "name", None)),
        "item_count": len(report.items),
        "action": "",
        "target_report_id": None,
    }

    existing = await get_this_week_report(db, report.user_id, this_monday)
    if existing is None:
        info["action"] = "retarget_period"
        info["target_report_id"] = report.id
        if apply:
            report.period_start = this_monday
            report.period_end = this_sunday
            # 主题按标题重算
            titles = [{"task_title": it.task_title or ""} for it in report.items]
            theme = compose_theme_from_titles(titles)
            if theme:
                report.theme = theme
        return info

    # 本周已有：去重并入
    info["action"] = "merge_into_this_week"
    info["target_report_id"] = existing.id
    existing_fps = {_item_fingerprint(it) for it in existing.items}
    to_move: list[ProgressReportItem] = []
    seen: set[str] = set()
    for it in sorted(report.items, key=lambda x: (x.sort_order, x.id or 0)):
        fp = _item_fingerprint(it)
        if fp in existing_fps or fp in seen:
            continue
        seen.add(fp)
        to_move.append(it)

    info["merge_count"] = len(to_move)
    info["drop_dupes"] = len(report.items) - len(to_move)

    if apply:
        base_sort = max((it.sort_order for it in existing.items), default=-1) + 1
        for idx, it in enumerate(to_move):
            it.report_id = existing.id
            it.sort_order = base_sort + idx
        # 剩余误写条目（含重复）从上周报删除
        keep_ids = {it.id for it in to_move}
        for it in list(report.items):
            if it.id not in keep_ids:
                await db.delete(it)
        # 任务溯源：误写周报上的 source_report_id 改挂本周
        tasks = (
            await db.execute(select(Task).where(Task.source_report_id == report.id))
        ).scalars().all()
        for t in tasks:
            t.source_report_id = existing.id
        # 刷新本周主题
        await db.flush()
        await db.refresh(existing, attribute_names=["items"])
        titles = [{"task_title": it.task_title or ""} for it in existing.items]
        theme = compose_theme_from_titles(titles)
        if theme:
            existing.theme = theme
        # 上周误写周报若已无条目：删空壳（含过期评价），避免历史芯片出现「已交·0条」
        await db.flush()
        await db.refresh(report, attribute_names=["items"])
        if not report.items:
            reviews = (
                await db.execute(
                    select(ProgressReportReview).where(ProgressReportReview.report_id == report.id)
                )
            ).scalars().all()
            for rev in reviews:
                await db.delete(rev)
            await db.delete(report)
            info["deleted_empty_source"] = True
    return info


async def run(*, apply: bool) -> int:
    this_monday = monday_of(date.today())
    last_monday = this_monday - timedelta(days=7)
    print(f"本周={this_monday}~{sunday_of(this_monday)}  误写源周期={last_monday}")
    print(f"模式={'APPLY 写入' if apply else 'DRY-RUN 只预览'}")

    async with async_session() as db:
        candidates = await find_candidates(db)
        if not candidates:
            print("没有需要迁移的周报。")
            return 0
        results = []
        for report in candidates:
            results.append(await migrate_one(db, report, apply=apply))
        if apply:
            await db.commit()
        else:
            await db.rollback()

    for r in results:
        extra = ""
        if r["action"] == "merge_into_this_week":
            extra = f" 并入{r.get('merge_count', 0)}条、去重丢弃{r.get('drop_dupes', 0)}条"
        print(
            f"- report#{r['report_id']} {r['user']} "
            f"items={r['item_count']} → {r['action']} target=#{r['target_report_id']}{extra}"
        )
    print(f"合计 {len(results)} 份周报{'已迁移' if apply else '待迁移'}。")
    return len(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移误落入上一周期的周报到本周")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
