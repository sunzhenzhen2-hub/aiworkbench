"""周报「下周计划」LLM 规范化，并同步为本人自建待办。

管理员侧通过「部门待办」汇总查看成员待办，不再复制镜像任务。
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProgressReport, ProgressReportItem, Project, ProjectMember, Task, TaskEvent, User
from app.services.llm_resolver import resolve_llm
from app.services.phase_match import guess_phase_id
from app.services.project_match import looks_like_daily_work
from app.services.project_service import (
    build_project_context,
    ensure_daily_project,
    guess_text_in_bundle,
)
from app.services.report_ai_structure import (
    is_continuation_text,
    is_soft_continuation_text,
    is_vague_continuation,
    strip_continuation_prefix,
)

logger = logging.getLogger(__name__)

SOURCE_REPORT_NEXT = "report_next"
SOURCE_REPORT_DONE = "report_done"

# 待办标题可读长度（库字段上限 256）
_TITLE_MAX = 80


@dataclass
class NormalizedNextPlan:
    title: str
    success_criteria: str
    due_date: date | None


def _item_source_ref(item: ProgressReportItem) -> str:
    if item.chain_id:
        return f"chain:{item.chain_id}"
    return f"idx:{item.sort_order}"


def _short_project_hint(name: str, max_len: int = 18) -> str:
    """项目名缩略，去掉括号说明；日常任务不加。"""
    n = (name or "").strip()
    if not n or n == "日常任务":
        return ""
    n = re.sub(r"[（(][^）)]*[）)]", "", n).strip()
    n = re.sub(r"\s+", "", n)
    return n[:max_len]


def _is_template_continuation(goal: str, prior: str = "") -> bool:
    """识别「继续推进「xxx」相关工作」等空壳下一步。"""
    g = (goal or "").strip()
    if not g:
        return False
    if re.match(r'^继续推进[「"][^」"]+[」"]相关工作。?$', g):
        return True
    if re.match(r"^继续推进.+相关工作。?$", g) and (
        not prior or prior in g or f"「{prior}」" in g or f"\"{prior}\"" in g
    ):
        return True
    return False


_NEXT_PLACEHOLDERS = frozenset(
    {
        "原文未写下周计划",
        "下周",
        "预计下周",
        "待定",
        "无",
        "暂无",
        "日常工作",
        "日常",
        "无下一步",
        "暂无计划",
    }
)
_RESULT_DONE_HINT = re.compile(
    r"(已完成|测试已完成|已办结|已交付|已提交|已交|已上线|已出|已给到|已交给)"
)


def _norm_for_cmp(text: str) -> str:
    s = re.sub(r"\s+", "", (text or "").strip().lower())
    return re.sub(r"[，。、；;：:！!？?\.\,·\-_/／]+", "", s)


def _text_similar(a: str, b: str, threshold: float = 0.72) -> bool:
    na, nb = _norm_for_cmp(a), _norm_for_cmp(b)
    if not na or not nb:
        return False
    if na == nb or na in nb or nb in na:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


def should_skip_next_plan_item(
    *,
    next_goal: str | None,
    result_text: str | None = None,
    task_title: str | None = None,
) -> bool:
    """
    真正的「下一步」才同步为未完成待办。
    空壳占位、或下一步只是复述本周已做成的总结 → 不建/不保留未完成待办。
    """
    goal = (next_goal or "").strip()
    if not goal or goal in _NEXT_PLACEHOLDERS:
        return True
    if len(goal) <= 4 and goal in {"下周做", "下周定", "继续", "推进", "跟进"}:
        return True
    result = (result_text or "").strip()
    title = (task_title or "").strip()
    if result and _text_similar(goal, result):
        return True
    # 「下一步」写成「完成xxx」，而本周结果已声明完成同类事项 → 当作总结而非待办
    if goal.startswith("完成") and result and _RESULT_DONE_HINT.search(result):
        if title and (_norm_for_cmp(title) in _norm_for_cmp(goal) or _text_similar(goal, title)):
            return True
        if _text_similar(goal, result, threshold=0.6):
            return True
    return False


def _next_actions_from_result(result_text: str, max_len: int = 36) -> str:
    """从「做成了什么」里抽未完成/待跟进要点，作标题补充。"""
    raw = (result_text or "").strip()
    if not raw:
        return ""
    lines = [ln.strip() for ln in re.split(r"[\n；;]", raw) if ln.strip()]
    tips: list[str] = []
    for ln in lines:
        ln = re.sub(r"^[\d一二三四五六七八九十]+[、.．]?\s*", "", ln)
        if not ln:
            continue
        if re.search(r"(已完成|已办结|已交付|已上线)$", ln) and not re.search(
            r"(未|待|还需|跟进|推进)", ln
        ):
            continue
        # 取首句，去掉「已…」中间态，突出未完成
        head = re.split(r"[，,]", ln, maxsplit=1)[0].strip()
        head = re.sub(r"已[^，,]{1,12}", "", head)
        head = re.sub(r"\s+", "", head)
        if re.search(r"(未|待|还需|并发|对接|收集|调试)", ln) and not head.startswith("跟进"):
            tip = f"跟进{head}" if head else ln[:20]
        else:
            tip = head or ln[:20]
        tip = tip[:22]
        if tip and tip not in tips:
            tips.append(tip)
        if len(tips) >= 2:
            break
    if not tips:
        return ""
    return "、".join(tips)[:max_len]


def compose_clear_task_title(
    *,
    task_title: str,
    next_goal: str,
    project_name: str = "",
    result_text: str = "",
    max_len: int = _TITLE_MAX,
) -> str:
    """
    周报同步待办标题：下一步为主，概述/项目补上下文，避免「完善采购」这类无头标题。
    """
    goal = (next_goal or "").strip()
    prior = (task_title or "").strip()
    hint = _short_project_hint(project_name)
    core_goal = strip_continuation_prefix(goal).strip() if goal else ""
    soft_only = is_soft_continuation_text(goal) and not is_continuation_text(goal)
    template = _is_template_continuation(goal, prior)
    from_result = _next_actions_from_result(result_text)

    if template or is_vague_continuation(goal) or (goal and len(re.sub(r"\s+", "", core_goal)) < 4):
        if prior and from_result:
            action = f"{prior}：{from_result}"
        elif prior:
            action = f"继续推进「{prior}」" if template or is_vague_continuation(goal) else prior
        elif from_result:
            action = from_result
        else:
            action = goal or "下周跟进"
    elif soft_only and prior and prior != goal:
        # 套话「继续政务外网运维…」不抢标题，保留更具体的概述
        action = f"{prior}：{from_result}" if from_result else prior
    elif prior and goal and prior != goal and prior not in goal and goal not in prior:
        # 概述与下一步不同：拼成「概述：下一步」
        piece = core_goal if (
            is_continuation_text(goal) or is_soft_continuation_text(goal)
        ) else goal
        action = f"{prior}：{piece}"
    else:
        action = goal or prior or "下周跟进"
        if from_result and (template or action == prior or len(action) < 16):
            action = f"{prior or hint or '跟进'}：{from_result}"

    # 标题仍缺项目语境时补项目简称（标题里已有则不加）
    if hint and hint not in action:
        use_hint = True
        if len(hint) >= 6 and hint[:6] in action:
            use_hint = False
        if use_hint and (
            len(action) < 28
            or is_continuation_text(action)
            or is_soft_continuation_text(action)
            or is_vague_continuation(action)
            or template
            or (prior and prior == goal)
        ):
            action = f"{hint} · {action}"

    action = re.sub(r"\s+", " ", action).strip(" ：:")
    return (action or "下周跟进")[:max_len]


def _rule_normalize(
    *,
    task_title: str,
    next_goal: str,
    next_due_date: date | None,
    project_name: str = "",
    result_text: str = "",
) -> NormalizedNextPlan:
    """下一步待办：标题带概述/项目/未完成要点；目标尽量写清可验收事项。"""
    goal = (next_goal or "").strip()
    prior = (task_title or "").strip()
    title = compose_clear_task_title(
        task_title=prior,
        next_goal=goal,
        project_name=project_name,
        result_text=result_text,
    )
    from_result = _next_actions_from_result(result_text, max_len=80)
    if _is_template_continuation(goal, prior) or is_vague_continuation(goal):
        if from_result:
            tip = from_result[2:] if from_result.startswith("跟进") else from_result
            criteria = f"跟进并完成：{tip}"
        elif prior:
            criteria = f"推进「{prior}」并形成可验收产出（勿仅写「继续推进」）"
        else:
            criteria = "推进并确认结果"
    else:
        criteria = goal or (f"推进「{prior}」并确认结果" if prior else "推进并确认结果")
    return NormalizedNextPlan(title=title, success_criteria=criteria[:2000], due_date=next_due_date)


async def _resolve_item_project_id(
    db: AsyncSession,
    *,
    item: ProgressReportItem,
    author: User,
    daily_id: int,
    classify_bundle: dict[str, Any] | None = None,
) -> int:
    """
    同步任务时的项目归属：
    - 条目已选真实项目 → 用该项目
    - 空或「日常任务」且文字不像杂事 → 再猜一次，并写回条目，避免任务掉进日常筐
    """
    project_id = item.project_id
    title = (item.task_title or "").strip()
    result = (item.result_text or "").strip()
    next_goal = (item.next_goal or "").strip()
    if project_id and project_id != daily_id:
        return project_id
    if looks_like_daily_work(title, result):
        return project_id or daily_id
    bundle = classify_bundle or await build_project_context(db)
    guessed, _conf, _name, _reason = guess_text_in_bundle(
        bundle, author.id, title or next_goal, f"{result} {next_goal}".strip()
    )
    if guessed and guessed != daily_id:
        item.project_id = guessed
        return guessed
    return project_id or daily_id


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_due(raw: Any, fallback: date | None) -> date | None:
    if isinstance(raw, date):
        return raw
    s = str(raw or "").strip()
    if not s or s.lower() in ("null", "none"):
        return fallback
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return fallback


async def normalize_next_plans_batch(
    db: AsyncSession,
    *,
    period_end: date | None,
    items: list[ProgressReportItem],
    use_llm: bool = True,
) -> dict[int, NormalizedNextPlan]:
    """item.id -> 规范化结果；LLM 失败或关闭时规则兜底。"""
    actionable = [
        it
        for it in items
        if not it.no_action
        and (it.next_goal or "").strip()
        and it.next_due_date
        and not should_skip_next_plan_item(
            next_goal=it.next_goal,
            result_text=it.result_text,
            task_title=it.task_title,
        )
    ]
    out: dict[int, NormalizedNextPlan] = {
        it.id: _rule_normalize(
            task_title=it.task_title,
            next_goal=it.next_goal,
            next_due_date=it.next_due_date,
            result_text=it.result_text or "",
        )
        for it in actionable
    }
    if not actionable or not use_llm:
        return out

    llm = await resolve_llm(db, "report_structure") or await resolve_llm(db, "general")
    if not llm:
        return out

    payload = [
        {
            "id": it.id,
            "task_title": (it.task_title or "")[:120],
            "result_text": (it.result_text or "")[:300],
            "next_goal": (it.next_goal or "")[:500],
            "next_due_date": it.next_due_date.isoformat() if it.next_due_date else None,
        }
        for it in actionable
    ]
    system = (
        "你是周报「下周计划」规范化助手。把每条计划整理成待办任务字段。\n"
        "只输出 JSON：{\"plans\":[{\"id\":数字,\"title\":\"做什么≤80字\","
        "\"success_criteria\":\"做成什么样或做多少（一句话，原文无数量则不要编造）\","
        "\"due_date\":\"YYYY-MM-DD\"}]}\n"
        "规则：title 以 next_goal（下一步）为主，若 next_goal 空泛（如「继续推进」）"
        "或缺少对象，必须带上 task_title（本周概述）写成「概述：下一步」；"
        "不要只用已完成结果当标题；保留原意；不编造数量；"
        "due_date 优先用输入的 next_due_date；必须覆盖输入的每个 id。"
    )
    user_msg = (
        f"本周期结束日：{(period_end or date.today()).isoformat()}\n"
        f"计划列表：\n{json.dumps(payload, ensure_ascii=False)}"
    )
    try:
        url = llm.api_base.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {llm.api_key}"},
                json={
                    "model": llm.model_name,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 3000,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        data = _extract_json(content) or {}
        for row in data.get("plans") or []:
            if not isinstance(row, dict):
                continue
            try:
                iid = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            base = out.get(iid)
            if not base:
                continue
            title = str(row.get("title") or base.title).strip()[:_TITLE_MAX] or base.title
            criteria = str(row.get("success_criteria") or base.success_criteria).strip()[:2000]
            due = _parse_due(row.get("due_date"), base.due_date)
            # LLM 标题若仍空泛，回退规则拼标题
            item = next((x for x in actionable if x.id == iid), None)
            if item and (
                is_vague_continuation(title)
                or (
                    title == (item.next_goal or "").strip()[:_TITLE_MAX]
                    and (item.task_title or "").strip()
                    and (item.task_title or "").strip() not in title
                )
            ):
                title = compose_clear_task_title(
                    task_title=item.task_title or "",
                    next_goal=item.next_goal or "",
                    result_text=item.result_text or "",
                )
            out[iid] = NormalizedNextPlan(
                title=title,
                success_criteria=criteria or base.success_criteria,
                due_date=due,
            )
    except Exception:
        logger.exception("normalize_next_plans_batch LLM failed; using rule fallback")
    return out


async def _ensure_project_member(db: AsyncSession, project_id: int, user_id: int) -> None:
    exists = (
        await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if exists:
        return
    db.add(ProjectMember(project_id=project_id, user_id=user_id, member_role="member"))
    await db.flush()


async def sync_next_plan_todos(
    db: AsyncSession,
    *,
    report: ProgressReport,
    author: User,
    use_llm: bool = True,
) -> int:
    """
    将已提交周报的下周计划同步为本人自建待办。
    返回新建或更新的条数。
    """
    if report.period_type != "week" or report.status != "submitted":
        return 0

    items = list(report.items or [])
    if not items:
        result = await db.execute(
            select(ProgressReportItem).where(ProgressReportItem.report_id == report.id)
        )
        items = list(result.scalars().all())

    plans = await normalize_next_plans_batch(
        db,
        period_end=report.period_end or report.period_start,
        items=items,
        use_llm=use_llm,
    )

    daily = await ensure_daily_project(db)
    needs_classify = any(
        (not it.project_id) or it.project_id == daily.id for it in items if plans.get(it.id)
    )
    classify_bundle = await build_project_context(db) if needs_classify else None
    existing_rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source == SOURCE_REPORT_NEXT,
                    Task.source_report_id == report.id,
                    Task.assignee_id == author.id,
                )
            )
        ).scalars().all()
    )
    by_ref_map: dict[str, list[Task]] = defaultdict(list)
    for t in existing_rows:
        by_ref_map[t.source_ref or ""].append(t)
    touched = 0

    def _tasks_for(ref: str) -> list[Task]:
        return list(by_ref_map.get(ref) or [])

    def _pick_task(ref: str) -> Task | None:
        rows = _tasks_for(ref)
        if not rows:
            return None
        rows.sort(key=lambda t: (0 if t.status != "completed" else 1, t.id or 0))
        return rows[0]

    def _remember(task: Task) -> None:
        ref = task.source_ref or ""
        bucket = by_ref_map[ref]
        if task not in bucket:
            bucket.append(task)

    # 空壳/复述本周结果的「下一步」：不建未完成待办；已有的则收成已完成（避免列表里像总结却未完成）
    for it in items:
        if it.no_action:
            continue
        if not should_skip_next_plan_item(
            next_goal=it.next_goal,
            result_text=it.result_text,
            task_title=it.task_title,
        ):
            continue
        ref = _item_source_ref(it)
        tasks = _tasks_for(ref)
        if not tasks:
            task = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.source_ref == ref,
                        Task.source_report_id == report.id,
                        Task.assignee_id == author.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if task:
                _remember(task)
                tasks = [task]
        result = (it.result_text or "").strip()
        for task in tasks:
            if task.status == "completed":
                continue
            task.status = "completed"
            if result:
                task.result_text = result[:4000]
                if not (task.goal or "").strip():
                    task.goal = result[:2000]
            touched += 1
            db.add(
                TaskEvent(
                    task_id=task.id,
                    actor_id=author.id,
                    action="updated_from_report",
                    payload={
                        "report_id": report.id,
                        "item_id": it.id,
                        "kind": "next_closed_as_summary",
                    },
                )
            )

    if not plans:
        await db.flush()
        return touched

    for it in items:
        norm = plans.get(it.id)
        if not norm:
            continue
        if should_skip_next_plan_item(
            next_goal=it.next_goal,
            result_text=it.result_text,
            task_title=it.task_title,
        ):
            continue
        ref = _item_source_ref(it)
        project_id = await _resolve_item_project_id(
            db,
            item=it,
            author=author,
            daily_id=daily.id,
            classify_bundle=classify_bundle,
        )
        await _ensure_project_member(db, project_id, author.id)

        task = _pick_task(ref)
        if not task:
            # 并发下另一请求可能已插入；创建前按 source/source_ref/assignee 再查一次
            task = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.source == SOURCE_REPORT_NEXT,
                        Task.source_ref == ref,
                        Task.source_report_id == report.id,
                        Task.assignee_id == author.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if task:
                _remember(task)
        now = datetime.now(timezone.utc)
        checklist = [
            {
                "title": norm.success_criteria[:200],
                "done": False,
                "sort_order": 0,
            }
        ]
        phase_id = await guess_phase_id(
            db,
            project_id,
            it.task_title,
            it.next_goal,
            it.result_text,
            " ".join(it.tags or []) if isinstance(it.tags, list) else "",
        )
        project_name = (
            await db.execute(select(Project.name).where(Project.id == project_id))
        ).scalar_one_or_none() or ""
        clear_title = compose_clear_task_title(
            task_title=it.task_title or "",
            next_goal=it.next_goal or "",
            project_name=project_name,
            result_text=it.result_text or "",
        )
        # 规范化结果若已含概述/项目则保留，否则用清晰拼标题
        final_title = norm.title[:256]
        if (
            is_vague_continuation(final_title)
            or _is_template_continuation(it.next_goal or "", it.task_title or "")
            or _is_template_continuation(final_title, it.task_title or "")
            or (project_name and _short_project_hint(project_name) not in final_title
                and (it.task_title or "").strip() == (it.next_goal or "").strip())
            or (
                (it.task_title or "").strip()
                and (it.task_title or "").strip() not in final_title
                and final_title == (it.next_goal or "").strip()[:_TITLE_MAX]
            )
        ):
            final_title = clear_title
        elif _short_project_hint(project_name) and _short_project_hint(project_name) not in final_title:
            if len(final_title) < 28 or (it.task_title or "").strip() == (it.next_goal or "").strip():
                final_title = compose_clear_task_title(
                    task_title=it.task_title or "",
                    next_goal=final_title,
                    project_name=project_name,
                    result_text=it.result_text or "",
                )
        # 目标：空壳「继续推进」用规范化 criteria
        final_goal = norm.success_criteria[:2000]

        if task:
            task.project_id = project_id
            task.title = final_title[:256]
            task.goal = final_goal
            task.due_date = norm.due_date
            task.checklist = checklist
            task.source = SOURCE_REPORT_NEXT
            task.source_report_id = report.id
            task.source_ref = ref
            task.phase_id = phase_id
            touched += 1
            db.add(
                TaskEvent(
                    task_id=task.id,
                    actor_id=author.id,
                    action="updated_from_report",
                    payload={"report_id": report.id, "item_id": it.id},
                )
            )
        else:
            task = Task(
                project_id=project_id,
                phase_id=phase_id,
                assignee_id=author.id,
                created_by_id=author.id,
                assignment_type="self",
                title=final_title[:256],
                goal=final_goal,
                due_date=norm.due_date,
                status="not_started",
                priority="p3",
                acceptance_status="accepted",
                accepted_at=now,
                checklist=checklist,
                result_text="",
                source=SOURCE_REPORT_NEXT,
                source_report_id=report.id,
                source_ref=ref,
            )
            db.add(task)
            await db.flush()
            _remember(task)
            db.add(
                TaskEvent(
                    task_id=task.id,
                    actor_id=author.id,
                    action="created_from_report",
                    payload={"report_id": report.id, "item_id": it.id},
                )
            )
            touched += 1

        # 同 ref 重复未完成镜像：只留一条，其余收成已完成，避免列表重复
        for extra in _tasks_for(ref):
            if extra.id == task.id or extra.status == "completed":
                continue
            extra.status = "completed"
            touched += 1

        it.linked_task_id = task.id

    await db.flush()
    return touched


def _done_source_ref(item: ProgressReportItem) -> str:
    return f"done:{_item_source_ref(item)}"


async def sync_done_result_todos(
    db: AsyncSession,
    *,
    report: ProgressReport,
    author: User,
) -> int:
    """
    将已提交周报的「做成了什么」同步为本人已完成任务，并挂到对应阶段。
    与「下一步」待办分开存（source=report_done），不占用 linked_task_id。
    """
    if report.period_type != "week" or report.status != "submitted":
        return 0

    items = list(report.items or [])
    if not items:
        result = await db.execute(
            select(ProgressReportItem).where(ProgressReportItem.report_id == report.id)
        )
        items = list(result.scalars().all())

    actionable = [
        it
        for it in items
        if not it.no_action and (it.result_text or "").strip() and (it.task_title or "").strip()
    ]
    if not actionable:
        return 0

    daily = await ensure_daily_project(db)
    needs_classify = any((not it.project_id) or it.project_id == daily.id for it in actionable)
    classify_bundle = await build_project_context(db) if needs_classify else None
    existing_rows = list(
        (
            await db.execute(
                select(Task).where(
                    Task.source == SOURCE_REPORT_DONE,
                    Task.source_report_id == report.id,
                    Task.assignee_id == author.id,
                )
            )
        ).scalars().all()
    )
    by_ref_map: dict[str, list[Task]] = defaultdict(list)
    for t in existing_rows:
        by_ref_map[t.source_ref or ""].append(t)
    touched = 0
    now = datetime.now(timezone.utc)

    def _pick_done(ref: str) -> Task | None:
        rows = list(by_ref_map.get(ref) or [])
        if not rows:
            return None
        rows.sort(key=lambda t: t.id or 0)
        return rows[0]

    def _remember_done(task: Task) -> None:
        ref = task.source_ref or ""
        bucket = by_ref_map[ref]
        if task not in bucket:
            bucket.append(task)

    for it in actionable:
        ref = _done_source_ref(it)
        project_id = await _resolve_item_project_id(
            db,
            item=it,
            author=author,
            daily_id=daily.id,
            classify_bundle=classify_bundle,
        )
        await _ensure_project_member(db, project_id, author.id)
        title = (it.task_title or "").strip()[:256] or "本周完成项"
        result = (it.result_text or "").strip()[:4000]
        phase_id = await guess_phase_id(
            db,
            project_id,
            it.task_title,
            it.result_text,
            " ".join(it.tags or []) if isinstance(it.tags, list) else "",
        )
        task = _pick_done(ref)
        if not task:
            # 并发下另一请求可能已插入；创建前按 source/source_ref/assignee 再查一次
            task = (
                await db.execute(
                    select(Task)
                    .where(
                        Task.source == SOURCE_REPORT_DONE,
                        Task.source_ref == ref,
                        Task.source_report_id == report.id,
                        Task.assignee_id == author.id,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if task:
                _remember_done(task)
        if task:
            task.project_id = project_id
            task.title = title
            task.goal = result[:2000] or task.goal
            task.result_text = result
            task.status = "completed"
            task.source = SOURCE_REPORT_DONE
            task.source_report_id = report.id
            task.source_ref = ref
            task.phase_id = phase_id
            touched += 1
            db.add(
                TaskEvent(
                    task_id=task.id,
                    actor_id=author.id,
                    action="updated_from_report",
                    payload={"report_id": report.id, "item_id": it.id, "kind": "done"},
                )
            )
        else:
            task = Task(
                project_id=project_id,
                phase_id=phase_id,
                assignee_id=author.id,
                created_by_id=author.id,
                assignment_type="self",
                title=title,
                goal=result[:2000] or "本周已完成",
                due_date=report.period_end or report.period_start,
                status="completed",
                priority="p3",
                acceptance_status="accepted",
                accepted_at=now,
                checklist=[],
                result_text=result,
                source=SOURCE_REPORT_DONE,
                source_report_id=report.id,
                source_ref=ref,
            )
            db.add(task)
            await db.flush()
            _remember_done(task)
            db.add(
                TaskEvent(
                    task_id=task.id,
                    actor_id=author.id,
                    action="created_from_report",
                    payload={"report_id": report.id, "item_id": it.id, "kind": "done"},
                )
            )
            touched += 1

    await db.flush()
    return touched


async def resync_submitted_week_reports(
    db: AsyncSession,
    *,
    report_ids: list[int] | None = None,
    use_llm: bool = False,
) -> dict[str, int]:
    """把已提交周报重新同步到任务/阶段（用于补历史数据）。默认不用 LLM，避免慢。"""
    from sqlalchemy.orm import selectinload

    q = (
        select(ProgressReport)
        .options(
            selectinload(ProgressReport.items),
            selectinload(ProgressReport.author),
        )
        .where(
            ProgressReport.period_type == "week",
            ProgressReport.status == "submitted",
        )
    )
    if report_ids:
        q = q.where(ProgressReport.id.in_(report_ids))
    reports = list((await db.execute(q.order_by(ProgressReport.id))).scalars().all())
    next_total = 0
    done_total = 0
    for report in reports:
        author = report.author
        if not author:
            author = await db.get(User, report.user_id)
        if not author:
            continue
        next_total += await sync_next_plan_todos(
            db, report=report, author=author, use_llm=use_llm
        )
        done_total += await sync_done_result_todos(db, report=report, author=author)
    return {"reports": len(reports), "next_synced": next_total, "done_synced": done_total}
