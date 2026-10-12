"""周报卡片 AI 结构化整理 + 历史任务链/项目/交办匹配。"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import ProgressReport, ProgressReportItem, Project, ProjectMember, Task, User
from app.services.llm_resolver import ResolvedLlm, resolve_llm
from app.services.project_match import guess_project_id, text_similarity
from app.services.work_tags import WORK_TAG_OPTIONS, resolve_item_tags

# 长前缀优先；不含单独「继续」（易误伤「继续政务外网运维…」等套话）
CONTINUATION_PREFIXES: tuple[str, ...] = (
    "继续推进",
    "继续跟进",
    "继续开展",
    "继续落实",
    "继续完善",
    "继续处理",
    "持续推进",
    "持续跟进",
    "接着推进",
    "下一步推进",
    "下一步跟进",
)

_CHAIN_THRESHOLD_DEFAULT = 0.55
_CHAIN_THRESHOLD_CONTINUATION = 0.40
# 延续类多看约 12 周历史
_HISTORY_LOOKBACK_DAYS = 84


def _similarity(a: str, b: str) -> float:
    return text_similarity(a, b)


def strip_continuation_prefix(text: str) -> str:
    """去掉「继续推进」等延续前缀，便于与上周无前缀标题比对。"""
    t = (text or "").strip()
    if not t:
        return ""
    for prefix in CONTINUATION_PREFIXES:
        if t.startswith(prefix):
            rest = t[len(prefix) :].lstrip("：:，,、 \t-—")
            return rest or t
    # 软前缀：单独「继续」
    if t.startswith("继续") and not any(t.startswith(p) for p in CONTINUATION_PREFIXES):
        rest = t[len("继续") :].lstrip("：:，,、 \t-—")
        return rest or t
    return t


def is_continuation_text(*parts: str) -> bool:
    """标题/下一步是否以明确延续类前缀开头（不含单独「继续」）。"""
    for part in parts:
        t = (part or "").strip()
        if t and any(t.startswith(p) for p in CONTINUATION_PREFIXES):
            return True
    return False


def is_soft_continuation_text(*parts: str) -> bool:
    """套话式「继续…」开头（非明确「继续推进」等）。"""
    for part in parts:
        t = (part or "").strip()
        if not t:
            continue
        if is_continuation_text(t):
            continue
        if t.startswith("继续"):
            return True
    return False


_MIN_TOPIC_CHARS = 4
CONTINUATION_WARNING = (
    "「继续推进」类表述请写清具体事项，并优先采用任务链挂上前序工作，避免无头无尾。"
)
VAGUE_CONTINUATION_WARNING = (
    "标题太笼统（如「继续推进」「持续跟进」）。请写清具体事项：对象 + 动作。"
)


def is_vague_continuation(title: str) -> bool:
    """续推前缀且去前缀后有效主题过短。"""
    t = (title or "").strip()
    if not t:
        return False
    if t in {
        "继续推进",
        "持续推进",
        "继续跟进",
        "持续跟进",
        "继续开展",
        "继续落实",
        "继续完善",
        "继续处理",
        "接着推进",
        "下一步推进",
        "下一步跟进",
        "继续",
        "下一步",
        "持续",
    }:
        return True
    hard = is_continuation_text(t)
    soft = is_soft_continuation_text(t)
    if not hard and not soft:
        return False
    core = strip_continuation_prefix(t).strip()
    compact = "".join(
        ch for ch in core if ch.strip() and ch not in "：:，,、.。！!？?·-—"
    )
    return len(compact) < _MIN_TOPIC_CHARS


def continuation_warning_for(title: str, next_goal: str = "") -> str:
    if is_vague_continuation(title) or is_vague_continuation(next_goal):
        return VAGUE_CONTINUATION_WARNING
    if is_continuation_text(title, next_goal) or is_soft_continuation_text(title, next_goal):
        return CONTINUATION_WARNING
    return ""


def _chain_compare_text(title: str, result: str = "", next_goal: str = "") -> str:
    """用于串链相似度的正文：去前缀后的标题 + 结果摘要（不含套话下一步）。"""
    core = strip_continuation_prefix(title) or (title or "").strip()
    bits = [core]
    if result:
        bits.append((result or "")[:200])
    # 仅当下一步是明确延续前缀时才并入比对，避免「继续政务外网运维」套话污染
    if is_continuation_text(next_goal):
        goal_core = strip_continuation_prefix(next_goal)
        if goal_core and goal_core != core:
            bits.append(goal_core[:120])
    return " ".join(b for b in bits if b).strip()


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _item_to_structured(raw: dict[str, Any]) -> dict[str, Any]:
    title = (raw.get("task_title") or "").strip()[:120]
    result = (raw.get("result_text") or "").strip()
    next_goal = (raw.get("next_goal") or "").strip()
    stuck = (raw.get("stuck_detail") or "").strip()
    return {
        "task_title": title,
        "result_text": result,
        "quant_value": (raw.get("quant_value") or "").strip()[:64],
        "stuck_status": raw.get("stuck_status") or "none",
        "stuck_detail": stuck,
        "stuck_next_action": (raw.get("stuck_next_action") or "").strip(),
        "stuck_follow_up": _parse_date(raw.get("stuck_follow_up")),
        "stuck_need_who": (raw.get("stuck_need_who") or "").strip(),
        "next_goal": next_goal,
        "next_due_date": _parse_date(raw.get("next_due_date")),
        "risk_level": raw.get("risk_level") or "normal",
        "risk_reason": (raw.get("risk_reason") or "").strip(),
        "risk_impact_date": _parse_date(raw.get("risk_impact_date")),
        "risk_coordinator": (raw.get("risk_coordinator") or "").strip(),
        "tags": resolve_item_tags(
            raw.get("tags"),
            title=title,
            result=result,
            next_goal=next_goal,
            stuck=stuck,
        ),
    }


async def _load_context(db: AsyncSession, user: User, period_start: date) -> dict[str, Any]:
    memberships = await db.execute(
        select(ProjectMember, Project)
        .join(Project, Project.id == ProjectMember.project_id)
        .where(ProjectMember.user_id == user.id)
    )
    projects: list[dict[str, Any]] = []
    project_ids: list[int] = []
    for m, p in memberships.all():
        projects.append({"project_id": p.id, "name": p.name})
        project_ids.append(p.id)

    since = period_start - timedelta(days=_HISTORY_LOOKBACK_DAYS)
    hist = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items))
        .where(
            ProgressReport.user_id == user.id,
            ProgressReport.status == "submitted",
            ProgressReport.period_start >= since,
            ProgressReport.period_start < period_start,
        )
        .order_by(ProgressReport.period_start.desc())
    )
    history: list[dict[str, Any]] = []
    for report in hist.scalars().all():
        for it in report.items:
            if it.no_action:
                continue
            title = (it.task_title or "").strip()
            if not title and not (it.result_text or "").strip():
                continue
            history.append(
                {
                    "item_id": it.id,
                    "chain_id": it.chain_id,
                    "project_id": it.project_id,
                    "linked_task_id": it.linked_task_id,
                    "task_title": title,
                    "result_text": (it.result_text or "")[:200],
                    "next_goal": (it.next_goal or "")[:120],
                    "period_start": str(report.period_start),
                }
            )

    tasks: list[dict[str, Any]] = []
    if project_ids:
        t_res = await db.execute(
            select(Task).where(
                Task.assignee_id == user.id,
                Task.project_id.in_(project_ids),
                Task.status != "completed",
            )
        )
        for t in t_res.scalars().all():
            tasks.append(
                {
                    "task_id": t.id,
                    "title": t.title,
                    "project_id": t.project_id,
                }
            )

    return {"projects": projects, "history": history, "tasks": tasks}


def _rule_match(
    draft: dict[str, Any],
    ctx: dict[str, Any],
    existing_chain_id: str | None,
) -> dict[str, Any]:
    structured = _item_to_structured(draft)
    query = f"{structured['task_title']} {structured['result_text']}".strip()
    draft_compare = _chain_compare_text(
        structured["task_title"],
        structured["result_text"],
        structured.get("next_goal") or "",
    )
    continuation = is_continuation_text(
        structured["task_title"],
        structured.get("next_goal") or "",
    )
    chain_threshold = (
        _CHAIN_THRESHOLD_CONTINUATION if continuation else _CHAIN_THRESHOLD_DEFAULT
    )
    draft_project_id = draft.get("project_id")
    if not isinstance(draft_project_id, int):
        draft_project_id = None

    # 结构化建议阈值略低于总览展示（0.35），便于填写时给出候选
    pid, conf, pname = guess_project_id(
        structured["task_title"],
        structured["result_text"],
        ctx["projects"],
        threshold=0.35,
    )
    project_suggestion = None
    if pid is not None and pname:
        project_suggestion = {
            "project_id": pid,
            "name": pname,
            "confidence": conf,
        }

    chain_suggestion = None
    create_new_chain = not bool(existing_chain_id)
    if not existing_chain_id:
        best_chain = 0.0
        for h in ctx["history"]:
            h_title = h.get("task_title") or ""
            h_compare = _chain_compare_text(
                h_title,
                h.get("result_text") or "",
                h.get("next_goal") or "",
            )
            score = max(
                _similarity(structured["task_title"], h_title),
                _similarity(
                    strip_continuation_prefix(structured["task_title"]),
                    strip_continuation_prefix(h_title),
                ),
                _similarity(query, f"{h_title} {h.get('result_text') or ''}"),
                _similarity(draft_compare, h_compare) if draft_compare and h_compare else 0.0,
            )
            # 同项目加分，优先串本立项上的延续
            if (
                draft_project_id is not None
                and h.get("project_id") == draft_project_id
                and score > 0
            ):
                score = min(1.0, score + 0.08)
            if score > best_chain and score >= chain_threshold:
                best_chain = score
                reason_prefix = "延续" if continuation else "相似"
                if h.get("chain_id"):
                    chain_suggestion = {
                        "chain_id": h["chain_id"],
                        "title": h_title or "历史任务",
                        "last_period": h["period_start"],
                        "reason": (
                            f"{reason_prefix} {h['period_start']}「{h_title}」"
                            f"（匹配度 {score:.0%}）"
                        ),
                        "project_id": h.get("project_id"),
                        "seed_item_id": None,
                    }
                else:
                    chain_suggestion = {
                        "chain_id": str(uuid.uuid4()),
                        "title": h_title or "历史任务",
                        "last_period": h["period_start"],
                        "reason": (
                            f"与 {h['period_start']}「{h_title}」{reason_prefix}，"
                            f"建议新建并串联（匹配度 {score:.0%}）"
                        ),
                        "project_id": h.get("project_id"),
                        "seed_item_id": h["item_id"],
                    }
                create_new_chain = False

    task_suggestion = None
    best_task = 0.0
    for t in ctx["tasks"]:
        score = max(
            _similarity(structured["task_title"] or query, t["title"]),
            _similarity(
                strip_continuation_prefix(structured["task_title"] or query),
                strip_continuation_prefix(t["title"]),
            ),
        )
        if score > best_task and score >= 0.55:
            best_task = score
            task_suggestion = {
                "task_id": t["task_id"],
                "title": t["title"],
                "project_id": t["project_id"],
            }

    warn = continuation_warning_for(
        structured["task_title"],
        structured.get("next_goal") or "",
    )
    return {
        "enabled": True,
        "structured": structured,
        "project_suggestion": project_suggestion,
        "chain_suggestion": chain_suggestion,
        "task_suggestion": task_suggestion,
        "create_new_chain": create_new_chain,
        "message": "已用规则给出相似匹配（未调用大模型润色）",
        "continuation_warning": warn,
    }


async def _resolve_structure_llm(db: AsyncSession) -> ResolvedLlm | None:
    resolved = await resolve_llm(db, purpose="report_structure")
    if resolved:
        return resolved
    return await resolve_llm(db, purpose="report_coach")


async def ai_structure(
    db: AsyncSession,
    user: User,
    draft: dict[str, Any],
    period_type: str,
    period_start: date,
    period_end: date | None,
    existing_chain_id: str | None,
) -> dict[str, Any]:
    ctx = await _load_context(db, user, period_start)
    base = _rule_match(draft, ctx, existing_chain_id)

    # 历史无 chain 时：若规则命中，把建议 chain_id 固化到返回；前端采用后再写库
    resolved = await _resolve_structure_llm(db)
    if not resolved:
        if not settings.openai_api_key:
            base["message"] = (
                "当前未配置 AI，已用规则匹配历史任务/项目。"
                "可在「系统管理 → 模型管理」添加模型以启用润色。"
            )
        return base

    tag_example = json.dumps(WORK_TAG_OPTIONS[:2], ensure_ascii=False)
    tag_list = "、".join(WORK_TAG_OPTIONS)
    prompt = {
        "role": "system",
        "content": (
            "你是周报助手。根据员工草稿，输出 JSON（不要 markdown）：\n"
            "{"
            '"structured":{"task_title":"","result_text":"","quant_value":"",'
            '"stuck_status":"none|blocker|coordination","stuck_detail":"",'
            '"stuck_next_action":"","stuck_follow_up":null,"stuck_need_who":"",'
            '"next_goal":"","next_due_date":null,'
            '"risk_level":"normal|watch|escalate","risk_reason":"",'
            '"risk_impact_date":null,"risk_coordinator":"",'
            f'"tags":{tag_example}'
            "},"
            '"project_id":null,"chain_item_id":null,"task_id":null,'
            '"create_new_chain":true,"reason":""'
            "}\n"
            "要求：\n"
            "1) structured 把草稿拆进字段并润色，保留原意，task_title≤120字；\n"
            "2) project_id 只能选自 projects 列表的 id，不确定则 null；\n"
            "3) chain_item_id 只能选自 history 的 item_id（表示延续该历史卡片同件事），无则 null；\n"
            "4) task_id 只能选自 tasks 列表，无则 null；\n"
            "5) 若 chain_item_id 有值则 create_new_chain=false；\n"
            "6) 日期用 YYYY-MM-DD 或 null；用中文；\n"
            f"7) tags 必填 1～3 个，只能从：{tag_list}；"
            "按事项性质打标（签约/方案→售前，安装验收→售中，故障→售后，巡检驻点→运维，"
            "催进度→流程跟进，与业主沟通→客户对接，讲解上会→方案汇报，统筹→项目管理）；\n"
            "8) 若 task_title 或 next_goal 以「继续推进/继续跟进/持续/接着/下一步」等开头，"
            "必须尽量从 history 选 chain_item_id 串上前序工作，禁止轻易 create_new_chain=true；"
            "匹配时忽略「继续…」前缀，看后面的事项主题。"
        ),
    }
    user_msg = {
        "role": "user",
        "content": json.dumps(
            {
                "period_type": period_type,
                "period_start": str(period_start),
                "period_end": str(period_end) if period_end else None,
                "existing_chain_id": existing_chain_id,
                "draft": draft,
                "projects": ctx["projects"],
                "history": ctx["history"][:40],
                "tasks": ctx["tasks"][:30],
            },
            ensure_ascii=False,
            default=str,
        ),
    }
    url = resolved.api_base.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {resolved.api_key}"},
                json={
                    "model": resolved.model_name,
                    "messages": [prompt, user_msg],
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            start = content.find("{")
            end = content.rfind("}")
            data = json.loads(content[start : end + 1]) if start >= 0 else {}
    except Exception:
        base["message"] = "AI 调用失败，已回退到规则匹配。"
        return base

    structured = _item_to_structured({**draft, **(data.get("structured") or {})})
    # 空字段用草稿兜底
    for k, v in base["structured"].items():
        if k == "tags":
            continue
        if structured.get(k) in (None, "") and v not in (None, ""):
            structured[k] = v
    # 标签：模型优先，否则规则（保证非空）
    llm_tags = (data.get("structured") or {}).get("tags")
    structured["tags"] = resolve_item_tags(
        llm_tags if llm_tags is not None else structured.get("tags"),
        title=structured.get("task_title") or "",
        result=structured.get("result_text") or "",
        next_goal=structured.get("next_goal") or "",
        stuck=structured.get("stuck_detail") or "",
    )

    project_suggestion = base.get("project_suggestion")
    pid = data.get("project_id")
    if isinstance(pid, int):
        for p in ctx["projects"]:
            if p["project_id"] == pid:
                project_suggestion = {
                    "project_id": pid,
                    "name": p["name"],
                    "confidence": 0.9,
                }
                break

    chain_suggestion = base.get("chain_suggestion")
    create_new_chain = bool(data.get("create_new_chain", True)) and not existing_chain_id
    hist_by_id = {h["item_id"]: h for h in ctx["history"]}
    chain_item_id = data.get("chain_item_id")
    if isinstance(chain_item_id, int) and chain_item_id in hist_by_id:
        h = hist_by_id[chain_item_id]
        if h.get("chain_id"):
            cid = h["chain_id"]
            seed = None
        else:
            cid = str(uuid.uuid4())
            seed = h["item_id"]
        chain_suggestion = {
            "chain_id": cid,
            "title": h["task_title"] or structured["task_title"] or "历史任务",
            "last_period": h["period_start"],
            "reason": (data.get("reason") or f"延续 {h['period_start']}「{h['task_title']}」")[:200],
            "project_id": h.get("project_id"),
            "seed_item_id": seed,
        }
        create_new_chain = False
    elif chain_suggestion and not existing_chain_id:
        # 模型未点名历史卡时，保留规则串链建议（尤其「继续推进」类）
        create_new_chain = False

    task_suggestion = base.get("task_suggestion")
    tid = data.get("task_id")
    if isinstance(tid, int):
        for t in ctx["tasks"]:
            if t["task_id"] == tid:
                task_suggestion = {
                    "task_id": tid,
                    "title": t["title"],
                    "project_id": t["project_id"],
                }
                break

    if existing_chain_id:
        create_new_chain = False

    warn = continuation_warning_for(
        structured.get("task_title") or "",
        structured.get("next_goal") or "",
    )
    return {
        "enabled": True,
        "structured": structured,
        "project_suggestion": project_suggestion,
        "chain_suggestion": chain_suggestion,
        "task_suggestion": task_suggestion,
        "create_new_chain": create_new_chain,
        "message": f"AI（{resolved.name or resolved.model_name}）已整理并自动打标，请确认后再采用正文。",
        "continuation_warning": warn,
    }
