"""管理者侧周报一阶段评价：LLM 生成并落库，无模型时规则兜底。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import ProgressReport, ProgressReportReview
from app.services.ai_platform import run_structured
from app.services.report_quality import _is_vague


def rule_based_review(report: ProgressReport) -> dict[str, Any]:
    """无 LLM 时的一阶段简要评价。"""
    items = [it for it in (report.items or []) if not it.no_action]
    strengths: list[str] = []
    risks: list[str] = []
    escalate = 0
    blockers = 0
    prior_yes = 0
    prior_no = 0
    prior_total = 0
    vague = 0

    for it in items:
        title = (it.task_title or "").strip() or "未命名任务"
        if it.quant_value or (it.result_text and not _is_vague(it.result_text or "")):
            strengths.append(f"「{title}」有具体结果")
        if _is_vague(it.result_text or ""):
            vague += 1
        if it.risk_level == "escalate":
            escalate += 1
            reason = (it.risk_reason or "")[:40]
            risks.append(f"「{title}」需升级：{reason}")
        elif it.risk_level == "watch":
            reason = (it.risk_reason or "")[:40]
            risks.append(f"「{title}」需关注：{reason}")
        if it.stuck_status in ("blocker", "coordination"):
            blockers += 1
            detail = (it.stuck_detail or "")[:40]
            risks.append(f"「{title}」有卡点：{detail}")
        if it.prior_done is True:
            prior_yes += 1
            prior_total += 1
        elif it.prior_done is False:
            prior_no += 1
            prior_total += 1

    if not items:
        summary = "本期几乎无实质任务卡，建议核实是否漏报。"
    elif escalate or blockers:
        summary = f"本期 {len(items)} 项任务中，有 {escalate} 项需升级、{blockers} 项卡点，需跟进协调。"
    elif vague:
        summary = f"本期 {len(items)} 项任务已提交，但有 {vague} 项结果偏笼统，完成度可读性一般。"
    else:
        summary = f"本期提交 {len(items)} 项任务，表述较清楚，风险可控。"

    if prior_total:
        rate = round(100 * prior_yes / prior_total)
        completion = f"上期目标兑现：{prior_yes}/{prior_total}（约 {rate}%）。"
        if prior_no:
            completion += f" 有 {prior_no} 项未兑现，建议在下期说明原因。"
    else:
        completion = "本期未标注上期目标兑现情况，历史完成度暂无法统计。"

    # 去重并截断
    def _uniq(rows: list[str], n: int = 5) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in rows:
            x = (x or "").strip()
            if not x or x in seen:
                continue
            seen.add(x)
            out.append(x)
            if len(out) >= n:
                break
        return out

    if not strengths and items:
        strengths.append("已按时提交周报，便于管理者跟进。")

    return {
        "summary": summary,
        "strengths": _uniq(strengths),
        "risks": _uniq(risks),
        "completion_comment": completion,
        "source": "rule",
        "model_name": "",
    }


def _items_payload(report: ProgressReport) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, it in enumerate(report.items or [], start=1):
        if it.no_action:
            continue
        rows.append(
            {
                "序号": idx,
                "任务概述": (it.task_title or "")[:80],
                "做成了什么": (it.result_text or "")[:200],
                "量化": it.quant_value or "",
                "卡点": it.stuck_status,
                "卡点说明": (it.stuck_detail or "")[:120],
                "下周目标": (it.next_goal or "")[:120],
                "完成日": str(it.next_due_date) if it.next_due_date else "",
                "风险": it.risk_level,
                "上期兑现": it.prior_done,
            }
        )
    return rows


async def generate_phase1_review(
    db: AsyncSession, report: ProgressReport
) -> dict[str, Any]:
    """调用 LLM 或规则生成评价内容（不落库）。"""
    if settings.agent_native:
        return rule_based_review(report)
    system = (
        "你是中小团队管理者的周报审阅助手。根据员工已提交周报，写一阶段简要评价。"
        "只输出 JSON，键：summary(字符串，2～4句)、strengths(字符串数组)、"
        "risks(字符串数组)、completion_comment(字符串，评论历史任务完成/兑现)。"
        "语气客观、白话，不要英文字段名，不要编造未出现的事实。"
    )
    user_msg = (
        f"周期：{report.period_type} {report.period_start}～{report.period_end or report.period_start}\n"
        f"主题：{report.theme or '无'}\n"
        f"任务：{ _items_payload(report) }\n"
        "请输出 JSON。"
    )
    data, source = await run_structured(
        db,
        purpose="report_review",
        system=system,
        user=user_msg,
        fallback_purposes=("overview_brief", "report_coach"),
        max_tokens=800,
    )
    if source == "llm" and isinstance(data, dict):
        strengths = data.get("strengths") or []
        risks = data.get("risks") or []
        if not isinstance(strengths, list):
            strengths = [str(strengths)]
        if not isinstance(risks, list):
            risks = [str(risks)]
        return {
            "summary": str(data.get("summary") or "").strip() or "已生成评价，但摘要为空。",
            "strengths": [str(x).strip() for x in strengths if str(x).strip()][:6],
            "risks": [str(x).strip() for x in risks if str(x).strip()][:6],
            "completion_comment": str(data.get("completion_comment") or "").strip(),
            "source": "llm",
            "model_name": "",
        }
    return rule_based_review(report)


async def ensure_phase1_review(
    db: AsyncSession,
    report_id: int,
    *,
    force: bool = False,
) -> ProgressReportReview | None:
    """有则返回；无则生成并落库。force 时覆盖 phase=1。报告不存在返回 None。"""
    result = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items), selectinload(ProgressReport.author))
        .where(ProgressReport.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        return None
    if report.status != "submitted":
        return None

    existing = await db.execute(
        select(ProgressReportReview).where(
            ProgressReportReview.report_id == report_id,
            ProgressReportReview.phase == 1,
        )
    )
    row = existing.scalar_one_or_none()
    if row and not force:
        return row

    payload = await generate_phase1_review(db, report)
    if row and force:
        row.summary = payload["summary"]
        row.strengths = payload["strengths"]
        row.risks = payload["risks"]
        row.completion_comment = payload["completion_comment"]
        row.source = payload["source"]
        row.model_name = payload.get("model_name") or ""
        await db.commit()
        await db.refresh(row)
        return row

    review = ProgressReportReview(
        report_id=report.id,
        user_id=report.user_id,
        period_type=report.period_type,
        period_start=report.period_start,
        phase=1,
        summary=payload["summary"],
        strengths=payload["strengths"],
        risks=payload["risks"],
        completion_comment=payload["completion_comment"],
        source=payload["source"],
        model_name=payload.get("model_name") or "",
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review
