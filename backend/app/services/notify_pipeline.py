"""统一通知发送管道：读模板 → 渲染 → 站内/钉钉 → 写发送日志。"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Notification,
    NotificationDeliveryLog,
    NotificationTemplate,
    User,
)
from app.services.dingtalk import send_work_notice
from app.websocket.notifications import manager

logger = logging.getLogger(__name__)

_PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def render_template(tpl: str, variables: dict[str, Any]) -> str:
    """安全替换 {var}；缺失变量留空字符串。"""

    def _repl(m: re.Match[str]) -> str:
        key = m.group(1)
        val = variables.get(key, "")
        return "" if val is None else str(val)

    return _PLACEHOLDER.sub(_repl, tpl or "")


def _parse_hhmm(value: str | None) -> time | None:
    if value is None or value == "":
        return None
    parts = value.strip().split(":")
    if len(parts) < 2:
        raise ValueError("时间格式应为 HH:MM")
    return time(int(parts[0]), int(parts[1]))


def format_time_opt(t: time | None) -> str | None:
    if t is None:
        return None
    return f"{t.hour:02d}:{t.minute:02d}"


def in_quiet_hours(now: time, quiet_start: time | None, quiet_end: time | None) -> bool:
    """判断当前时刻是否在安静时段（支持跨夜，如 22:00–08:00）。"""
    if quiet_start is None or quiet_end is None:
        return False
    if quiet_start == quiet_end:
        return False
    if quiet_start < quiet_end:
        return quiet_start <= now < quiet_end
    return now >= quiet_start or now < quiet_end


def cron_matches(cron: str, when: datetime) -> bool:
    """匹配五段 cron：分 时 日 月 周（周 0=周日，也接受 7）。空串不匹配。"""
    cron = (cron or "").strip()
    if not cron:
        return False
    parts = cron.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    cron_dow = (when.weekday() + 1) % 7

    def _field_ok(field: str, value: int) -> bool:
        if field == "*":
            return True
        for piece in field.split(","):
            piece = piece.strip()
            if piece == "*":
                return True
            if "/" in piece:
                base, step_s = piece.split("/", 1)
                step = int(step_s)
                if base == "*":
                    return value % step == 0
                return value >= int(base) and (value - int(base)) % step == 0
            if "-" in piece:
                a, b = piece.split("-", 1)
                if int(a) <= value <= int(b):
                    return True
                continue
            if int(piece) == value:
                return True
        return False

    if not _field_ok(minute, when.minute):
        return False
    if not _field_ok(hour, when.hour):
        return False
    if not _field_ok(dom, when.day):
        return False
    if not _field_ok(month, when.month):
        return False
    if dow != "*":
        ok = False
        for piece in dow.split(","):
            piece = piece.strip()
            vals: list[int] = []
            if "-" in piece:
                a, b = piece.split("-", 1)
                vals = list(range(int(a), int(b) + 1))
            elif piece == "*":
                ok = True
                break
            else:
                vals = [int(piece)]
            for v in vals:
                if v == 7:
                    v = 0
                if v == cron_dow:
                    ok = True
                    break
            if ok:
                break
        if not ok:
            return False
    return True


async def get_template(db: AsyncSession, event_key: str) -> NotificationTemplate | None:
    result = await db.execute(
        select(NotificationTemplate).where(NotificationTemplate.event_key == event_key)
    )
    return result.scalar_one_or_none()


async def _count_today_success(
    db: AsyncSession,
    *,
    event_key: str,
    user_id: int,
    channel: str,
    ref_type: str,
    ref_id: str,
) -> int:
    today = date.today()
    start = datetime(today.year, today.month, today.day, tzinfo=timezone.utc)
    q = (
        select(func.count())
        .select_from(NotificationDeliveryLog)
        .where(
            NotificationDeliveryLog.event_key == event_key,
            NotificationDeliveryLog.user_id == user_id,
            NotificationDeliveryLog.channel == channel,
            NotificationDeliveryLog.status == "success",
            NotificationDeliveryLog.created_at >= start,
        )
    )
    if ref_type:
        q = q.where(
            NotificationDeliveryLog.ref_type == ref_type,
            NotificationDeliveryLog.ref_id == ref_id,
        )
    result = await db.execute(q)
    return int(result.scalar_one() or 0)


async def _write_log(
    db: AsyncSession,
    *,
    event_key: str,
    template_id: int | None,
    user_id: int | None,
    channel: str,
    title: str,
    content: str,
    status: str,
    error_message: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> None:
    db.add(
        NotificationDeliveryLog(
            event_key=event_key,
            template_id=template_id,
            user_id=user_id,
            channel=channel,
            title=title[:256],
            content=content or "",
            status=status,
            error_message=(error_message or "")[:2000],
            ref_type=ref_type or "",
            ref_id=str(ref_id or ""),
        )
    )


async def _send_inbox(
    db: AsyncSession,
    *,
    u: User,
    title: str,
    body: str,
    payload: dict[str, Any],
    event_key: str,
    template_id: int | None,
    max_per_day: int,
    ref_type: str,
    ref_id_s: str,
) -> None:
    if max_per_day > 0:
        n = await _count_today_success(
            db,
            event_key=event_key,
            user_id=u.id,
            channel="inbox",
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
        if n >= max_per_day:
            await _write_log(
                db,
                event_key=event_key,
                template_id=template_id,
                user_id=u.id,
                channel="inbox",
                title=title,
                content=body,
                status="skipped",
                error_message="max_per_day",
                ref_type=ref_type,
                ref_id=ref_id_s,
            )
            return
    try:
        db.add(
            Notification(
                user_id=u.id,
                title=(title or "")[:128],
                content=body or "",
            )
        )
        await manager.send_to_user(
            u.id,
            {"title": title, "content": body, **payload},
        )
        await _write_log(
            db,
            event_key=event_key,
            template_id=template_id,
            user_id=u.id,
            channel="inbox",
            title=title,
            content=body,
            status="success",
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("inbox notify fail user=%s", u.id)
        await _write_log(
            db,
            event_key=event_key,
            template_id=template_id,
            user_id=u.id,
            channel="inbox",
            title=title,
            content=body,
            status="failed",
            error_message=str(exc),
            ref_type=ref_type,
            ref_id=ref_id_s,
        )


async def _send_dingtalk(
    db: AsyncSession,
    *,
    u: User,
    title: str,
    body: str,
    event_key: str,
    template_id: int | None,
    max_per_day: int,
    ref_type: str,
    ref_id_s: str,
) -> bool:
    if not u.dingtalk_user_id:
        await _write_log(
            db,
            event_key=event_key,
            template_id=template_id,
            user_id=u.id,
            channel="dingtalk",
            title=title,
            content=body,
            status="skipped",
            error_message="no_dingtalk_userid",
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
        return False
    if max_per_day > 0:
        n = await _count_today_success(
            db,
            event_key=event_key,
            user_id=u.id,
            channel="dingtalk",
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
        if n >= max_per_day:
            await _write_log(
                db,
                event_key=event_key,
                template_id=template_id,
                user_id=u.id,
                channel="dingtalk",
                title=title,
                content=body,
                status="skipped",
                error_message="max_per_day",
                ref_type=ref_type,
                ref_id=ref_id_s,
            )
            return False
    try:
        await send_work_notice(u.dingtalk_user_id, title, body)
        await _write_log(
            db,
            event_key=event_key,
            template_id=template_id,
            user_id=u.id,
            channel="dingtalk",
            title=title,
            content=body,
            status="success",
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.exception("dingtalk notify fail user=%s", u.id)
        await _write_log(
            db,
            event_key=event_key,
            template_id=template_id,
            user_id=u.id,
            channel="dingtalk",
            title=title,
            content=body,
            status="failed",
            error_message=str(exc),
            ref_type=ref_type,
            ref_id=ref_id_s,
        )
        return False


async def dispatch_notification(
    db: AsyncSession,
    *,
    event_key: str,
    users: list[User],
    variables: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    ref_type: str = "",
    ref_id: str | int = "",
    fallback_title: str = "",
    fallback_body: str = "",
    respect_quiet: bool = True,
) -> dict[str, bool]:
    """按模板向多名用户发通知。返回是否至少一人钉钉实发成功。"""
    variables = dict(variables or {})
    payload = dict(payload or {})
    payload.setdefault("type", event_key)
    ref_id_s = str(ref_id or "")

    tpl = await get_template(db, event_key)
    if tpl is None:
        title = fallback_title or event_key
        body = fallback_body or ""
        channel_inbox = True
        channel_dingtalk = True
        template_id = None
        max_per_day = 0
        quiet_start = quiet_end = None
    else:
        if not tpl.is_enabled:
            for u in users:
                if not u:
                    continue
                await _write_log(
                    db,
                    event_key=event_key,
                    template_id=tpl.id,
                    user_id=u.id,
                    channel="inbox",
                    title="",
                    content="",
                    status="skipped",
                    error_message="template_disabled",
                    ref_type=ref_type,
                    ref_id=ref_id_s,
                )
            await db.flush()
            return {"dingtalk_notified": False}
        title = render_template(tpl.title_template, variables) or fallback_title
        body = render_template(tpl.body_template, variables) or fallback_body
        channel_inbox = bool(tpl.channel_inbox)
        channel_dingtalk = bool(tpl.channel_dingtalk)
        template_id = tpl.id
        max_per_day = int(tpl.max_per_day or 0)
        quiet_start, quiet_end = tpl.quiet_start, tpl.quiet_end

    now_local = datetime.now().time()
    if respect_quiet and in_quiet_hours(now_local, quiet_start, quiet_end):
        for u in users:
            if not u:
                continue
            await _write_log(
                db,
                event_key=event_key,
                template_id=template_id,
                user_id=u.id,
                channel="inbox",
                title=title,
                content=body,
                status="skipped",
                error_message="quiet_hours",
                ref_type=ref_type,
                ref_id=ref_id_s,
            )
        await db.flush()
        return {"dingtalk_notified": False}

    dingtalk_ok = False
    for u in users:
        if not u:
            continue
        if channel_inbox:
            await _send_inbox(
                db,
                u=u,
                title=title,
                body=body,
                payload=payload,
                event_key=event_key,
                template_id=template_id,
                max_per_day=max_per_day,
                ref_type=ref_type,
                ref_id_s=ref_id_s,
            )
        if channel_dingtalk:
            ok = await _send_dingtalk(
                db,
                u=u,
                title=title,
                body=body,
                event_key=event_key,
                template_id=template_id,
                max_per_day=max_per_day,
                ref_type=ref_type,
                ref_id_s=ref_id_s,
            )
            if ok:
                dingtalk_ok = True

    await db.flush()
    return {"dingtalk_notified": dingtalk_ok}


parse_hhmm = _parse_hhmm
