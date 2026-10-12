"""任务 AI：一句话草稿（意图/项目/人/级别）与拆解。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Project, ProjectMember, Task, User
from app.services.ai_platform import run_structured
from app.services.project_match import guess_project_id, split_project_and_task
from app.services.project_service import guess_project_for_user
from app.services.task_service import TASK_PRIORITIES


def _rule_task_draft(text: str, projects: list[dict[str, Any]], members: list[dict[str, Any]]) -> dict[str, Any]:
    raw = (text or "").strip()
    assign_hints = re.search(r"(交办|指派|给|让|请).{0,8}(跟进|处理|负责)", raw)
    intent = "delegated" if assign_hints else "self"
    pname, task_part = split_project_and_task(raw)
    title = (task_part or pname or raw)[:256]
    pid, _c, matched_name = guess_project_id(raw, "", projects, threshold=0.4)
    assignee_id = None
    for m in members:
        name = m.get("name") or ""
        if name and name in raw:
            assignee_id = m.get("id")
            break
    priority = "p3"
    if re.search(r"(紧急|重点|尽快|立刻|P0|p0)", raw):
        priority = "p0"
    elif re.search(r"(优先|关注|P1|p1)", raw):
        priority = "p1"
    elif re.search(r"(P2|p2)", raw):
        priority = "p2"
    elif re.search(r"(P4|p4|低优)", raw):
        priority = "p4"
    accept_by = None
    if intent == "delegated":
        accept_by = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    return {
        "assignment_type": intent,
        "title": title or raw[:256],
        "goal": "",
        "project_id": pid,
        "project_name": matched_name,
        "assignee_id": assignee_id,
        "assistant_ids": [],
        "priority": priority,
        "accept_by": accept_by,
        "due_date": None,
        "confidence": 0.45,
        "rationale": "规则兜底解析",
    }


async def draft_task_from_text(
    db: AsyncSession,
    *,
    user: User,
    text: str,
) -> dict[str, Any]:
    memberships = await db.execute(
        select(ProjectMember, Project)
        .join(Project, Project.id == ProjectMember.project_id)
        .where(ProjectMember.user_id == user.id)
    )
    projects: list[dict[str, Any]] = []
    project_ids: list[int] = []
    for _m, p in memberships.all():
        projects.append({"project_id": p.id, "id": p.id, "name": p.name})
        project_ids.append(p.id)

    members: list[dict[str, Any]] = []
    if project_ids:
        rows = await db.execute(
            select(ProjectMember)
            .options(selectinload(ProjectMember.user))
            .where(ProjectMember.project_id.in_(project_ids))
        )
        seen: set[int] = set()
        for m in rows.scalars().all():
            if m.user_id in seen or not m.user:
                continue
            seen.add(m.user_id)
            members.append({"id": m.user.id, "name": m.user.name})

    fallback = _rule_task_draft(text, projects, members)
    ctx_id, ctx_conf, ctx_name, ctx_reason = await guess_project_for_user(
        db, user.id, text, member_only=True
    )
    if ctx_id and ctx_id in set(project_ids) and ctx_conf >= 0.55:
        fallback["project_id"] = ctx_id
        fallback["project_name"] = ctx_name
        if ctx_reason:
            fallback["rationale"] = ctx_reason
    system = (
        "你是项目工作台助手。根据用户一句话，输出 JSON（不要 markdown）："
        '{"assignment_type":"self|delegated","title":"...","goal":"做成什么样",'
        '"project_id":null或数字,'
        '"assignee_id":null或数字,"assistant_ids":[],"priority":"p0|p1|p2|p3|p4",'
        '"accept_by":ISO8601或null,"due_date":"YYYY-MM-DD或null","rationale":"一句说明"}。'
        "self=自己做；delegated=交办他人。优先匹配给出的项目与成员。"
        "goal 写清验收标准；人能少填就少填：能猜到就猜。"
    )
    user_msg = (
        f"用户说：{text}\n"
        f"可选项目：{projects[:40]}\n"
        f"可选成员：{members[:60]}\n"
        "只输出 JSON。"
    )
    data, source = await run_structured(
        db, purpose="task_draft", system=system, user=user_msg, fallback_purposes=("report_structure",)
    )
    if not data:
        fallback["source"] = source
        return fallback

    out = {**fallback, **{k: v for k, v in data.items() if v is not None}}
    out["assignment_type"] = (
        "delegated" if out.get("assignment_type") == "delegated" else "self"
    )
    if out.get("priority") not in TASK_PRIORITIES:
        out["priority"] = "p3"
    title = (out.get("title") or text).strip()[:256]
    out["title"] = title or text[:256]
    out["goal"] = str(out.get("goal") or fallback.get("goal") or "").strip()[:2000]
    # 校验 project_id / assignee_id 在候选内
    pids = {p["project_id"] for p in projects}
    if out.get("project_id") not in pids:
        out["project_id"] = fallback.get("project_id")
    mids = {m["id"] for m in members}
    if out.get("assignee_id") not in mids:
        out["assignee_id"] = fallback.get("assignee_id") if out["assignment_type"] == "delegated" else None
    out["source"] = source
    out["confidence"] = 0.8 if source == "llm" else 0.45
    return out


async def breakdown_task(db: AsyncSession, task: Task) -> dict[str, Any]:
    system = (
        "把任务拆成 3～7 条可执行小步骤。输出 JSON："
        '{"items":[{"title":"...","done":false}]}。标题短、可勾选完成。不要 markdown。'
    )
    user_msg = f"任务：{task.title}\n当前说明：{(task.result_text or '')[:500]}\n只输出 JSON。"
    data, source = await run_structured(
        db,
        purpose="task_breakdown",
        system=system,
        user=user_msg,
        fallback_purposes=("report_structure",),
    )
    if data and isinstance(data.get("items"), list) and data["items"]:
        items = []
        for i, it in enumerate(data["items"][:12]):
            if isinstance(it, dict):
                t = str(it.get("title") or "").strip()
            else:
                t = str(it).strip()
            if t:
                items.append({"title": t[:200], "done": False, "sort_order": i})
        return {"items": items, "source": source}
    # 规则兜底：按顿号/分号切
    parts = re.split(r"[；;、\n]|然后|接着|并", task.title)
    items = []
    for i, p in enumerate(parts):
        p = p.strip(" ，,。.")
        if len(p) >= 2:
            items.append({"title": p[:200], "done": False, "sort_order": i})
    if not items:
        items = [{"title": "梳理现状", "done": False, "sort_order": 0},
                 {"title": "推进处理", "done": False, "sort_order": 1},
                 {"title": "确认结果并反馈", "done": False, "sort_order": 2}]
    return {"items": items[:7], "source": "rule"}


def _rule_polish_goal(*, title: str, text: str) -> str:
    raw = (text or "").strip()
    t = (title or "").strip() or "该任务"
    if not raw:
        return f"完成「{t}」，做到可核对、可验收（明确交付物或通过标准）。"
    # 轻量整理：压空白、补成一句可验收表述
    cleaned = re.sub(r"\s+", " ", raw)
    if len(cleaned) < 8 and t not in cleaned:
        return f"完成「{t}」：{cleaned}（写清做到什么程度即可验收）。"
    if not re.search(r"(完成|达成|通过|交付|验收|确认)", cleaned):
        return f"{cleaned}（做成可验收的结果并自测/确认通过）。"
    return cleaned


def _rule_polish_result(*, title: str, goal: str, text: str) -> str:
    raw = (text or "").strip()
    t = (title or "").strip() or "该任务"
    if not raw:
        g = (goal or "").strip()
        if g:
            return f"已按目标推进「{t}」：{g[:80]}（请补充实际做成了什么）。"
        return f"已完成「{t}」相关工作（请补充具体交付与结果）。"
    cleaned = re.sub(r"\s+", " ", raw)
    if len(cleaned) < 6:
        return f"已完成：{cleaned}（建议补一句做成了什么、做到什么程度）。"
    return cleaned


async def polish_task_field(
    db: AsyncSession,
    *,
    task: Task,
    field: str,
    text: str | None = None,
) -> dict[str, Any]:
    """润色目标或结果说明。返回 {field, text, source}，不自动落库。"""
    if field not in {"goal", "result_text"}:
        raise ValueError("field 仅支持 goal 或 result_text")

    title = (task.title or "").strip()
    goal = (getattr(task, "goal", None) or "").strip()
    current = (text if text is not None else getattr(task, field, None) or "").strip()

    if field == "goal":
        fallback = _rule_polish_goal(title=title, text=current)
        system = (
            "你是任务目标润色助手。把用户写的目标改成一句（或两三句）清晰、可验收的表述。"
            "要求：具体、可核对；避免空话（如推进中、跟进一下）。"
            '只输出 JSON：{"text":"..."}。不要 markdown。'
        )
        user_msg = (
            f"任务标题：{title}\n"
            f"原目标：{current or '（空）'}\n"
            "请润色目标（做成什么样/做到什么程度）。只输出 JSON。"
        )
    else:
        fallback = _rule_polish_result(title=title, goal=goal, text=current)
        system = (
            "你是任务结果说明润色助手。把用户写的结果改成清楚、可核对的「做成了什么」。"
            "要求：写清结果与程度；不要编造用户没写的事实；可略微补全表达。"
            '只输出 JSON：{"text":"..."}。不要 markdown。'
        )
        user_msg = (
            f"任务标题：{title}\n"
            f"目标：{goal or '（空）'}\n"
            f"原结果说明：{current or '（空）'}\n"
            "请润色结果说明。只输出 JSON。"
        )

    data, source = await run_structured(
        db,
        purpose="task_polish",
        system=system,
        user=user_msg,
        fallback_purposes=("task_draft", "report_structure"),
    )
    polished = ""
    if data and data.get("text") is not None:
        polished = str(data.get("text") or "").strip()
    if not polished:
        polished = fallback
        source = "rule" if source != "llm" else source
        if not data:
            source = "rule"
    max_len = 2000 if field == "goal" else 5000
    return {"field": field, "text": polished[:max_len], "source": source}