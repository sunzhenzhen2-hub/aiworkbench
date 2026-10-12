"""任务评价：LLM/规则生成建议，执行人本人可读。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models import Task, TaskReview
from app.core.config import settings
from app.services.ai_platform import run_structured
from app.services.report_quality import _is_vague

# 同一任务只允许一路后台 LLM 升级在飞，避免刷新连发
_upgrade_inflight: set[int] = set()

_DEBUG_LOG = "/home/aiworkbench/.cursor/debug-c7f782.log"


def _dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        import json
        import time

        with open(_DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "c7f782",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def rule_based_task_review(task: Task) -> dict[str, Any]:
    title = (task.title or "").strip() or "未命名任务"
    goal = (getattr(task, "goal", None) or "").strip()
    suggestions: list[str] = []
    strengths: list[str] = []
    risks: list[str] = []

    if goal and not _is_vague(goal):
        strengths.append("目标表述较具体，便于验收。")
    elif goal:
        risks.append("目标偏笼统，建议写清做成什么样/做到什么程度。")
        suggestions.append("把目标改成可验收的句子，例如「完成 X 并自测通过」。")
    else:
        risks.append("未填写目标，后续难判断是否完成。")
        suggestions.append("补充目标：做成什么样、做到什么程度。")

    if not task.due_date:
        suggestions.append("建议补上截止日期，方便排期与催办。")
    else:
        strengths.append("已设定截止日期。")

    pri = (getattr(task, "priority", None) or "p3").lower()
    if pri in ("p0", "p1"):
        strengths.append(f"级别为 {pri.upper()}，属于重点跟进。")

    if not strengths:
        strengths.append("任务已建档，可开始推进。")
    if not suggestions:
        suggestions.append("推进时及时更新结果与进度，便于周报汇总。")

    summary = f"「{title}」已建档。"
    if risks:
        summary += " " + risks[0]
    elif strengths:
        summary += " " + strengths[0]

    return {
        "summary": summary,
        "strengths": strengths[:5],
        "risks": risks[:5],
        "suggestions": suggestions[:5],
        "source": "rule",
        "model_name": "",
    }


async def generate_task_review(db: AsyncSession, task: Task) -> dict[str, Any]:
    if settings.agent_native:
        return rule_based_task_review(task)
    system = (
        "你是项目工作台任务辅导助手。根据任务信息给出简短评价 JSON（不要 markdown）："
        '{"summary":"一句总评","strengths":["优点"],"risks":["风险"],'
        '"suggestions":["可执行建议"]}。语气具体、可操作，面向执行人本人。'
    )
    user_msg = (
        f"标题：{task.title}\n"
        f"目标：{getattr(task, 'goal', '') or '（空）'}\n"
        f"级别：{getattr(task, 'priority', 'p3')}\n"
        f"截止日期：{task.due_date or '未设'}\n"
        f"状态：{task.status}\n"
        f"结果备注：{(task.result_text or '')[:300] or '（空）'}"
    )
    data, _resolved = await run_structured(
        db,
        purpose="task_review",
        system=system,
        user=user_msg,
        fallback_purposes=("task_draft", "general"),
    )
    if isinstance(data, dict) and (data.get("summary") or data.get("suggestions")):
        strengths = data.get("strengths") or []
        risks = data.get("risks") or []
        suggestions = data.get("suggestions") or []
        if not isinstance(strengths, list):
            strengths = []
        if not isinstance(risks, list):
            risks = []
        if not isinstance(suggestions, list):
            suggestions = []
        return {
            "summary": str(data.get("summary") or "").strip() or "已生成任务评价。",
            "strengths": [str(x).strip() for x in strengths if str(x).strip()][:6],
            "risks": [str(x).strip() for x in risks if str(x).strip()][:6],
            "suggestions": [str(x).strip() for x in suggestions if str(x).strip()][:6],
            "source": "llm",
            "model_name": "",
        }
    return rule_based_task_review(task)


def _apply_payload(row: TaskReview, payload: dict[str, Any]) -> None:
    row.summary = payload["summary"]
    row.strengths = payload["strengths"]
    row.risks = payload["risks"]
    row.suggestions = payload["suggestions"]
    row.source = payload["source"]
    row.model_name = payload.get("model_name") or ""


async def ensure_task_review(
    db: AsyncSession,
    task_id: int,
    *,
    force: bool = False,
) -> TaskReview | None:
    """获取或生成任务评价。

    默认（force=False）只写规则评价，避免打开详情时同步等 LLM。
    force=True 时走 LLM（失败则回退规则）。
    """
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.assignee))
        .where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        return None

    existing = await db.execute(
        select(TaskReview).where(TaskReview.task_id == task_id)
    )
    row = existing.scalar_one_or_none()
    if row and not force:
        return row

    # 首开详情：规则秒回；强制刷新才同步调 LLM
    payload = (
        await generate_task_review(db, task)
        if force
        else rule_based_task_review(task)
    )
    if row:
        _apply_payload(row, payload)
        await db.commit()
        await db.refresh(row)
        return row

    review = TaskReview(
        task_id=task.id,
        user_id=task.assignee_id,
        summary=payload["summary"],
        strengths=payload["strengths"],
        risks=payload["risks"],
        suggestions=payload["suggestions"],
        source=payload["source"],
        model_name=payload.get("model_name") or "",
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return review


async def upgrade_task_review_llm(task_id: int) -> None:
    """后台把规则评价升级为 LLM（独立会话；失败静默保留规则结果）。"""
    # #region agent log
    _dbg(
        "H2",
        "task_ai_review.py:upgrade_enter",
        "upgrade_task_review_llm enter",
        {"task_id": task_id, "inflight_size": len(_upgrade_inflight)},
    )
    # #endregion
    try:
        async with async_session() as db:
            result = await db.execute(
                select(TaskReview).where(TaskReview.task_id == task_id)
            )
            row = result.scalar_one_or_none()
            if row is None or (row.source or "") == "llm":
                # #region agent log
                _dbg(
                    "H2",
                    "task_ai_review.py:upgrade_skip",
                    "upgrade skip already llm or missing",
                    {
                        "task_id": task_id,
                        "has_row": row is not None,
                        "source": (row.source if row else None),
                    },
                )
                # #endregion
                return
            task_result = await db.execute(select(Task).where(Task.id == task_id))
            task = task_result.scalar_one_or_none()
            if task is None:
                return
            try:
                payload = await generate_task_review(db, task)
            except Exception:
                return
            if (payload.get("source") or "") != "llm":
                return
            _apply_payload(row, payload)
            await db.commit()
            # #region agent log
            _dbg(
                "H4",
                "task_ai_review.py:upgrade_done",
                "upgrade committed llm",
                {"task_id": task_id},
            )
            # #endregion
    finally:
        _upgrade_inflight.discard(task_id)


def try_mark_upgrade_scheduled(task_id: int) -> bool:
    """若该任务尚无在飞升级，标记并返回 True；已在飞则返回 False。"""
    if task_id in _upgrade_inflight:
        # #region agent log
        _dbg(
            "H2",
            "task_ai_review.py:dedupe_hit",
            "skip duplicate upgrade schedule",
            {"task_id": task_id},
        )
        # #endregion
        return False
    _upgrade_inflight.add(task_id)
    # #region agent log
    _dbg(
        "H2",
        "task_ai_review.py:dedupe_schedule",
        "mark upgrade scheduled",
        {"task_id": task_id, "inflight_size": len(_upgrade_inflight)},
    )
    # #endregion
    return True
