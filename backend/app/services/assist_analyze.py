"""管理员分析管控：串联项目 / 任务 / 周报，产出进度质量结论与指导催办清单。"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import (
    AssistGuidanceNote,
    Blocker,
    Notification,
    ProgressReport,
    ProgressReportItem,
    ProgressReportReview,
    Project,
    Task,
    User,
)
from app.services.ai_platform import run_structured, run_text_stream
from app.services.project_service import get_overview_stats, list_projects, list_visible_project_rows
from app.services.report_quality import monday_of, sunday_of, validate_report_items
from app.services.report_roster import list_report_roster_users
from app.services.task_notify import notify_task_remind
from app.services.task_service import KEY_TASK_PRIORITIES, load_task

ALLOWED_NUDGE_TYPES = {
    "report_missing",
    "report_quality",
    "task_pending_accept",
    "task_overdue",
    "blocker_open",
}


def _user_brief(u: User | None) -> dict[str, Any] | None:
    if not u:
        return None
    return {"id": u.id, "name": u.name or u.username, "username": u.username}


def _deep_links(*, user_id: int | None = None, project_id: int | None = None, task_id: int | None = None) -> dict[str, str]:
    links: dict[str, str] = {}
    if user_id is not None:
        links["person"] = f"/reports/person/{user_id}"
        links["reports"] = f"/reports?tab=board"
    if project_id is not None:
        links["project"] = f"/projects/{project_id}"
    if task_id is not None:
        links["task"] = f"/tasks/{task_id}"
    return links


def format_prompt_pack(kind: str, payload: dict[str, Any]) -> str:
    """生成可粘贴到智能助手的提问稿（脱敏：仅显示名与业务摘要）。"""
    lines = [
        "请根据以下工作台材料，帮我分析进度与质量风险，并给出可执行的指导与催办建议。",
        f"分析范围：{kind}",
        "",
    ]
    summary = payload.get("summary") or payload.get("conclusion") or ""
    if summary:
        lines.append(f"结论摘要：{summary}")
        lines.append("")
    focus = payload.get("focus") or payload.get("guidance_draft") or []
    if isinstance(focus, list) and focus:
        lines.append("关注点：")
        for item in focus[:12]:
            lines.append(f"- {item}")
        lines.append("")
    actions = payload.get("actions") or []
    if isinstance(actions, list) and actions:
        lines.append("待办动作：")
        for a in actions[:15]:
            if isinstance(a, dict):
                lines.append(f"- [{a.get('priority', 'normal')}] {a.get('title', '')}：{a.get('detail', '')}")
            else:
                lines.append(f"- {a}")
        lines.append("")
    lines.append("请用简体中文回答，分：结论、风险、建议动作、对相关人员的指导话术。")
    return "\n".join(lines).strip()


async def _week_bounds(today: date | None = None) -> tuple[date, date]:
    d = today or date.today()
    start = monday_of(d)
    return start, sunday_of(d)


async def _roster_submission(
    db: AsyncSession, week_start: date
) -> tuple[list[User], set[int], list[ProgressReport]]:
    roster = await list_report_roster_users(db)
    if not roster:
        return [], set(), []
    ids = [u.id for u in roster]
    rows = await db.execute(
        select(ProgressReport)
        .options(selectinload(ProgressReport.items), selectinload(ProgressReport.author))
        .where(
            ProgressReport.user_id.in_(ids),
            ProgressReport.period_type == "week",
            ProgressReport.period_start == week_start,
            ProgressReport.status == "submitted",
        )
    )
    reports = list(rows.scalars().all())
    submitted_ids = {r.user_id for r in reports}
    return roster, submitted_ids, reports


async def _open_blockers(db: AsyncSession, project_ids: list[int] | None = None) -> list[Blocker]:
    q = (
        select(Blocker)
        .options(selectinload(Blocker.assignee), selectinload(Blocker.project))
        .where(Blocker.status != "resolved")
        .order_by(Blocker.updated_at.desc())
        .limit(40)
    )
    if project_ids is not None:
        if not project_ids:
            return []
        q = q.where(Blocker.project_id.in_(project_ids))
    result = await db.execute(q)
    return list(result.scalars().all())


async def _task_alerts(
    db: AsyncSession,
    *,
    project_ids: list[int] | None = None,
    assignee_id: int | None = None,
) -> dict[str, list[Task]]:
    today = date.today()
    base = (
        select(Task)
        .options(selectinload(Task.assignee), selectinload(Task.project))
        .where(Task.status != "completed")
        .order_by(Task.updated_at.desc())
        .limit(80)
    )
    if project_ids is not None:
        if not project_ids:
            return {"pending_accept": [], "overdue": [], "key_open": []}
        base = base.where(Task.project_id.in_(project_ids))
    if assignee_id is not None:
        base = base.where(Task.assignee_id == assignee_id)
    result = await db.execute(base)
    tasks = list(result.scalars().all())
    pending_accept = [t for t in tasks if (t.acceptance_status or "") == "pending"]
    overdue = [
        t
        for t in tasks
        if t.due_date is not None and t.due_date < today and (t.acceptance_status or "") != "pending"
    ]
    key_open = [t for t in tasks if (t.priority or "") in KEY_TASK_PRIORITIES]
    return {"pending_accept": pending_accept, "overdue": overdue, "key_open": key_open}


def _task_brief(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title or "",
        "status": t.status,
        "priority": t.priority,
        "acceptance_status": t.acceptance_status,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "assignee": _user_brief(t.assignee),
        "project_id": t.project_id,
        "project_name": t.project.name if t.project else None,
        "links": _deep_links(task_id=t.id, project_id=t.project_id, user_id=t.assignee_id),
    }


def _blocker_brief(b: Blocker) -> dict[str, Any]:
    return {
        "id": b.id,
        "description": (b.description or "")[:200],
        "status": b.status,
        "party_type": (b.party_type or "internal"),
        "next_action": (b.next_action or "")[:120],
        "next_follow_up": b.next_follow_up.isoformat() if b.next_follow_up else None,
        "project_id": b.project_id,
        "project_name": b.project.name if b.project else None,
        "assignee": _user_brief(b.assignee),
        "links": _deep_links(project_id=b.project_id, user_id=b.assignee_id),
    }


def _issue_nature_for_action(action_type: str, party_type: str | None = None) -> str:
    """问题性质：person / external / process / unclear。"""
    if action_type in {
        "report_missing",
        "task_pending_accept",
        "task_overdue",
        "report_quality",
    }:
        return "person"
    if action_type == "blocker_open":
        pt = (party_type or "").strip() or "internal"
        if pt in {"customer", "third_party"}:
            return "external"
        if pt == "approval":
            return "process"
        if pt == "internal":
            return "person"
        return "unclear"
    return "unclear"


def _guess_focus_nature(text: str) -> str:
    t = text or ""
    if any(k in t for k in ("未交", "周报", "逾期", "待接收", "质量")):
        return "person"
    if any(k in t for k in ("第三方", "客户", "外部")):
        return "external"
    if any(k in t for k in ("审批", "流程")):
        return "process"
    if "卡点" in t:
        return "unclear"
    return "unclear"


def _is_coaching_focus_line(text: str) -> bool:
    """过滤掉指标复读，只保留可当指导话术的句子。"""
    t = (text or "").strip()
    if not t:
        return False
    # 纯状态复读
    if t.startswith("近 ") and "提交" in t:
        return False
    if t in {"本周周报已提交", "本周周报未提交"}:
        return False
    if t.endswith("条") and ("周报" in t or "卡点" in t or "任务" in t):
        return False
    if "提交" in t and "/" in t and len(t) < 40:
        return False
    return True


def _build_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """驾驶舱三问：是否按时 / 最大风险 / 需要决策。"""
    stats = payload.get("stats") or {}
    kind = str(payload.get("kind") or "")
    overdue = int(stats.get("overdue_tasks") or 0)
    blockers_n = int(stats.get("open_blockers") or 0)
    expected = int(stats.get("report_expected") or 0)
    submitted = int(stats.get("report_submitted") or 0)
    missing = max(0, expected - submitted) if expected else 0
    # 人员维：用 this_week_submitted，缺周报计 1
    if "this_week_submitted" in stats:
        if not bool(stats.get("this_week_submitted")):
            missing = max(missing, 1)
        else:
            missing = 0 if expected == 0 else missing
    pending = int(stats.get("pending_accept") or 0)

    # 人员维：缺 1 份周报即可偏黄；团队维仍用 >=2
    missing_yellow = 1 if kind == "person" else 2
    if overdue or blockers_n >= 3 or missing >= missing_yellow or pending >= 3:
        on_track = "偏黄/有风险：存在逾期、卡点或周报缺口，需本周跟进。"
    elif overdue == 0 and blockers_n == 0 and missing == 0 and pending == 0:
        on_track = "整体按时：暂无显著逾期与未关卡点。"
    else:
        on_track = "基本可控：有少量事项需留意。"

    top_risk = None
    for a in payload.get("actions") or []:
        if isinstance(a, dict) and a.get("priority") == "high":
            top_risk = a.get("title") or a.get("detail")
            break
    if not top_risk:
        focus = payload.get("focus") or []
        if focus and isinstance(focus[0], str):
            top_risk = focus[0]
    if not top_risk:
        top_risk = "暂无突出风险"

    decisions: list[dict[str, Any]] = []
    for b in payload.get("blockers") or []:
        if not isinstance(b, dict):
            continue
        pt = b.get("party_type") or ""
        st = b.get("status") or ""
        need = st in {"pending_decision", "escalated"} or pt in {
            "customer",
            "third_party",
            "approval",
        }
        if not need:
            continue
        nature = _issue_nature_for_action("blocker_open", pt)
        decisions.append(
            {
                "title": f"卡点待协调：{(b.get('description') or '')[:40]}",
                "detail": f"项目 {b.get('project_name') or b.get('project_id')} · 性质 {nature}",
                "issue_nature": nature,
                "links": b.get("links") or {},
            }
        )
    for qa in payload.get("quality_alerts") or []:
        if not isinstance(qa, dict):
            continue
        msgs = qa.get("messages") or []
        if not any("升级" in m for m in msgs):
            continue
        uname = (qa.get("user") or {}).get("name") or "某人"
        decisions.append(
            {
                "title": f"周报需升级：{uname}",
                "detail": "；".join(msgs)[:120],
                "issue_nature": "person",
                "links": qa.get("links") or {},
            }
        )
    return {
        "on_track_hint": on_track,
        "top_risk": top_risk,
        "decisions_needed": decisions[:8],
    }


def _finalize_analyze_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """给 actions 打 issue_nature，并附加 snapshot / focus_items。"""
    actions = payload.get("actions") or []
    for a in actions:
        if not isinstance(a, dict):
            continue
        nature = _issue_nature_for_action(
            str(a.get("type") or ""),
            a.get("party_type"),
        )
        a["issue_nature"] = nature
    payload["actions"] = actions
    focus = payload.get("focus") or []
    payload["focus_items"] = [
        {"text": f, "issue_nature": _guess_focus_nature(f)}
        for f in focus
        if isinstance(f, str)
    ]
    payload["snapshot"] = _build_snapshot(payload)
    return payload


def _quality_alerts_from_reports(reports: list[ProgressReport]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    for r in reports:
        items = [
            {
                "project_id": it.project_id,
                "task_title": it.task_title or "",
                "result_text": it.result_text or "",
                "quant_value": it.quant_value or "",
                "stuck_status": it.stuck_status or "none",
                "stuck_detail": it.stuck_detail or "",
                "stuck_next_action": it.stuck_next_action or "",
                "stuck_follow_up": it.stuck_follow_up,
                "next_goal": it.next_goal or "",
                "next_due_date": it.next_due_date,
                "risk_level": it.risk_level or "normal",
                "risk_reason": it.risk_reason or "",
                "risk_impact_date": it.risk_impact_date,
                "risk_coordinator": it.risk_coordinator or "",
                "no_action": bool(it.no_action),
            }
            for it in (r.items or [])
        ]
        msgs: list[str] = []
        try:
            _ok, hard, soft = validate_report_items(items)
            msgs.extend(hard or [])
            msgs.extend(soft or [])
        except Exception:
            pass
        escalate = any((it.risk_level or "") == "escalate" for it in (r.items or []))
        has_blocker = any((it.stuck_status or "none") in ("blocker", "coordination") for it in (r.items or []))
        if escalate:
            msgs.append("存在需升级事项")
        if has_blocker:
            msgs.append("存在卡点/需协调")
        # 已提交周报：只保留软提示与风险类，硬错误仅作参考（提交后字段可能仍欠完善）
        msgs = list(dict.fromkeys(msgs))[:8]
        if not msgs:
            continue
        alerts.append(
            {
                "user": _user_brief(r.author),
                "report_id": r.id,
                "messages": msgs,
                "links": _deep_links(user_id=r.user_id),
            }
        )
    return alerts


def _parse_analyze_plain(raw: str, fallback_focus: list[str]) -> tuple[str, list[str]]:
    """解析流式白话：首段为结论，其后「- 」行为关注点。"""
    text = (raw or "").strip()
    if not text:
        return "", list(fallback_focus)
    text = re.sub(
        r"<(think|thinking|reasoning|thought)\b[^>]*>[\s\S]*?</\1\s*>",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"<(think|thinking|reasoning|thought)\b[^>]*>[\s\S]*$",
        "",
        text,
        flags=re.I,
    ).strip()
    bullet_re = re.compile(r"^\s*[-•*]\s+(.*)$")
    paragraphs: list[str] = []
    focus: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            paragraphs.append("\n".join(buf).strip())
            buf = []

    for line in text.splitlines():
        m = bullet_re.match(line)
        if m:
            flush()
            item = (m.group(1) or "").strip()
            if item:
                focus.append(item[:120])
            continue
        if not line.strip():
            flush()
            continue
        buf.append(line.strip())
    flush()
    conclusion = paragraphs[0] if paragraphs else ""
    return conclusion[:500], (focus[:8] or list(fallback_focus))


def _visible_conclusion_while_streaming(raw: str) -> str:
    conclusion, _ = _parse_analyze_plain(raw, [])
    return conclusion


async def _enrich_narrative(
    db: AsyncSession, *, kind: str, facts: dict[str, Any]
) -> tuple[str, list[str], str]:
    """返回 conclusion, focus, source。"""
    rule_conclusion = facts.get("rule_conclusion") or "请关注提交、质量告警与逾期任务。"
    rule_focus = list(facts.get("rule_focus") or [])
    if settings.agent_native:
        return rule_conclusion, rule_focus, "rule"
    data, source = await run_structured(
        db,
        purpose="assist_analyze",
        system=(
            "你是项目支撑工作台的管理分析助手。根据给定事实输出 JSON："
            '{"conclusion":"一两句中文结论","focus":["关注点1","关注点2"]}。'
            "不要编造事实，不要打分，不要英文。"
        ),
        user=f"分析类型={kind}\n事实={facts}",
        fallback_purposes=("overview_brief", "report_coach"),
        max_tokens=600,
    )
    if data and isinstance(data.get("conclusion"), str) and data["conclusion"].strip():
        focus = data.get("focus") if isinstance(data.get("focus"), list) else rule_focus
        focus_s = [str(x).strip() for x in focus if str(x).strip()][:8]
        return data["conclusion"].strip(), focus_s or rule_focus, source or "llm"
    return rule_conclusion, rule_focus, "rule"


async def _enrich_narrative_stream(db: AsyncSession, *, kind: str, facts: dict[str, Any]):
    """
    流式生成结论。
    Yields: ("delta", text) | ("final", conclusion, focus, source)
    """
    rule_conclusion = facts.get("rule_conclusion") or "请关注提交、质量告警与逾期任务。"
    rule_focus = list(facts.get("rule_focus") or [])
    if settings.agent_native:
        yield ("final", rule_conclusion, rule_focus, "rule")
        return
    system = (
        "你是中文管理分析助手。直接输出给管理者看的正文，禁止思考过程、英文、"
        "<think> 标签或 JSON。"
        "先写一两句中文结论（不要列表），空一行，再每行一个关注点，格式「- 关注点」。"
        "不要编造事实，不要打分。"
    )
    # 事实里去掉过长字段，控制提示体积
    slim = {
        k: v
        for k, v in facts.items()
        if k not in {"rule_conclusion", "rule_focus"} or k in {"rule_conclusion", "rule_focus"}
    }
    user_msg = f"分析类型={kind}\n事实={slim}\n请直接按格式输出中文正文。"

    accumulated = ""
    shown = ""
    source = "empty"
    got_delta = False

    async for kind_ev, payload in run_text_stream(
        db,
        purpose="assist_analyze",
        system=system,
        user=user_msg,
        fallback_purposes=("overview_brief", "report_coach"),
        max_tokens=600,
        timeout=60.0,
    ):
        if kind_ev == "delta":
            got_delta = True
            accumulated += str(payload)
            visible = _visible_conclusion_while_streaming(accumulated)
            if not visible:
                # 尚未形成首段：直接推原始增量（去掉明显思考标签时的空窗）
                # 若累计仍像思考草稿则不推
                low = accumulated.lower()
                if "<think" in low or "let me" in low:
                    continue
                # 首段未解析出前，用去掉列表行后的全文作可见
                lines = []
                for line in accumulated.splitlines():
                    if line.strip().startswith(("-", "•", "*")):
                        break
                    lines.append(line)
                visible = "\n".join(lines).strip()
            if len(visible) > len(shown):
                chunk = visible[len(shown) :]
                shown = visible
                yield ("delta", chunk)
        elif kind_ev == "done":
            source = str(payload)

    if source == "llm" and accumulated.strip():
        conclusion, focus = _parse_analyze_plain(accumulated, rule_focus)
        if not conclusion:
            conclusion = shown or rule_conclusion
            focus = rule_focus
        yield ("final", conclusion, focus, "llm" if got_delta else source)
    else:
        yield ("final", rule_conclusion, rule_focus, "rule")


async def _stream_analyze_envelope(
    db: AsyncSession,
    *,
    kind: str,
    label: str,
    payload: dict[str, Any],
    facts: dict[str, Any],
):
    """统一 SSE：start → partial(规则骨架) → delta* → done(完整)。"""
    yield {"event": "start", "data": {"kind": kind}}
    # 先出规则骨架，页面立刻有统计与清单
    skeleton = dict(payload)
    skeleton["summary"] = facts.get("rule_conclusion") or skeleton.get("summary") or ""
    skeleton["conclusion"] = skeleton["summary"]
    skeleton["focus"] = list(facts.get("rule_focus") or skeleton.get("focus") or [])
    skeleton["source"] = "rule"
    skeleton["prompt_pack"] = format_prompt_pack(label, skeleton)
    yield {"event": "partial", "data": _finalize_analyze_payload(skeleton)}

    # 清空结论区，准备流式替换（前端用 streaming 标记）
    yield {"event": "narrative_start", "data": {}}

    final_conclusion = skeleton["summary"]
    final_focus = list(skeleton["focus"])
    final_source = "rule"
    async for item in _enrich_narrative_stream(db, kind=kind, facts=facts):
        if item[0] == "delta":
            yield {"event": "delta", "data": {"text": item[1]}}
        elif item[0] == "final":
            final_conclusion, final_focus, final_source = item[1], item[2], item[3]

    done = dict(skeleton)
    done["summary"] = final_conclusion
    done["conclusion"] = final_conclusion
    done["focus"] = final_focus
    done["source"] = final_source
    done["prompt_pack"] = format_prompt_pack(label, done)
    # 人员页：若有 guidance_draft，保持 collect 时的规则稿
    yield {"event": "done", "data": _finalize_analyze_payload(done)}


async def _collect_team(
    db: AsyncSession, user: User, permissions: set[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    week_start, week_end = await _week_bounds()
    stats = await get_overview_stats(db, user, permissions)
    roster, submitted_ids, reports = await _roster_submission(db, week_start)
    missing = [u for u in roster if u.id not in submitted_ids]
    projects = await list_projects(db, user, permissions)
    project_ids = [p.id for p in projects]
    blockers = await _open_blockers(db, project_ids)
    tasks = await _task_alerts(db, project_ids=project_ids)
    quality_alerts = _quality_alerts_from_reports(reports)

    actions: list[dict[str, Any]] = []
    for u in missing:
        actions.append(
            {
                "type": "report_missing",
                "priority": "high",
                "title": f"催交周报：{u.name or u.username}",
                "detail": f"本周（{week_start}～{week_end}）尚未提交周报",
                "user": _user_brief(u),
                "links": _deep_links(user_id=u.id),
            }
        )
    for t in tasks["pending_accept"][:10]:
        actions.append(
            {
                "type": "task_pending_accept",
                "priority": "high",
                "title": f"催接收任务：{t.title}",
                "detail": f"负责人 {t.assignee.name if t.assignee else ''} 尚未接收",
                "user": _user_brief(t.assignee),
                "links": _deep_links(task_id=t.id, user_id=t.assignee_id, project_id=t.project_id),
            }
        )
    for t in tasks["overdue"][:10]:
        actions.append(
            {
                "type": "task_overdue",
                "priority": "high",
                "title": f"逾期任务：{t.title}",
                "detail": f"截止日期 {t.due_date}",
                "user": _user_brief(t.assignee),
                "links": _deep_links(task_id=t.id, user_id=t.assignee_id, project_id=t.project_id),
            }
        )
    for b in blockers[:8]:
        actions.append(
            {
                "type": "blocker_open",
                "priority": "normal",
                "title": f"未关卡点：{(b.description or '')[:40]}",
                "detail": f"项目 {b.project.name if b.project else b.project_id}",
                "party_type": b.party_type or "internal",
                "user": _user_brief(b.assignee),
                "links": _deep_links(project_id=b.project_id, user_id=b.assignee_id),
            }
        )
    for qa in quality_alerts[:8]:
        uname = (qa.get("user") or {}).get("name") or "某人"
        actions.append(
            {
                "type": "report_quality",
                "priority": "normal",
                "title": f"周报质量提醒：{uname}",
                "detail": "；".join(qa.get("messages") or [])[:160],
                "user": qa.get("user"),
                "links": qa.get("links") or {},
            }
        )

    expected = len(roster)
    submitted = len(submitted_ids)
    rule_focus = []
    if missing:
        rule_focus.append(f"{len(missing)} 人未交本周周报")
    if tasks["overdue"]:
        rule_focus.append(f"{len(tasks['overdue'])} 个逾期未完成任务")
    if tasks["pending_accept"]:
        rule_focus.append(f"{len(tasks['pending_accept'])} 个待接收交办")
    if blockers:
        rule_focus.append(f"{len(blockers)} 个未关闭卡点")
    if quality_alerts:
        rule_focus.append(f"{len(quality_alerts)} 份周报有质量提醒")
    if not rule_focus:
        rule_focus.append("本周关键指标平稳，可抽查重点项目进展")

    rule_conclusion = (
        f"本周周报提交 {submitted}/{expected}，"
        f"任务完成率约 {round((stats.task_completion_rate or 0) * 100)}%，"
        f"质量告警 {stats.report_quality_alerts} 条。"
    )
    facts = {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "submitted": submitted,
        "expected": expected,
        "task_completion_rate": stats.task_completion_rate,
        "quality_alerts": stats.report_quality_alerts,
        "open_blockers": len(blockers),
        "overdue_tasks": len(tasks["overdue"]),
        "pending_accept": len(tasks["pending_accept"]),
        "missing_names": [u.name or u.username for u in missing[:12]],
        "rule_conclusion": rule_conclusion,
        "rule_focus": rule_focus,
    }
    payload = {
        "kind": "team",
        "period_start": week_start.isoformat(),
        "period_end": week_end.isoformat(),
        "summary": rule_conclusion,
        "conclusion": rule_conclusion,
        "focus": rule_focus,
        "source": "rule",
        "stats": {
            "report_expected": expected,
            "report_submitted": submitted,
            "report_submit_rate": round(submitted / expected, 4) if expected else 0.0,
            "task_total": stats.task_total,
            "task_done": stats.task_done,
            "task_completion_rate": stats.task_completion_rate,
            "report_quality_alerts": stats.report_quality_alerts,
            "open_blockers": len(blockers),
            "overdue_tasks": len(tasks["overdue"]),
            "pending_accept": len(tasks["pending_accept"]),
        },
        "missing_reporters": [_user_brief(u) for u in missing],
        "quality_alerts": quality_alerts,
        "blockers": [_blocker_brief(b) for b in blockers[:15]],
        "overdue_tasks": [_task_brief(t) for t in tasks["overdue"][:15]],
        "pending_accept_tasks": [_task_brief(t) for t in tasks["pending_accept"][:15]],
        "actions": actions,
        "prompt_pack": "",
    }
    return _finalize_analyze_payload(payload), facts


async def analyze_team(db: AsyncSession, user: User, permissions: set[str]) -> dict[str, Any]:
    payload, facts = await _collect_team(db, user, permissions)
    conclusion, focus, source = await _enrich_narrative(db, kind="team", facts=facts)
    payload["summary"] = conclusion
    payload["conclusion"] = conclusion
    payload["focus"] = focus
    payload["source"] = source
    payload["prompt_pack"] = format_prompt_pack("团队本周", payload)
    return _finalize_analyze_payload(payload)


async def analyze_team_stream(db: AsyncSession, user: User, permissions: set[str]):
    payload, facts = await _collect_team(db, user, permissions)
    async for ev in _stream_analyze_envelope(
        db, kind="team", label="团队本周", payload=payload, facts=facts
    ):
        yield ev


async def analyze_actions(db: AsyncSession, user: User, permissions: set[str]) -> dict[str, Any]:
    team = await analyze_team(db, user, permissions)
    return {
        "kind": "actions",
        "period_start": team["period_start"],
        "period_end": team["period_end"],
        "actions": team.get("actions") or [],
        "summary": team.get("summary") or "",
        "prompt_pack": format_prompt_pack("催办与指导清单", team),
    }


async def _collect_project(
    db: AsyncSession, user: User, permissions: set[str], project_id: int
) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, Any]:
    project = await db.get(Project, project_id)
    if not project:
        return {"error": "not_found"}
    week_start, week_end = await _week_bounds()
    tasks = await _task_alerts(db, project_ids=[project_id])
    blockers = await _open_blockers(db, [project_id])

    total_i = int(
        await db.scalar(select(func.count()).select_from(Task).where(Task.project_id == project_id))
        or 0
    )
    done_i = int(
        await db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.project_id == project_id, Task.status == "completed")
        )
        or 0
    )

    # 本周关联周报：显式挂项目或任务关联
    item_rows = await db.execute(
        select(ProgressReportItem, ProgressReport, User)
        .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
        .join(User, User.id == ProgressReport.user_id)
        .where(
            ProgressReport.period_type == "week",
            ProgressReport.period_start == week_start,
            ProgressReport.status == "submitted",
            or_(
                ProgressReportItem.project_id == project_id,
                ProgressReportItem.linked_task_id.in_(
                    select(Task.id).where(Task.project_id == project_id)
                ),
            ),
        )
        .limit(40)
    )
    related: list[dict[str, Any]] = []
    for item, report, author in item_rows.all():
        related.append(
            {
                "report_id": report.id,
                "user": _user_brief(author),
                "task_title": item.task_title or "",
                "result_text": (item.result_text or "")[:240],
                "links": _deep_links(user_id=author.id, project_id=project_id),
            }
        )

    rule_focus: list[str] = []
    if blockers:
        rule_focus.append(f"未关闭卡点 {len(blockers)} 个")
    if tasks["overdue"]:
        rule_focus.append(f"逾期任务 {len(tasks['overdue'])} 个")
    if tasks["pending_accept"]:
        rule_focus.append(f"待接收任务 {len(tasks['pending_accept'])} 个")
    if not related:
        rule_focus.append("本周尚无明确关联的已交周报条目")
    else:
        rule_focus.append(f"本周关联周报条目 {len(related)} 条")
    if not rule_focus:
        rule_focus.append("项目指标平稳，可核对阶段目标是否兑现")

    rate = round(done_i / total_i, 4) if total_i else 0.0
    rule_conclusion = (
        f"项目「{project.name}」任务完成 {done_i}/{total_i}"
        f"（{round(rate * 100)}%），未关卡点 {len(blockers)}，本周关联周报 {len(related)} 条。"
    )
    facts = {
        "project_name": project.name,
        "level": project.level,
        "status": project.status,
        "current_phase": project.current_phase,
        "task_done": done_i,
        "task_total": total_i,
        "open_blockers": len(blockers),
        "related_report_items": len(related),
        "rule_conclusion": rule_conclusion,
        "rule_focus": rule_focus,
    }

    actions: list[dict[str, Any]] = []
    for t in tasks["overdue"][:8]:
        actions.append(
            {
                "type": "task_overdue",
                "priority": "high",
                "title": f"逾期：{t.title}",
                "detail": f"截止 {t.due_date}",
                "user": _user_brief(t.assignee),
                "links": _deep_links(task_id=t.id, project_id=project_id, user_id=t.assignee_id),
            }
        )
    for b in blockers[:8]:
        actions.append(
            {
                "type": "blocker_open",
                "priority": "high",
                "title": f"卡点：{(b.description or '')[:40]}",
                "detail": b.status,
                "party_type": b.party_type or "internal",
                "user": _user_brief(b.assignee),
                "links": _deep_links(project_id=project_id, user_id=b.assignee_id),
            }
        )

    payload = {
        "kind": "project",
        "project": {
            "id": project.id,
            "name": project.name,
            "level": project.level,
            "status": project.status,
            "current_phase": project.current_phase,
            "links": _deep_links(project_id=project.id),
        },
        "period_start": week_start.isoformat(),
        "period_end": week_end.isoformat(),
        "summary": rule_conclusion,
        "conclusion": rule_conclusion,
        "focus": rule_focus,
        "source": "rule",
        "stats": {
            "task_total": total_i,
            "task_done": done_i,
            "task_completion_rate": rate,
            "open_blockers": len(blockers),
            "related_report_items": len(related),
            "overdue_tasks": len(tasks["overdue"]),
            "pending_accept": len(tasks["pending_accept"]),
        },
        "related_reports": related,
        "blockers": [_blocker_brief(b) for b in blockers],
        "overdue_tasks": [_task_brief(t) for t in tasks["overdue"]],
        "pending_accept_tasks": [_task_brief(t) for t in tasks["pending_accept"]],
        "actions": actions,
        "prompt_pack": "",
    }
    return _finalize_analyze_payload(payload), facts


async def analyze_project(
    db: AsyncSession, user: User, permissions: set[str], project_id: int
) -> dict[str, Any]:
    collected = await _collect_project(db, user, permissions, project_id)
    if isinstance(collected, dict) and collected.get("error"):
        return collected
    payload, facts = collected  # type: ignore[misc]
    conclusion, focus, source = await _enrich_narrative(db, kind="project", facts=facts)
    payload["summary"] = conclusion
    payload["conclusion"] = conclusion
    payload["focus"] = focus
    payload["source"] = source
    name = (payload.get("project") or {}).get("name") or "项目"
    payload["prompt_pack"] = format_prompt_pack(f"项目「{name}」", payload)
    return _finalize_analyze_payload(payload)


async def analyze_project_stream(
    db: AsyncSession, user: User, permissions: set[str], project_id: int
):
    collected = await _collect_project(db, user, permissions, project_id)
    if isinstance(collected, dict) and collected.get("error"):
        yield {"event": "error", "data": {"detail": "项目不存在"}}
        return
    payload, facts = collected  # type: ignore[misc]
    name = (payload.get("project") or {}).get("name") or "项目"
    async for ev in _stream_analyze_envelope(
        db, kind="project", label=f"项目「{name}」", payload=payload, facts=facts
    ):
        yield ev


async def _collect_person(
    db: AsyncSession, user: User, permissions: set[str], target_user_id: int
) -> tuple[dict[str, Any], dict[str, Any]] | dict[str, Any]:
    target = await db.get(User, target_user_id)
    if not target or not target.is_active:
        return {"error": "not_found"}
    week_start, week_end = await _week_bounds()
    earliest = week_start - timedelta(days=7 * 5)

    reports = list(
        (
            await db.execute(
                select(ProgressReport)
                .options(selectinload(ProgressReport.items))
                .where(
                    ProgressReport.user_id == target_user_id,
                    ProgressReport.period_type == "week",
                    ProgressReport.period_start >= earliest,
                    ProgressReport.status == "submitted",
                )
                .order_by(ProgressReport.period_start.desc())
            )
        )
        .scalars()
        .all()
    )
    submitted_starts = {r.period_start for r in reports}
    weeks_back = 6
    expected_weeks = [week_start - timedelta(days=7 * i) for i in range(weeks_back)]
    submitted_count = sum(1 for w in expected_weeks if w in submitted_starts)
    this_week_submitted = week_start in submitted_starts

    review_row = None
    this_report = next((r for r in reports if r.period_start == week_start), None)
    if this_report:
        review_row = (
            await db.execute(
                select(ProgressReportReview).where(
                    ProgressReportReview.report_id == this_report.id,
                    ProgressReportReview.phase == 1,
                )
            )
        ).scalar_one_or_none()

    tasks = await _task_alerts(db, assignee_id=target_user_id)
    blockers = list(
        (
            await db.execute(
                select(Blocker)
                .options(selectinload(Blocker.project), selectinload(Blocker.assignee))
                .where(Blocker.assignee_id == target_user_id, Blocker.status != "resolved")
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    quality_msgs: list[str] = []
    if this_report:
        for qa in _quality_alerts_from_reports([this_report]):
            quality_msgs.extend(qa.get("messages") or [])

    rule_focus: list[str] = []
    if not this_week_submitted:
        rule_focus.append("本周周报未提交")
    else:
        rule_focus.append("本周周报已提交")
    rule_focus.append(f"近 {weeks_back} 周提交 {submitted_count}/{weeks_back}")
    if tasks["overdue"]:
        rule_focus.append(f"逾期任务 {len(tasks['overdue'])} 个")
    if tasks["pending_accept"]:
        rule_focus.append(f"待接收交办 {len(tasks['pending_accept'])} 个")
    if blockers:
        rule_focus.append(f"名下未关卡点 {len(blockers)} 个")
    if quality_msgs:
        rule_focus.append("本周周报存在质量提醒")
    if review_row and (review_row.risks or []):
        rule_focus.append("一阶段评价提到风险点")

    display = target.name or target.username
    rule_conclusion = (
        f"{display} 近 {weeks_back} 周周报提交 {submitted_count}/{weeks_back}，"
        f"本周{'已交' if this_week_submitted else '未交'}；"
        f"逾期任务 {len(tasks['overdue'])}，待接收 {len(tasks['pending_accept'])}。"
    )
    guidance_bits = []
    if not this_week_submitted:
        guidance_bits.append("请本周内补交周报，写清做成了什么、卡点与下周计划。")
    if tasks["overdue"]:
        guidance_bits.append("优先处理逾期任务，完成后补上结果说明。")
    if tasks["pending_accept"]:
        guidance_bits.append("请尽快接收交办任务，避免阻塞协作。")
    if quality_msgs:
        guidance_bits.append("周报请避免空话，尽量带可核对的结果与数字。")
    if not guidance_bits:
        guidance_bits.append("保持当前节奏，重点项目进展可在周报里写得更可核对。")

    facts = {
        "name": display,
        "this_week_submitted": this_week_submitted,
        "submit_rate": f"{submitted_count}/{weeks_back}",
        "overdue": len(tasks["overdue"]),
        "pending_accept": len(tasks["pending_accept"]),
        "open_blockers": len(blockers),
        "phase1_summary": (review_row.summary if review_row else "")[:300],
        "rule_conclusion": rule_conclusion,
        "rule_focus": rule_focus,
    }

    guidance_draft = list(guidance_bits)

    recent_guidance = list(
        (
            await db.execute(
                select(AssistGuidanceNote)
                .where(AssistGuidanceNote.target_user_id == target_user_id)
                .order_by(AssistGuidanceNote.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )

    actions: list[dict[str, Any]] = []
    if not this_week_submitted:
        actions.append(
            {
                "type": "report_missing",
                "priority": "high",
                "title": f"催交周报：{display}",
                "detail": f"{week_start}～{week_end}",
                "user": _user_brief(target),
                "links": _deep_links(user_id=target.id),
            }
        )
    for t in tasks["overdue"][:8]:
        actions.append(
            {
                "type": "task_overdue",
                "priority": "high",
                "title": f"逾期：{t.title}",
                "detail": f"截止 {t.due_date}",
                "user": _user_brief(target),
                "links": _deep_links(task_id=t.id, user_id=target.id, project_id=t.project_id),
            }
        )

    payload = {
        "kind": "person",
        "user": _user_brief(target),
        "period_start": week_start.isoformat(),
        "period_end": week_end.isoformat(),
        "summary": rule_conclusion,
        "conclusion": rule_conclusion,
        "focus": rule_focus,
        "source": "rule",
        "stats": {
            "weeks": weeks_back,
            "submitted_weeks": submitted_count,
            "this_week_submitted": this_week_submitted,
            "overdue_tasks": len(tasks["overdue"]),
            "pending_accept": len(tasks["pending_accept"]),
            "open_blockers": len(blockers),
        },
        "phase1_review": (
            {
                "summary": review_row.summary or "",
                "strengths": list(review_row.strengths or []),
                "risks": list(review_row.risks or []),
                "completion_comment": review_row.completion_comment or "",
                "source": review_row.source,
            }
            if review_row
            else None
        ),
        "quality_messages": quality_msgs,
        "guidance_draft": guidance_draft,
        "overdue_tasks": [_task_brief(t) for t in tasks["overdue"]],
        "pending_accept_tasks": [_task_brief(t) for t in tasks["pending_accept"]],
        "blockers": [_blocker_brief(b) for b in blockers],
        "recent_guidance": [_guidance_out(n) for n in recent_guidance],
        "actions": actions,
        "prompt_pack": "",
    }
    return _finalize_analyze_payload(payload), facts


async def analyze_person(
    db: AsyncSession, user: User, permissions: set[str], target_user_id: int
) -> dict[str, Any]:
    collected = await _collect_person(db, user, permissions, target_user_id)
    if isinstance(collected, dict) and collected.get("error"):
        return collected
    payload, facts = collected  # type: ignore[misc]
    conclusion, focus, source = await _enrich_narrative(db, kind="person", facts=facts)
    payload["summary"] = conclusion
    payload["conclusion"] = conclusion
    payload["focus"] = focus
    payload["source"] = source
    guidance_draft = list(payload.get("guidance_draft") or [])
    for f in focus:
        if (
            isinstance(f, str)
            and _is_coaching_focus_line(f)
            and f not in guidance_draft
            and len(guidance_draft) < 6
        ):
            guidance_draft.append(f)
    payload["guidance_draft"] = guidance_draft
    display = (payload.get("user") or {}).get("name") or "人员"
    payload["prompt_pack"] = format_prompt_pack(f"人员「{display}」", payload)
    return _finalize_analyze_payload(payload)


async def analyze_person_stream(
    db: AsyncSession, user: User, permissions: set[str], target_user_id: int
):
    collected = await _collect_person(db, user, permissions, target_user_id)
    if isinstance(collected, dict) and collected.get("error"):
        yield {"event": "error", "data": {"detail": "用户不存在"}}
        return
    payload, facts = collected  # type: ignore[misc]
    display = (payload.get("user") or {}).get("name") or "人员"

    async def _envelope():
        async for ev in _stream_analyze_envelope(
            db, kind="person", label=f"人员「{display}」", payload=payload, facts=facts
        ):
            if ev.get("event") == "done":
                data = dict(ev.get("data") or {})
                guidance_draft = list(data.get("guidance_draft") or [])
                for f in data.get("focus") or []:
                    if (
                        isinstance(f, str)
                        and _is_coaching_focus_line(f)
                        and f not in guidance_draft
                        and len(guidance_draft) < 6
                    ):
                        guidance_draft.append(f)
                data["guidance_draft"] = guidance_draft
                data["prompt_pack"] = format_prompt_pack(f"人员「{display}」", data)
                yield {"event": "done", "data": data}
            else:
                yield ev

    async for ev in _envelope():
        yield ev


def _guidance_out(n: AssistGuidanceNote) -> dict[str, Any]:
    return {
        "id": n.id,
        "target_user_id": n.target_user_id,
        "project_id": n.project_id,
        "period_start": n.period_start.isoformat() if n.period_start else None,
        "content": n.content or "",
        "action_items": list(n.action_items or []),
        "source": n.source or "admin",
        "created_by_id": n.created_by_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "read_at": n.read_at.isoformat() if getattr(n, "read_at", None) else None,
        "improved_at": n.improved_at.isoformat() if getattr(n, "improved_at", None) else None,
        "improve_note": getattr(n, "improve_note", None) or "",
    }


async def save_guidance(
    db: AsyncSession,
    *,
    admin: User,
    target_user_id: int,
    content: str,
    action_items: list[Any] | None = None,
    project_id: int | None = None,
    period_start: date | None = None,
    source: str = "admin",
    notify: bool = False,
) -> dict[str, Any]:
    target = await db.get(User, target_user_id)
    if not target or not target.is_active:
        return {"error": "not_found"}
    text = (content or "").strip()
    if not text:
        return {"error": "empty"}
    note = AssistGuidanceNote(
        target_user_id=target_user_id,
        project_id=project_id,
        period_start=period_start or monday_of(date.today()),
        content=text[:4000],
        action_items=list(action_items or [])[:20],
        source=source if source in {"ai", "admin"} else "admin",
        created_by_id=admin.id,
    )
    db.add(note)
    await db.flush()
    if notify:
        db.add(
            Notification(
                user_id=target_user_id,
                title="收到一条工作指导",
                content=text[:500],
            )
        )
    await db.commit()
    await db.refresh(note)
    return {"ok": True, "note": _guidance_out(note)}


async def list_my_guidance(
    db: AsyncSession, user: User, *, limit: int = 30
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(AssistGuidanceNote)
            .options(selectinload(AssistGuidanceNote.created_by))
            .where(AssistGuidanceNote.target_user_id == user.id)
            .order_by(AssistGuidanceNote.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    out = []
    for n in rows:
        item = _guidance_out(n)
        item["created_by"] = _user_brief(n.created_by)
        out.append(item)
    return out


async def mark_guidance_read(
    db: AsyncSession, user: User, note_id: int
) -> dict[str, Any]:
    note = await db.get(AssistGuidanceNote, note_id)
    if not note or note.target_user_id != user.id:
        return {"error": "not_found"}
    if not note.read_at:
        note.read_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(note)
    return {"ok": True, "note": _guidance_out(note)}


async def mark_guidance_improved(
    db: AsyncSession, user: User, note_id: int, *, improve_note: str = ""
) -> dict[str, Any]:
    note = await db.get(AssistGuidanceNote, note_id)
    if not note or note.target_user_id != user.id:
        return {"error": "not_found"}
    now = datetime.now(timezone.utc)
    if not note.read_at:
        note.read_at = now
    note.improved_at = now
    note.improve_note = (improve_note or "").strip()[:500]
    await db.commit()
    await db.refresh(note)
    return {"ok": True, "note": _guidance_out(note)}


async def nudge_action(
    db: AsyncSession,
    *,
    actor: User,
    action_type: str,
    user_id: int | None = None,
    task_id: int | None = None,
    project_id: int | None = None,
    title: str = "",
    detail: str = "",
) -> dict[str, Any]:
    """分析管控一键催办：按动作类型发送通知。"""
    atype = (action_type or "").strip()
    actor_name = actor.name or actor.username

    if atype not in ALLOWED_NUDGE_TYPES:
        return {"error": "bad_request", "detail": "不支持的催办类型"}

    if atype == "task_pending_accept":
        if not task_id:
            return {"error": "bad_request", "detail": "缺少 task_id"}
        task = await load_task(db, task_id, detail=True)
        if not task:
            return {"error": "not_found", "detail": "任务不存在"}
        if (task.acceptance_status or "") != "pending":
            return {"error": "bad_request", "detail": "任务已不在待接收状态"}
        await notify_task_remind(db, task, actor_name=actor_name)
        await db.commit()
        return {"ok": True, "channel": "task_remind"}

    target_id = user_id
    if not target_id and task_id:
        task = await load_task(db, task_id, detail=False)
        if task:
            target_id = task.assignee_id
    if not target_id and project_id and atype == "blocker_open":
        # 卡点催办：通知项目主负责人
        proj = await db.get(Project, project_id)
        if proj:
            target_id = proj.owner_id

    if not target_id:
        return {"error": "bad_request", "detail": "缺少催办对象"}

    # 周报类催办：目标必须在应提交名册中
    if atype in {"report_missing", "report_quality"}:
        roster = await list_report_roster_users(db)
        roster_ids = {u.id for u in roster}
        if target_id not in roster_ids:
            return {"error": "bad_request", "detail": "催办对象不在本周应提交名册"}

    # 任务逾期：必须存在对应未完成任务且负责人匹配
    if atype == "task_overdue":
        if not task_id:
            return {"error": "bad_request", "detail": "缺少 task_id"}
        task = await load_task(db, task_id, detail=False)
        if not task or task.status == "completed":
            return {"error": "bad_request", "detail": "任务不存在或已完成"}
        if task.assignee_id != target_id:
            return {"error": "bad_request", "detail": "催办对象与任务负责人不一致"}

    # 卡点：必须有未关闭卡点挂在该项目
    if atype == "blocker_open":
        if not project_id:
            return {"error": "bad_request", "detail": "缺少 project_id"}
        open_cnt = await db.scalar(
            select(func.count())
            .select_from(Blocker)
            .where(Blocker.project_id == project_id, Blocker.status != "resolved")
        )
        if not int(open_cnt or 0):
            return {"error": "bad_request", "detail": "该项目无未关闭卡点"}

    target = await db.get(User, target_id)
    if not target or not target.is_active:
        return {"error": "not_found", "detail": "用户不存在"}

    link_hint = ""
    if task_id:
        link_hint = f" 任务入口：/tasks/{task_id}"
    elif project_id:
        link_hint = f" 项目入口：/projects/{project_id}"
    elif target_id:
        link_hint = f" 周报入口：/reports"

    titles = {
        "report_missing": "请尽快提交本周周报",
        "report_quality": "请完善周报质量",
        "task_overdue": "请尽快处理逾期任务",
        "blocker_open": "请跟进未关闭卡点",
    }
    # 固定标题，不用调用方自定义文案当通知标题（防钓鱼）
    notify_title = titles[atype]
    body = (detail or title or notify_title).strip()[:400]
    content = f"{actor_name} 催办：{body}{link_hint}".strip()
    db.add(
        Notification(
            user_id=target_id,
            title=notify_title[:128],
            content=content[:2000],
        )
    )
    await db.commit()
    return {"ok": True, "channel": "notification", "user_id": target_id}
