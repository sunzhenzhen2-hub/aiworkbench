"""周报/日报轻量三问：硬拦三条，其余软提示。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


VAGUE_PATTERNS = [
    r"^跟进",
    r"跟进项目进度",
    r"^推进中",
    r"^处理中",
    r"^持续中",
    r"^本周无进度",
    r"^无进展",
    r"^无$",
    r"^正常$",
    r"^/$",
    r"^——$",
    r"^-$",
]

RISK_HINT_KEYWORDS = [
    "滞后",
    "停机",
    "离线",
    "超期",
    "延期",
    "卡住",
    "未恢复",
    "无进展",
    "等设备",
    "等客户",
]


def _is_vague(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if len(t) < 4 and t in {"跟进", "推进", "处理", "配合", "正常", "无"}:
        return True
    for pat in VAGUE_PATTERNS:
        if re.search(pat, t):
            return True
    return False


def _has_digit(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def validate_report_items(items: list[dict[str, Any]]) -> tuple[bool, list[str], list[str]]:
    """返回 (是否可提交, 硬错误列表, 软提示列表)。"""
    hard: list[str] = []
    soft: list[str] = []

    if not items:
        hard.append("请至少添加一条任务卡片")
        return False, hard, soft

    active = [it for it in items if not it.get("no_action")]
    if not active and not any(it.get("no_action") for it in items):
        hard.append("请至少填写一条有实质动作的任务")
        return False, hard, soft

    for idx, it in enumerate(items, start=1):
        label = f"第{idx}条"
        title = (it.get("task_title") or "").strip()

        if it.get("no_action"):
            if not title:
                hard.append(f"{label}：请填写任务概述（即使无实质动作也方便以后归类）")
            if not it.get("project_id"):
                hard.append(f"{label}：请选择所属项目（杂项请选「日常任务」）")
            continue

        if not title:
            hard.append(f"{label}：请填写任务概述")

        if not it.get("project_id"):
            hard.append(f"{label}：请选择所属项目（杂项请选「日常任务」）")

        result = (it.get("result_text") or "").strip()
        if _is_vague(result):
            hard.append(f"{label}：「做成了什么」不能为空话，请写可核对的结果")

        quant = (it.get("quant_value") or "").strip()
        if not quant and not _has_digit(result):
            soft.append(f"{label}：建议补充一个量化数字（路数/百分比/份数等）")

        stuck = it.get("stuck_status") or "none"
        if stuck in ("blocker", "coordination"):
            if not (it.get("stuck_next_action") or "").strip():
                hard.append(f"{label}：有卡点/需协调时须填写下一步")
            if not it.get("stuck_follow_up"):
                hard.append(f"{label}：有卡点/需协调时须填写下次跟进日")
            if not (it.get("stuck_detail") or "").strip():
                hard.append(f"{label}：请用一句话说明卡在哪里")

        next_goal = (it.get("next_goal") or "").strip()
        if _is_vague(next_goal) or not next_goal:
            hard.append(f"{label}：请写清下一步目标（不能只写跟进/推进）")
        if not it.get("next_due_date"):
            hard.append(f"{label}：请填写下一步计划完成日")

        risk = it.get("risk_level") or "normal"
        if risk == "escalate":
            if not (it.get("risk_coordinator") or "").strip():
                hard.append(f"{label}：「需升级」须填写协调对象")
            if not it.get("risk_impact_date"):
                hard.append(f"{label}：「需升级」须填写影响日期")
            if not (it.get("risk_reason") or "").strip():
                hard.append(f"{label}：「需升级」须填写原因")

        blob = f"{result} {it.get('stuck_detail') or ''}"
        if risk == "normal" and any(k in blob for k in RISK_HINT_KEYWORDS):
            soft.append(f"{label}：表述里像有风险/卡点，建议标「需关注」或「需升级」，或补充卡点")

    return len(hard) == 0, hard, soft


def monday_of(d: date) -> date:
    return d.fromordinal(d.toordinal() - (d.weekday()))


def sunday_of(d: date) -> date:
    return d.fromordinal(monday_of(d).toordinal() + 6)


def compose_theme_from_titles(items: list[dict[str, Any]], max_len: int = 80) -> str:
    titles = [(it.get("task_title") or "").strip() for it in items if (it.get("task_title") or "").strip()]
    if not titles:
        return ""
    joined = "；".join(titles)
    return joined[:max_len]
