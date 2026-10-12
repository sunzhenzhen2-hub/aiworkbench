"""交办任务去重：空泛续推标题拦截 + 同人未完成相似任务匹配。"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.services.project_match import text_similarity
from app.services.report_ai_structure import (
    CONTINUATION_WARNING,
    VAGUE_CONTINUATION_WARNING,
    is_continuation_text,
    is_soft_continuation_text,
    is_vague_continuation,
    strip_continuation_prefix,
)

HARD_SIMILARITY = 0.85
SOFT_SIMILARITY = 0.55

Level = Literal["none", "soft", "hard"]

VAGUE_MESSAGE = (
    VAGUE_CONTINUATION_WARNING
    + "例如「继续推进零信任终端试点」。"
)
HARD_MESSAGE = "已有高度相似的未完成任务，请打开已有任务继续推进，避免重复建单。"
SOFT_MESSAGE = "疑似与已有未完成任务重复，请想清楚是否还要新建。"


def score_task_similarity(title: str, goal: str, other_title: str, other_goal: str = "") -> float:
    a = (title or "").strip()
    b = (other_title or "").strip()
    a_core = strip_continuation_prefix(a)
    b_core = strip_continuation_prefix(b)
    scores = [
        text_similarity(a, b),
        text_similarity(a_core, b_core),
    ]
    g = (goal or "").strip()
    og = (other_goal or "").strip()
    if g and og:
        scores.append(text_similarity(f"{a_core} {g}"[:200], f"{b_core} {og}"[:200]))
    elif g:
        scores.append(text_similarity(f"{a_core} {g}"[:200], b_core))
    return max(scores) if scores else 0.0


async def find_similar_open_tasks(
    db: AsyncSession,
    *,
    assignee_id: int,
    title: str,
    goal: str = "",
    project_id: int | None = None,
    exclude_id: int | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """同负责人未完成任务中，按相似度降序返回候选。"""
    q = select(Task).where(
        Task.assignee_id == assignee_id,
        Task.status != "completed",
    )
    if exclude_id is not None:
        q = q.where(Task.id != exclude_id)
    rows = list((await db.execute(q)).scalars().all())
    scored: list[dict[str, Any]] = []
    for t in rows:
        score = score_task_similarity(title, goal, t.title or "", t.goal or "")
        if project_id is not None and t.project_id == project_id and score > 0:
            score = min(1.0, score + 0.05)
        if score < SOFT_SIMILARITY:
            continue
        scored.append(
            {
                "id": t.id,
                "title": (t.title or "")[:120],
                "score": round(score, 4),
                "project_id": t.project_id,
                "status": t.status,
            }
        )
    scored.sort(key=lambda x: (-x["score"], x["id"]))
    return scored[:limit]


def classify_similar_level(similar: list[dict[str, Any]]) -> Level:
    if not similar:
        return "none"
    best = similar[0]["score"]
    if best >= HARD_SIMILARITY:
        return "hard"
    if best >= SOFT_SIMILARITY:
        return "soft"
    return "none"


async def check_task_duplicate(
    db: AsyncSession,
    *,
    assignee_id: int,
    title: str,
    goal: str = "",
    project_id: int | None = None,
    exclude_id: int | None = None,
) -> dict[str, Any]:
    vague = is_vague_continuation(title)
    similar: list[dict[str, Any]] = []
    if not vague:
        similar = await find_similar_open_tasks(
            db,
            assignee_id=assignee_id,
            title=title,
            goal=goal,
            project_id=project_id,
            exclude_id=exclude_id,
        )
    level: Level = "none"
    message = ""
    if vague:
        message = VAGUE_MESSAGE
    else:
        level = classify_similar_level(similar)
        if level == "hard":
            message = HARD_MESSAGE
        elif level == "soft":
            message = SOFT_MESSAGE
    return {
        "vague": vague,
        "similar": similar,
        "level": level,
        "message": message,
        "continuation_warning": CONTINUATION_WARNING
        if (is_continuation_text(title) or is_soft_continuation_text(title)) and not vague
        else "",
    }
