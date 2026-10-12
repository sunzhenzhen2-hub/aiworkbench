"""通知默认模板种子（按 event_key 仅插入缺失行，不覆盖管理员已改配置）。"""

from __future__ import annotations

from datetime import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NotificationTemplate

# (event_key, name, description, title, body, cron, max_per_day)
DEFAULT_TEMPLATES: list[dict] = [
    {
        "event_key": "task_assigned",
        "name": "交办任务",
        "description": "管理者交办任务给责任人时",
        "title_template": "收到交办任务，请接收",
        "body_template": "{actor_name} 交办了「{task_title}」，请打开工作台点「接收」后再更新进度。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "task_accepted",
        "name": "任务已接收",
        "description": "责任人接收交办任务后通知交办人",
        "title_template": "交办任务已接收",
        "body_template": "{actor_name} 已接收「{task_title}」。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "task_remind",
        "name": "催办接收",
        "description": "催促责任人尽快接收交办任务",
        "title_template": "请尽快接收交办任务",
        "body_template": "{actor_name} 催办：请尽快接收「{task_title}」。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "task_completed",
        "name": "交办完成",
        "description": "交办任务完成后通知交办人",
        "title_template": "交办任务已完成",
        "body_template": "{actor_name} 已完成「{task_title}」。{result_summary}",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "task_priority_p0",
        "name": "P0 重点任务",
        "description": "任务设为 P0 时提醒责任人",
        "title_template": "收到 P0 重点任务",
        "body_template": "{actor_name} 将任务「{task_title}」设为 {priority_label}，请优先跟进。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "project_role_assigned",
        "name": "项目角色指派",
        "description": "新建项目时指定项目经理或项目支撑",
        "title_template": "你被指定为{role_label}",
        "body_template": "{actor_name} 新建了项目「{project_name}」，你是{role_label}。请打开工作台查看。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "blocker_declared",
        "name": "辅助人申报卡点",
        "description": "辅助人从周报申报卡点后通知项目负责人",
        "title_template": "辅助人申报了新卡点",
        "body_template": "「{project_name}」：{blocker_desc}，请在周报看板确认。",
        "schedule_cron": "",
        "max_per_day": 0,
    },
    {
        "event_key": "blocker_follow_up_overdue",
        "name": "卡点跟进到期",
        "description": "卡点下次跟进日已过且未解决",
        "title_template": "卡点跟进逾期提醒",
        "body_template": "【提醒】卡点「{blocker_desc}」下次跟进时间已到（{next_follow_up}），请及时更新状态。",
        "schedule_cron": "0 9 * * *",
        "max_per_day": 1,
    },
    {
        "event_key": "phase_overdue",
        "name": "阶段计划逾期",
        "description": "阶段计划完成日已过且未完成",
        "title_template": "阶段逾期提醒",
        "body_template": "【提醒】项目「{project_name}」的阶段「{phase_name}」已过计划完成日 {planned_end}，请尽快推进。",
        "schedule_cron": "0 9 * * *",
        "max_per_day": 1,
    },
    {
        "event_key": "weekly_report_overdue",
        "name": "周报未交提醒",
        "description": "名单内成员上周周报未提交",
        "title_template": "周报逾期提醒",
        "body_template": "【提醒】上周周报（{period_start} ~ {period_end}）还未提交，请于今天 18:00 前完成。",
        "schedule_cron": "0 12 * * 1",
        "max_per_day": 1,
    },
]


async def seed_notification_templates(db: AsyncSession) -> int:
    """插入缺失的默认模板，返回新增条数。"""
    created = 0
    for cfg in DEFAULT_TEMPLATES:
        result = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.event_key == cfg["event_key"]
            )
        )
        if result.scalar_one_or_none():
            continue
        db.add(
            NotificationTemplate(
                event_key=cfg["event_key"],
                name=cfg["name"],
                description=cfg["description"],
                title_template=cfg["title_template"],
                body_template=cfg["body_template"],
                channel_inbox=True,
                channel_dingtalk=True,
                is_enabled=True,
                schedule_cron=cfg["schedule_cron"],
                max_per_day=cfg["max_per_day"],
                quiet_start=time(22, 0) if cfg["schedule_cron"] else None,
                quiet_end=time(8, 0) if cfg["schedule_cron"] else None,
            )
        )
        created += 1
    if created:
        await db.flush()
    return created
