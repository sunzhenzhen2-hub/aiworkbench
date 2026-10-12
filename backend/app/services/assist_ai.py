"""项目 / 卡点 / 总览 / 方案检索 AI 草稿（确认后落库）。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Blocker, Project, Solution, Task
from app.services.ai_platform import run_structured, run_text_stream
from app.services.project_match import guess_project_type_from_title
from app.services.project_phases import STANDARD_PHASES, is_standard_phase, phases_for


def _strip_model_thinking(raw: str) -> str:
    """去掉模型思考块（含未闭合），避免流式上屏。"""
    text = raw or ""
    # 常见标签：<think>…</think>、<thinking>…</thinking>
    text = re.sub(
        r"<(think|thinking|reasoning|thought)\b[^>]*>[\s\S]*?</\1\s*>",
        "",
        text,
        flags=re.I,
    )
    # 未闭合：从开标签起全部丢掉（流式中常见）
    text = re.sub(
        r"<(think|thinking|reasoning|thought)\b[^>]*>[\s\S]*$",
        "",
        text,
        flags=re.I,
    )
    return text.strip()


def _looks_like_model_scratchpad(text: str) -> bool:
    """英文草稿/自我分析，不当作用户可读总览。"""
    s = (text or "").strip()
    if not s:
        return True
    low = s.lower()
    markers = (
        "let me analyze",
        "the user wants",
        "based on the statistics",
        "i need to",
        "i'll write",
        "i will write",
        "plain language",
        "<think",
    )
    if any(m in low for m in markers):
        return True
    # 几乎全是 ASCII 且较长 → 多半是英文思考
    ascii_chars = sum(1 for c in s if ord(c) < 128)
    if len(s) >= 40 and ascii_chars / max(len(s), 1) > 0.85:
        return True
    return False


def _is_stats_echo_line(line: str) -> bool:
    return bool(
        re.match(
            r"^(可见项目|重点项目|未完成重点|待接收交办|未关闭卡点)",
            (line or "").strip(),
        )
    )


def _extract_brief_paragraphs_and_focus(raw: str) -> tuple[list[str], list[str]]:
    """拆成非列表段落 + focus 列表行（已剥思考块）。"""
    text = _strip_model_thinking(raw)
    if not text:
        return [], []
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    bullet_re = re.compile(r"^\s*[-•*]\s+(.*)$")
    paragraphs: list[str] = []
    focus: list[str] = []
    buf: list[str] = []

    def flush_buf() -> None:
        nonlocal buf
        if buf:
            paragraphs.append("\n".join(buf).strip())
            buf = []

    for line in text.splitlines():
        m = bullet_re.match(line)
        if m:
            flush_buf()
            item = (m.group(1) or "").strip()
            if item and not _is_stats_echo_line(item):
                focus.append(item[:120])
            continue
        if not line.strip():
            flush_buf()
            continue
        buf.append(line.strip())
    flush_buf()
    return paragraphs, focus


def _parse_brief_plain_text(raw: str, fallback_focus: list[str]) -> tuple[str, list[str]]:
    """取第一段可读中文白话为 summary，其余列表为 focus。"""
    paragraphs, focus = _extract_brief_paragraphs_and_focus(raw)
    summary = ""
    for p in paragraphs:
        if p and not _looks_like_model_scratchpad(p):
            summary = p
            break
    return summary[:500], (focus[:5] or list(fallback_focus))


def _summary_visible_while_streaming(raw: str) -> str:
    """流式只露出可读中文总览；思考/英文草稿阶段返回空。"""
    paragraphs, _focus = _extract_brief_paragraphs_and_focus(raw)
    for p in paragraphs:
        if p and not _looks_like_model_scratchpad(p):
            return p
    return ""


async def _overview_rule_context(
    db: AsyncSession, project_ids: list[int]
) -> tuple[str, list[str], dict[str, int]]:
    """统计 + 规则兜底文案。"""
    if not project_ids:
        return "暂无可见项目。", [], {
            "projects": 0,
            "key": 0,
            "key_tasks": 0,
            "pending_accept": 0,
            "open_blockers": 0,
        }

    key_proj = (
        await db.execute(
            select(func.count(Project.id)).where(
                Project.id.in_(project_ids), Project.level == "key"
            )
        )
    ).scalar_one()
    key_tasks = (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.project_id.in_(project_ids),
                Task.priority.in_(("p0", "p1")),
                Task.status != "completed",
            )
        )
    ).scalar_one()
    pending_accept = (
        await db.execute(
            select(func.count(Task.id)).where(
                Task.project_id.in_(project_ids),
                Task.acceptance_status == "pending",
            )
        )
    ).scalar_one()
    open_blockers = (
        await db.execute(
            select(func.count(Blocker.id)).where(
                Blocker.project_id.in_(project_ids),
                Blocker.status != "resolved",
            )
        )
    ).scalar_one()

    rule_summary = (
        f"可见项目 {len(project_ids)} 个；重点项目 {int(key_proj or 0)}；"
        f"未完成重点任务(P0/P1) {int(key_tasks or 0)}；待接收交办 {int(pending_accept or 0)}；"
        f"未关闭卡点 {int(open_blockers or 0)}。"
    )
    focus: list[str] = []
    if int(pending_accept or 0):
        focus.append("先处理待接收交办，避免任务悬空")
    if int(key_tasks or 0):
        focus.append("盯紧重点任务进展")
    if int(key_proj or 0):
        focus.append("查看重点项目卡点")
    if not focus:
        focus.append("保持周报与任务更新节奏")

    stats = {
        "projects": len(project_ids),
        "key": int(key_proj or 0),
        "key_tasks": int(key_tasks or 0),
        "pending_accept": int(pending_accept or 0),
        "open_blockers": int(open_blockers or 0),
    }
    return rule_summary, focus, stats


async def draft_project_from_text(db: AsyncSession, text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    ptype = guess_project_type_from_title(raw)
    name = re.split(r"[，,。\s]", raw)[0][:64] or "未命名项目"
    phase_names = phases_for(ptype)
    fallback = {
        "name": name,
        "project_type": ptype,
        "level": "normal",
        "current_phase": phase_names[0] if phase_names else STANDARD_PHASES[0],
        "description": raw[:500],
        "rationale": "规则兜底",
        "source": "rule",
    }
    system = (
        "根据用户描述提炼立项项目。输出 JSON："
        '{"name":"立项名","project_type":"software|integration|fusion",'
        '"level":"normal|key|daily","current_phase":"需求分析等","description":"一句说明","rationale":"..."}。'
        "software=软件开发项目；integration=系统集成项目；fusion=集众融合项目（如智安校园）；"
        "level：普通项目用 normal，重点用 key，杂项日常用 daily。不要 markdown。"
    )
    data, source = await run_structured(
        db, purpose="project_draft", system=system, user=f"用户：{raw}\n只输出 JSON。"
    )
    if not data:
        return fallback
    out = {**fallback, **{k: v for k, v in data.items() if v is not None}}
    out["name"] = str(out.get("name") or name)[:128]
    if out.get("project_type") not in {"software", "integration", "fusion"}:
        out["project_type"] = ptype
    if out.get("level") not in {"normal", "key", "daily"}:
        # 兼容旧模型输出
        legacy = {"green": "normal", "yellow": "key", "red": "key"}.get(str(out.get("level")), "normal")
        out["level"] = legacy if legacy in {"normal", "key", "daily"} else "normal"
    allowed_phases = phases_for(str(out.get("project_type")))
    phase = str(out.get("current_phase") or (allowed_phases[0] if allowed_phases else STANDARD_PHASES[0]))
    if not is_standard_phase(phase) and phase not in allowed_phases:
        phase = allowed_phases[0] if allowed_phases else STANDARD_PHASES[0]
    out["current_phase"] = phase
    out["source"] = source
    return out


async def draft_blocker_from_text(
    db: AsyncSession, *, text: str, project_id: int | None = None
) -> dict[str, Any]:
    raw = (text or "").strip()
    fallback = {
        "description": raw[:512] or "卡点",
        "responsible_party": "待确认",
        "party_type": "internal",
        "actions_taken": "已记录",
        "next_action": "跟进确认",
        "next_follow_up": None,
        "status": "following",
        "rationale": "规则兜底",
        "source": "rule",
    }
    system = (
        "把自然语言整理成卡点 JSON："
        '{"description":"...","responsible_party":"...","party_type":"third_party|customer|internal|approval",'
        '"actions_taken":"...","next_action":"...","next_follow_up":"YYYY-MM-DD或null","status":"following",'
        '"rationale":"..."}。不要 markdown。'
    )
    data, source = await run_structured(
        db, purpose="blocker_draft", system=system, user=f"用户：{raw}\n项目id={project_id}\n只输出 JSON。"
    )
    if not data:
        return fallback
    out = {**fallback, **{k: v for k, v in data.items() if v is not None}}
    if out.get("party_type") not in {"third_party", "customer", "internal", "approval"}:
        out["party_type"] = "internal"
    out["source"] = source
    return out


async def overview_brief(db: AsyncSession, *, project_ids: list[int]) -> dict[str, Any]:
    rule_summary, focus, stats = await _overview_rule_context(db, project_ids)
    if not project_ids:
        return {"summary": rule_summary, "focus": focus, "source": "rule", "stats": stats}

    fallback = {"summary": rule_summary, "focus": focus, "source": "rule", "stats": stats}
    system = (
        "根据统计写两句白话总览 + 今日关注点。输出 JSON："
        '{"summary":"...","focus":["...","..."]}。不要 markdown，不要数字堆砌恐吓。'
    )
    user_msg = f"统计：{rule_summary}\n只输出 JSON。"
    data, source = await run_structured(
        db, purpose="overview_brief", system=system, user=user_msg
    )
    if not data:
        return fallback
    return {
        "summary": str(data.get("summary") or rule_summary)[:500],
        "focus": [str(x) for x in (data.get("focus") or focus)][:5],
        "source": source,
        "stats": stats,
    }


async def overview_brief_stream(db: AsyncSession, *, project_ids: list[int]):
    """
    SSE 事件字典生成器：
    - start
    - delta {text}  （仅第一段摘要增量）
    - done {summary, focus, source, stats?}
    """
    rule_summary, focus, stats = await _overview_rule_context(db, project_ids)
    yield {"event": "start", "data": {}}

    if not project_ids:
        yield {
            "event": "done",
            "data": {
                "summary": rule_summary,
                "focus": focus,
                "source": "rule",
                "stats": stats,
            },
        }
        return

    system = (
        "你是中文工作台助手。直接输出给员工看的正文，禁止输出思考过程、"
        "英文分析、<think> 标签或自我解释。"
        "先写一段中文白话总览（不要列表、不要 JSON、不要复述统计数字），"
        "然后空一行，再每行一个行动关注点，格式为「- 关注点」。"
        "不要 markdown 标题，不要恐吓语气。"
    )
    user_msg = f"统计：{rule_summary}\n请直接按格式输出中文正文。"

    accumulated = ""
    shown = ""
    source = "empty"
    got_delta = False

    async for kind, payload in run_text_stream(
        db,
        purpose="overview_brief",
        system=system,
        user=user_msg,
        max_tokens=600,
    ):
        if kind == "delta":
            got_delta = True
            accumulated += str(payload)
            visible = _summary_visible_while_streaming(accumulated)
            # 思考阶段 visible 为空：不推 delta，前端继续显示「正在整理…」
            if len(visible) > len(shown):
                chunk = visible[len(shown) :]
                shown = visible
                yield {"event": "delta", "data": {"text": chunk}}
            elif len(visible) < len(shown):
                # 解析修正导致缩短：用 done 校正，流式阶段不回退闪烁
                pass
        elif kind == "done":
            source = str(payload)

    if source == "llm" and accumulated.strip():
        summary, parsed_focus = _parse_brief_plain_text(accumulated, focus)
        if not summary:
            summary = rule_summary
            parsed_focus = focus
        yield {
            "event": "done",
            "data": {
                "summary": summary[:500],
                "focus": parsed_focus[:5],
                "source": "llm" if got_delta else source,
                "stats": stats,
            },
        }
    else:
        # 无模型 / 失败：立刻规则兜底（若已流过部分则用已显示文案校正）
        summary = shown.strip() if shown.strip() and not _looks_like_model_scratchpad(shown) else rule_summary
        yield {
            "event": "done",
            "data": {
                "summary": summary[:500],
                "focus": focus,
                "source": "rule" if source in ("empty", "error") else source,
                "stats": stats,
            },
        }


async def search_solutions_from_query(db: AsyncSession, text: str) -> dict[str, Any]:
    """问句 → 关键词/标签建议 + 命中方案列表（不自动改库）。"""
    raw = (text or "").strip()
    keywords = [w for w in re.split(r"[\s，,。？?、]+", raw) if len(w) >= 2][:6]
    fallback = {
        "keywords": keywords,
        "suggested_tags": keywords[:3],
        "rationale": "规则拆词检索",
        "source": "rule",
        "items": [],
    }
    system = (
        "把用户问题转成方案库检索词。输出 JSON："
        '{"keywords":["..."],"suggested_tags":["..."],"rationale":"一句说明"}。'
        "关键词要短、可检索。不要 markdown。"
    )
    data, source = await run_structured(
        db, purpose="solution_search", system=system, user=f"用户问：{raw}\n只输出 JSON。"
    )
    if data:
        kw = [str(x).strip() for x in (data.get("keywords") or keywords) if str(x).strip()]
        tags = [
            str(x).strip()
            for x in (data.get("suggested_tags") or kw)[:5]
            if str(x).strip()
        ]
        keywords = kw[:8] or keywords
        fallback["keywords"] = keywords
        fallback["suggested_tags"] = tags
        fallback["rationale"] = str(data.get("rationale") or "")[:200]
        fallback["source"] = source

    clauses = []
    for w in keywords[:5]:
        like = f"%{w}%"
        clauses.append(Solution.title.ilike(like))
        clauses.append(Solution.summary.ilike(like))
        clauses.append(Solution.original_filename.ilike(like))
    if not clauses:
        return fallback

    rows = (
        await db.execute(
            select(Solution)
            .options(selectinload(Solution.tags))
            .where(Solution.status == "ready", or_(*clauses))
            .order_by(Solution.updated_at.desc())
            .limit(12)
        )
    ).scalars().all()
    fallback["items"] = [
        {
            "id": s.id,
            "title": s.title or s.original_filename,
            "summary": (s.summary or "")[:200],
            "tags": [t.name for t in (s.tags or [])][:8],
        }
        for s in rows
    ]
    return fallback
