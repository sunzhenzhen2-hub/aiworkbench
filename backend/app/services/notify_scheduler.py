"""通知定时调度：每分钟检查模板 cron，触发对应扫描任务。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.core.database import async_session
from app.models import NotificationTemplate
from app.services.notify_pipeline import cron_matches
from app.services.reminders import run_scheduled_event

logger = logging.getLogger(__name__)

_SCHEDULER_TASK: asyncio.Task | None = None
_LAST_FIRED: set[str] = set()  # "YYYYMMDDHHMM:event_key"


async def tick_once(now: datetime | None = None) -> list[str]:
    """执行一轮调度检查；返回已触发的 event_key 列表。"""
    now = now or datetime.now()
    minute_key = now.strftime("%Y%m%d%H%M")
    fired: list[str] = []
    async with async_session() as db:
        result = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.is_enabled.is_(True),
                NotificationTemplate.schedule_cron != "",
            )
        )
        templates = list(result.scalars().all())
        for tpl in templates:
            stamp = f"{minute_key}:{tpl.event_key}"
            if stamp in _LAST_FIRED:
                continue
            if not cron_matches(tpl.schedule_cron, now):
                continue
            try:
                await run_scheduled_event(db, tpl.event_key)
                _LAST_FIRED.add(stamp)
                fired.append(tpl.event_key)
                logger.info("notify scheduler fired %s", tpl.event_key)
            except Exception:  # noqa: BLE001
                logger.exception("notify scheduler fail event=%s", tpl.event_key)
    # 只保留最近两分钟标记，避免集合无限增长
    keep_prefix = now.strftime("%Y%m%d%H")
    stale = [k for k in _LAST_FIRED if not k.startswith(keep_prefix)]
    for k in stale:
        _LAST_FIRED.discard(k)
    return fired


async def _loop() -> None:
    while True:
        try:
            await tick_once()
        except Exception:  # noqa: BLE001
            logger.exception("notify scheduler tick error")
        await asyncio.sleep(60)


def start_notify_scheduler() -> None:
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK and not _SCHEDULER_TASK.done():
        return
    _SCHEDULER_TASK = asyncio.create_task(_loop(), name="notify-scheduler")
    logger.info("notify scheduler started")


async def stop_notify_scheduler() -> None:
    global _SCHEDULER_TASK
    if _SCHEDULER_TASK and not _SCHEDULER_TASK.done():
        _SCHEDULER_TASK.cancel()
        try:
            await _SCHEDULER_TASK
        except asyncio.CancelledError:
            pass
    _SCHEDULER_TASK = None
