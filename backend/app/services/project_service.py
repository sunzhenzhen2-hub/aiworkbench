from datetime import date
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Blocker,
    BlockerHistory,
    Phase,
    ProgressReport,
    ProgressReportItem,
    Project,
    ProjectLevelChange,
    ProjectMember,
    Task,
    TaskAssistant,
    TaskAttachment,
    TaskEvent,
    TaskReview,
    User,
    WeeklyReport,
)
from app.schemas import BlockerBrief, BlockerOut, OverviewStats, ProjectDetail, ProjectListItem, ProjectListOut, UserBrief
from app.services.project_match import (
    guess_project_id,
    guess_project_type_from_title,
    guess_project_with_context,
    looks_like_daily_work,
    looks_like_named_project,
    suggest_project_name_from_title,
    split_project_and_task,
    canonical_project_name,
    norm_text,
)
from app.services.project_phases import (
    STANDARD_PHASES,
    is_pristine_default_timeline,
    phases_for,
)
from app.services.report_quality import monday_of, sunday_of, validate_report_items
from app.services.report_roster import list_report_roster_users
from app.services.task_attachment_storage import delete_stored as attachment_delete_stored

DAILY_PROJECT_NAME = "日常任务"

_PHASE_STATUSES = {"not_started", "in_progress", "completed"}


async def seed_standard_phases(
    db: AsyncSession,
    project_id: int,
    project_type: str | None = None,
) -> None:
    """为新项目写入规范阶段时间线（已有阶段则跳过）。"""
    existing = (
        await db.execute(select(Phase.id).where(Phase.project_id == project_id).limit(1))
    ).first()
    if existing:
        return
    ptype = project_type
    if not ptype:
        project = await db.get(Project, project_id)
        ptype = project.project_type if project else "software"
    names = phases_for(ptype)
    for i, name in enumerate(names):
        status = "in_progress" if i == 0 else "not_started"
        db.add(
            Phase(
                project_id=project_id,
                name=name,
                status=status,
                sort_order=i,
            )
        )
    await db.flush()


async def _load_project_phases(db: AsyncSession, project_id: int) -> list[Phase]:
    result = await db.execute(
        select(Phase).where(Phase.project_id == project_id).order_by(Phase.sort_order, Phase.id)
    )
    return list(result.scalars().all())


def _apply_phase_index_statuses(rows: list[Phase], index: int, status: str) -> str:
    """
    按目标阶段下标改写整条时间线状态，返回应写入的 current_phase。
    - in_progress：之前已完成、当前进行中、之后未开始
    - completed：含当前及之前已完成；若有下一阶段则下一阶段进行中，否则停在当前名
    - not_started：当前及之后未开始；若有上一阶段则上一阶段进行中
    """
    if not rows or index < 0 or index >= len(rows):
        return rows[index].name if rows and 0 <= index < len(rows) else (STANDARD_PHASES[0])
    if status == "in_progress":
        for i, ph in enumerate(rows):
            if i < index:
                ph.status = "completed"
            elif i == index:
                ph.status = "in_progress"
            else:
                ph.status = "not_started"
        return rows[index].name
    if status == "completed":
        for i, ph in enumerate(rows):
            if i <= index:
                ph.status = "completed"
            elif i == index + 1:
                ph.status = "in_progress"
            else:
                ph.status = "not_started"
        if index + 1 < len(rows):
            return rows[index + 1].name
        return rows[index].name
    # not_started
    for i, ph in enumerate(rows):
        if i >= index:
            ph.status = "not_started"
    if index > 0:
        rows[index - 1].status = "in_progress"
        return rows[index - 1].name
    rows[0].status = "in_progress"
    return rows[0].name


async def sync_project_phase_progress(
    db: AsyncSession,
    project: Project,
    *,
    phase_id: int | None = None,
    phase_name: str | None = None,
    status: str | None = None,
) -> list[Phase]:
    """
    双向同步：改阶段状态或改 current_phase 时，统一整条时间线 + 项目 current_phase。
    未指定 status 时视为将该阶段设为进行中。
    """
    rows = await _load_project_phases(db, project.id)
    if not rows:
        await seed_standard_phases(db, project.id, project.project_type)
        rows = await _load_project_phases(db, project.id)
    if not rows:
        return []

    target_index: int | None = None
    if phase_id is not None:
        for i, ph in enumerate(rows):
            if ph.id == phase_id:
                target_index = i
                break
    elif phase_name is not None:
        name = (phase_name or "").strip()
        for i, ph in enumerate(rows):
            if ph.name == name or name in ph.name or ph.name in name:
                target_index = i
                break

    if target_index is None:
        return rows

    desired = (status or "in_progress").strip()
    if desired not in _PHASE_STATUSES:
        desired = "in_progress"
    project.current_phase = _apply_phase_index_statuses(rows, target_index, desired)
    await db.flush()
    return rows


async def maybe_replace_phases_for_type_change(
    db: AsyncSession,
    project: Project,
    old_type: str | None,
    new_type: str | None,
) -> bool:
    """
    改类型时：若阶段仍是旧类型（或历史）默认未改动模板，则整表换成新类型模板。
    返回是否已替换。
    """
    if (old_type or "") == (new_type or ""):
        return False
    rows = await _load_project_phases(db, project.id)
    if not is_pristine_default_timeline(rows, project_type=old_type):
        return False
    for ph in rows:
        await db.delete(ph)
    await db.flush()
    names = phases_for(new_type)
    for i, name in enumerate(names):
        db.add(
            Phase(
                project_id=project.id,
                name=name,
                status="in_progress" if i == 0 else "not_started",
                sort_order=i,
            )
        )
    project.current_phase = names[0] if names else project.current_phase
    await db.flush()
    return True


async def ensure_daily_project(db: AsyncSession) -> Project:
    """确保存在「日常任务」兜底项目，并把活跃成员加入以便可选。"""
    result = await db.execute(select(Project).where(Project.name == DAILY_PROJECT_NAME))
    project = result.scalar_one_or_none()

    admin = (
        await db.execute(select(User).where(User.username == "admin"))
    ).scalar_one_or_none()
    owner_id = admin.id if admin else None
    if owner_id is None:
        any_user = (await db.execute(select(User).order_by(User.id).limit(1))).scalar_one_or_none()
        if not any_user:
            raise RuntimeError("无法创建日常任务：系统无用户")
        owner_id = any_user.id

    if not project:
        project = Project(
            name=DAILY_PROJECT_NAME,
            project_type="software",
            level="daily",
            status="normal",
            owner_id=owner_id,
            description="无立项归属的日常/杂项工作统一归入此项目",
        )
        db.add(project)
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=owner_id, member_role="lead"))

    # 活跃成员（含周报名单）都可看到并选择该项目
    users = list(
        (
            await db.execute(select(User).where(User.is_active.is_(True)))
        ).scalars().all()
    )
    existing = await db.execute(
        select(ProjectMember.user_id).where(ProjectMember.project_id == project.id)
    )
    member_ids = {row[0] for row in existing.all()}
    for u in users:
        if u.id in member_ids:
            continue
        role = "lead" if u.id == project.owner_id else "member"
        db.add(ProjectMember(project_id=project.id, user_id=u.id, member_role=role))
        member_ids.add(u.id)

    await db.flush()
    return project


async def _find_or_create_project_by_name(
    db: AsyncSession,
    name: str,
    *,
    owner_id: int,
    cache: dict[str, Project],
    project_type: str = "software",
    source_title: str = "",
) -> Project | None:
    label = (name or "").strip()
    if not label or label == DAILY_PROJECT_NAME:
        return None
    key = norm_text(canonical_project_name(label))
    if key in cache:
        return cache[key]

    # 已有同名或同规范名
    rows = list((await db.execute(select(Project))).scalars().all())
    for p in rows:
        if norm_text(p.name) == key or norm_text(canonical_project_name(p.name)) == key:
            cache[key] = p
            return p
        # 新建名被已有名包含 / 包含已有名（足够长）
        cn = norm_text(canonical_project_name(p.name))
        if len(key) >= 4 and len(cn) >= 4 and (key in cn or cn in key):
            cache[key] = p
            return p

    ptype = project_type or guess_project_type_from_title(source_title or label)
    phase_names = phases_for(ptype)
    project = Project(
        name=label[:128],
        project_type=ptype,
        level="normal",
        status="normal",
        current_phase=phase_names[0] if phase_names else STANDARD_PHASES[0],
        owner_id=owner_id,
        description="由历史周报概述自动建档（请核对是否与立项名称一致）",
    )
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=owner_id, member_role="lead"))
    await seed_standard_phases(db, project.id, ptype)
    # 把活跃成员都挂上，便于选项目
    active = list(
        (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    )
    for u in active:
        if u.id == owner_id:
            continue
        db.add(ProjectMember(project_id=project.id, user_id=u.id, member_role="member"))
    await db.flush()
    cache[key] = project
    return project


async def build_project_context(db: AsyncSession) -> dict[str, Any]:
    """
    一次装好归类所需材料：真实立项、谁和谁是同项目的人、这些人已经归好类的任务原文。
    """
    daily = await ensure_daily_project(db)
    projects = list((await db.execute(select(Project))).scalars().all())
    members = list((await db.execute(select(ProjectMember))).scalars().all())
    by_project: dict[int, set[int]] = {}
    for m in members:
        by_project.setdefault(m.project_id, set()).add(m.user_id)
    related: dict[int, set[int]] = {}
    for uids in by_project.values():
        for uid in uids:
            related.setdefault(uid, set()).update(uids)

    classified: dict[int, list[dict[str, Any]]] = {}

    report_rows = (
        await db.execute(
            select(ProgressReportItem, ProgressReport.user_id)
            .join(ProgressReport, ProgressReport.id == ProgressReportItem.report_id)
            .where(
                ProgressReportItem.project_id.is_not(None),
                ProgressReportItem.project_id != daily.id,
            )
        )
    ).all()
    for item, author_id in report_rows:
        text = f"{item.task_title or ''} {item.result_text or ''}".strip()
        if not text or not author_id or not item.project_id:
            continue
        classified.setdefault(author_id, []).append(
            {"project_id": item.project_id, "text": text}
        )

    task_rows = (
        await db.execute(
            select(Task).where(Task.project_id != daily.id)
        )
    ).scalars().all()
    for task in task_rows:
        text = f"{task.title or ''} {task.goal or ''}".strip()
        if not text:
            continue
        owners = {task.assignee_id}
        if task.created_by_id:
            owners.add(task.created_by_id)
        for uid in owners:
            if not uid:
                continue
            classified.setdefault(uid, []).append(
                {"project_id": task.project_id, "text": text}
            )

    return {
        "daily_id": daily.id,
        "projects": [{"project_id": p.id, "name": p.name} for p in projects],
        "related": related,
        "classified": classified,
    }


def context_items_for_user(bundle: dict[str, Any], user_id: int) -> list[dict[str, Any]]:
    """本人 + 同项目同事已经归好类的任务，当作上下文。"""
    related = bundle["related"].get(user_id) or {user_id}
    items: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for uid in related:
        for row in bundle["classified"].get(uid, []):
            key = (int(row["project_id"]), row["text"])
            if key in seen:
                continue
            seen.add(key)
            items.append(row)
            if len(items) >= 200:
                return items
    return items


def guess_text_in_bundle(
    bundle: dict[str, Any],
    user_id: int,
    task_title: str,
    result_text: str = "",
    *,
    only_project_ids: set[int] | None = None,
) -> tuple[int | None, float, str | None, str]:
    """在已装好的材料上猜测项目。only_project_ids 用于写任务：只能建议自己加入过的项目。"""
    projects = bundle["projects"]
    if only_project_ids is not None:
        projects = [p for p in projects if p["project_id"] in only_project_ids]
    return guess_project_with_context(
        task_title,
        result_text,
        projects,
        context_items_for_user(bundle, user_id),
        exclude_names={DAILY_PROJECT_NAME},
    )


async def guess_project_for_user(
    db: AsyncSession,
    user_id: int,
    task_title: str,
    result_text: str = "",
    *,
    member_only: bool = False,
) -> tuple[int | None, float, str | None, str]:
    bundle = await build_project_context(db)
    only: set[int] | None = None
    if member_only:
        only = {
            m.project_id
            for m in (
                await db.execute(
                    select(ProjectMember).where(ProjectMember.user_id == user_id)
                )
            ).scalars().all()
        }
    return guess_text_in_bundle(
        bundle, user_id, task_title, result_text, only_project_ids=only
    )


async def backfill_null_report_projects(db: AsyncSession) -> dict[str, int]:
    """
    历史周报任务卡归类：
    1) 先匹配已有立项项目（不含「日常任务」）
    2) 明显像立项、又匹配不上的 → 按概述自动建档，不落日常
    3) 仅杂项/行政类 → 「日常任务」
    同时纠正已误挂在「日常任务」上、但明显是立项工作的条目。
    """
    daily = await ensure_daily_project(db)

    stats = {"matched": 0, "created": 0, "daily": 0, "unchanged": 0}

    result = await db.execute(
        select(ProgressReportItem).where(
            or_(
                ProgressReportItem.project_id.is_(None),
                ProgressReportItem.project_id == daily.id,
            )
        )
    )
    items = list(result.scalars().all())
    if not items:
        return stats

    # 负责人按周报作者匹配，禁止新建项目一律挂 admin/孙振振
    report_ids = {it.report_id for it in items}
    reports = {
        r.id: r
        for r in (
            await db.execute(select(ProgressReport).where(ProgressReport.id.in_(report_ids)))
        ).scalars().all()
    }
    admin = (
        await db.execute(select(User).where(User.username == "admin"))
    ).scalar_one_or_none()
    fallback_owner_id = admin.id if admin else daily.owner_id

    create_cache: dict[str, Project] = {}

    all_proj = list((await db.execute(select(Project))).scalars().all())
    for p in all_proj:
        create_cache[norm_text(canonical_project_name(p.name))] = p

    bundle = await build_project_context(db)

    for it in items:
        title = it.task_title or ""
        result_text = it.result_text or ""
        report = reports.get(it.report_id)
        owner_id = report.user_id if report else fallback_owner_id

        pid, _conf, _name, _reason = guess_text_in_bundle(
            bundle,
            owner_id,
            title,
            result_text,
        )
        if pid is not None:
            _pname, task_part = split_project_and_task(title)
            if task_part and (it.task_title or "") == title:
                it.task_title = task_part[:120]
            if it.project_id != pid:
                it.project_id = pid
                stats["matched"] += 1
            else:
                stats["unchanged"] += 1
            continue

        if looks_like_named_project(title, result_text) or (
            title.strip() and not looks_like_daily_work(title, result_text)
        ):
            suggested = suggest_project_name_from_title(title) or title[:64]
            ptype = guess_project_type_from_title(title, result_text)
            before_ids = {p.id for p in create_cache.values()}
            created = await _find_or_create_project_by_name(
                db,
                suggested,
                owner_id=owner_id,
                cache=create_cache,
                project_type=ptype,
                source_title=title,
            )
            if created and created.id != daily.id:
                is_new = created.id not in before_ids
                _pname, task_part = split_project_and_task(title)
                if task_part and (it.task_title or "") == title:
                    it.task_title = task_part[:120]
                if it.project_id != created.id:
                    it.project_id = created.id
                    stats["created" if is_new else "matched"] += 1
                else:
                    stats["unchanged"] += 1
                continue

        if it.project_id != daily.id:
            it.project_id = daily.id
            stats["daily"] += 1
        else:
            stats["unchanged"] += 1

    await db.flush()
    return stats


def _user_brief(user: User) -> UserBrief:
    return UserBrief(id=user.id, name=user.name, username=user.username, avatar_url=user.avatar_url)


def _blocker_brief(blocker: Blocker) -> BlockerBrief:
    return BlockerBrief(
        description=blocker.description,
        party_type=blocker.party_type,
        next_action=blocker.next_action,
        next_follow_up=blocker.next_follow_up,
        status=blocker.status,
    )


def _blocker_out(blocker: Blocker) -> BlockerOut:
    today = date.today()
    return BlockerOut(
        id=blocker.id,
        project_id=blocker.project_id,
        description=blocker.description,
        responsible_party=blocker.responsible_party,
        party_type=blocker.party_type,
        found_date=blocker.found_date,
        actions_taken=blocker.actions_taken,
        synced_to=blocker.synced_to,
        response=blocker.response,
        impact=blocker.impact,
        status=blocker.status,
        next_action=blocker.next_action,
        next_follow_up=blocker.next_follow_up,
        assignee=_user_brief(blocker.assignee),
        is_follow_up_overdue=blocker.next_follow_up < today and blocker.status != "resolved",
        lead_acked_at=blocker.lead_acked_at,
        created_at=blocker.created_at,
        updated_at=blocker.updated_at,
    )


async def _latest_blocker(db: AsyncSession, project_id: int) -> Blocker | None:
    result = await db.execute(
        select(Blocker)
        .options(selectinload(Blocker.assignee))
        .where(Blocker.project_id == project_id, Blocker.status != "resolved")
        .order_by(Blocker.updated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _item_to_validate_dict(it: ProgressReportItem) -> dict[str, Any]:
    return {
        "project_id": it.project_id,
        "task_title": it.task_title,
        "no_action": it.no_action,
        "result_text": it.result_text,
        "quant_value": it.quant_value,
        "stuck_status": it.stuck_status,
        "stuck_detail": it.stuck_detail,
        "stuck_next_action": it.stuck_next_action,
        "stuck_follow_up": it.stuck_follow_up,
        "next_goal": it.next_goal,
        "next_due_date": it.next_due_date,
        "risk_level": it.risk_level,
        "risk_reason": it.risk_reason,
        "risk_impact_date": it.risk_impact_date,
        "risk_coordinator": it.risk_coordinator,
    }


def _effective_project_id(
    it: ProgressReportItem,
    project_list: list[dict[str, Any]],
) -> tuple[int | None, bool]:
    """返回 (有效项目 id, 是否为推断)。"""
    if it.project_id:
        return it.project_id, False
    guessed, _conf, _name = guess_project_id(it.task_title or "", it.result_text or "", project_list)
    if guessed is not None:
        return guessed, True
    return None, False


def _week_quality(*, escalate: int, soft_alerts: int, stuckish: int) -> str:
    if escalate > 0 or stuckish > 0:
        return "bad"
    if soft_alerts > 0:
        return "ok"
    return "good"


async def _load_week_reports(
    db: AsyncSession,
) -> tuple[list[ProgressReport], date, date, bool]:
    """加载本周已提交周报；无数据则切到最近有提交的周期。"""
    today = date.today()
    start = monday_of(today)
    end = sunday_of(start)
    auto_switched = False

    period_rows = await db.execute(
        select(
            ProgressReport.period_start,
            ProgressReport.period_end,
            func.count(ProgressReport.id),
        )
        .where(
            ProgressReport.period_type == "week",
            ProgressReport.status == "submitted",
        )
        .group_by(ProgressReport.period_start, ProgressReport.period_end)
        .order_by(ProgressReport.period_start.desc())
        .limit(16)
    )
    available = period_rows.all()

    async def _by_start(ps: date) -> list[ProgressReport]:
        result = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.period_type == "week",
                ProgressReport.period_start == ps,
                ProgressReport.status == "submitted",
            )
        )
        found = list(result.scalars().all())
        if found:
            return found
        result2 = await db.execute(
            select(ProgressReport)
            .options(
                selectinload(ProgressReport.items),
                selectinload(ProgressReport.author),
            )
            .where(
                ProgressReport.period_type == "week",
                ProgressReport.status == "submitted",
                ProgressReport.period_start <= ps,
            )
        )
        covering: list[ProgressReport] = []
        for r in result2.scalars().all():
            rend = r.period_end or r.period_start
            if r.period_start <= ps <= rend:
                covering.append(r)
        return covering

    reports = await _by_start(start)
    if not reports and available:
        start = available[0][0]
        end = available[0][1] or start
        reports = await _by_start(start)
        auto_switched = True
    elif reports:
        start = min(r.period_start for r in reports)
        end = max((r.period_end or r.period_start) for r in reports)
    return reports, start, end, auto_switched


async def _task_counts_by_project(
    db: AsyncSession, project_ids: list[int]
) -> dict[int, dict[str, int]]:
    if not project_ids:
        return {}
    today = date.today()
    result = await db.execute(select(Task).where(Task.project_id.in_(project_ids)))
    tasks = list(result.scalars().all())
    out: dict[int, dict[str, int]] = {
        pid: {"total": 0, "done": 0, "in_progress": 0, "overdue": 0} for pid in project_ids
    }
    for t in tasks:
        bucket = out.setdefault(
            t.project_id, {"total": 0, "done": 0, "in_progress": 0, "overdue": 0}
        )
        bucket["total"] += 1
        if t.status == "completed":
            bucket["done"] += 1
        elif t.status == "in_progress":
            bucket["in_progress"] += 1
        if t.due_date and t.due_date < today and t.status != "completed":
            bucket["overdue"] += 1
    return out


async def _week_metrics_by_project(
    db: AsyncSession,
    project_ids: list[int],
    *,
    include_report_stats: bool,
) -> tuple[
    dict[int, dict[str, int]],
    int,
    int,
    int,
    int,
    date | None,
    date | None,
    bool,
]:
    """
    返回:
      per_project: {pid: items, inferred, escalate, soft, stuckish}
      unassigned, expected, submitted, quality_alerts, period_start, period_end, auto_switched
    """
    empty_proj = {
        pid: {"items": 0, "inferred": 0, "escalate": 0, "soft": 0, "stuckish": 0}
        for pid in project_ids
    }
    if not include_report_stats:
        return empty_proj, 0, 0, 0, 0, None, None, False

    reports, period_start, period_end, auto_switched = await _load_week_reports(db)

    all_proj_result = await db.execute(select(Project.id, Project.name))
    all_projects = [{"project_id": row[0], "name": row[1]} for row in all_proj_result.all()]
    visible = set(project_ids)

    users_result = await list_report_roster_users(db)
    expected = len(users_result)
    roster_ids = {u.id for u in users_result}
    submitted = len({r.user_id for r in reports if r.user_id in roster_ids})

    per = empty_proj
    unassigned = 0
    quality_alerts = 0

    for r in reports:
        if r.user_id not in roster_ids:
            continue
        _, _, soft = validate_report_items([_item_to_validate_dict(it) for it in r.items])
        quality_alerts += len(soft)

        for it in r.items:
            if it.risk_level == "escalate":
                quality_alerts += 1
            if it.stuck_status in ("blocker", "coordination"):
                quality_alerts += 1

            eff_id, inferred = _effective_project_id(it, all_projects)
            if eff_id is None:
                unassigned += 1
                continue
            if eff_id not in visible:
                continue
            bucket = per[eff_id]
            bucket["items"] += 1
            if inferred:
                bucket["inferred"] += 1
            if it.risk_level == "escalate":
                bucket["escalate"] += 1
            if it.stuck_status in ("blocker", "coordination"):
                bucket["stuckish"] += 1
            _ok, _hard, item_soft = validate_report_items([_item_to_validate_dict(it)])
            if item_soft:
                bucket["soft"] += len(item_soft)

    return (
        per,
        unassigned,
        expected,
        submitted,
        quality_alerts,
        period_start,
        period_end,
        auto_switched,
    )


def _project_list_item(
    project: Project,
    blocker: Blocker | None,
    *,
    task_counts: dict[str, int] | None = None,
    week: dict[str, int] | None = None,
) -> ProjectListItem:
    today = date.today()
    tc = task_counts or {"total": 0, "done": 0, "in_progress": 0, "overdue": 0}
    wk = week or {"items": 0, "inferred": 0, "escalate": 0, "soft": 0, "stuckish": 0}
    return ProjectListItem(
        id=project.id,
        name=project.name,
        project_type=project.project_type,
        level=project.level,
        status=project.status,
        current_phase=project.current_phase,
        owner=_user_brief(project.owner),
        max_blocker=_blocker_brief(blocker) if blocker else None,
        next_update_date=project.next_update_date,
        is_update_overdue=bool(project.next_update_date and project.next_update_date < today),
        task_total=tc.get("total", 0),
        task_done=tc.get("done", 0),
        task_in_progress=tc.get("in_progress", 0),
        task_overdue=tc.get("overdue", 0),
        week_report_items=wk.get("items", 0),
        week_report_inferred=wk.get("inferred", 0),
        week_escalate=wk.get("escalate", 0),
        week_quality=_week_quality(
            escalate=wk.get("escalate", 0),
            soft_alerts=wk.get("soft", 0),
            stuckish=wk.get("stuckish", 0),
        ),
    )


async def list_visible_project_rows(
    db: AsyncSession,
    user: User,
    permissions: set[str],
) -> list[Project]:
    """仅返回可见项目 ORM 行（含 owner），不做任务/周报/卡点聚合。"""
    query = select(Project).options(selectinload(Project.owner))
    if "project.view.all" not in permissions:
        member_subq = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        query = query.where(or_(Project.owner_id == user.id, Project.id.in_(member_subq)))
    result = await db.execute(query.order_by(Project.updated_at.desc()))
    return list(result.scalars().all())


async def list_projects(
    db: AsyncSession,
    user: User,
    permissions: set[str],
    *,
    level: str | None = None,
    project_type: str | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[ProjectListItem]:
    query = select(Project).options(selectinload(Project.owner))
    if "project.view.all" not in permissions:
        member_subq = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        query = query.where(or_(Project.owner_id == user.id, Project.id.in_(member_subq)))
    if level:
        query = query.where(Project.level == level)
    if project_type:
        query = query.where(Project.project_type == project_type)
    if status:
        query = query.where(Project.status == status)
    if owner_id:
        query = query.where(Project.owner_id == owner_id)
    if search:
        query = query.where(Project.name.ilike(f"%{search}%"))
    # 重点 → 普通 → 日常；同级按最近更新
    level_order = func.array_position(["key", "normal", "daily"], Project.level)
    query = query.order_by(level_order.nulls_last(), Project.updated_at.desc())
    if page is not None and page_size is not None:
        if page < 1:
            raise ValueError("页码无效")
        if page_size < 1 or page_size > 100:
            raise ValueError("每页条数无效")
        query = query.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    projects = list(result.scalars().all())
    project_ids = [p.id for p in projects]

    task_map = await _task_counts_by_project(db, project_ids)
    can_reports = "report.view.all" in permissions
    week_map, *_rest = await _week_metrics_by_project(
        db, project_ids, include_report_stats=can_reports
    )

    items: list[ProjectListItem] = []
    for project in projects:
        blocker = await _latest_blocker(db, project.id)
        items.append(
            _project_list_item(
                project,
                blocker,
                task_counts=task_map.get(project.id),
                week=week_map.get(project.id),
            )
        )
    return items


async def list_projects_page(
    db: AsyncSession,
    user: User,
    permissions: set[str],
    *,
    level: str | None = None,
    project_type: str | None = None,
    status: str | None = None,
    owner_id: int | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 10,
) -> ProjectListOut:
    """项目管理列表分页（默认每页 10）。"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码无效")
    if page_size < 1 or page_size > 100:
        raise HTTPException(status_code=400, detail="每页条数无效")

    count_q = select(func.count(Project.id))
    if "project.view.all" not in permissions:
        member_subq = select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        count_q = count_q.where(or_(Project.owner_id == user.id, Project.id.in_(member_subq)))
    if level:
        count_q = count_q.where(Project.level == level)
    if project_type:
        count_q = count_q.where(Project.project_type == project_type)
    if status:
        count_q = count_q.where(Project.status == status)
    if owner_id:
        count_q = count_q.where(Project.owner_id == owner_id)
    if search:
        count_q = count_q.where(Project.name.ilike(f"%{search}%"))
    total = int((await db.execute(count_q)).scalar_one() or 0)

    items = await list_projects(
        db,
        user,
        permissions,
        level=level,
        project_type=project_type,
        status=status,
        owner_id=owner_id,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ProjectListOut(items=items, total=total, page=page, page_size=page_size)


async def get_blocker_attribution(
    db: AsyncSession, user: User, permissions: set[str]
) -> dict[str, Any]:
    """未关闭卡点按责任方类型汇总，供总览归因看板。"""
    projects = await list_visible_project_rows(db, user, permissions)
    if not projects:
        return {
            "counts": {
                "third_party": 0,
                "customer": 0,
                "internal": 0,
                "approval": 0,
                "other": 0,
            },
            "total": 0,
            "items": [],
        }
    project_map = {p.id: p for p in projects}
    project_ids = list(project_map.keys())
    rows = list(
        (
            await db.execute(
                select(Blocker)
                .options(selectinload(Blocker.assignee), selectinload(Blocker.project))
                .where(
                    Blocker.project_id.in_(project_ids),
                    Blocker.status != "resolved",
                )
                .order_by(Blocker.updated_at.desc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    counts = {
        "third_party": 0,
        "customer": 0,
        "internal": 0,
        "approval": 0,
        "other": 0,
    }
    items: list[dict[str, Any]] = []
    for b in rows:
        pt = (b.party_type or "internal").strip() or "internal"
        if pt not in counts:
            counts["other"] += 1
            bucket = "other"
        else:
            counts[pt] += 1
            bucket = pt
        proj = project_map.get(b.project_id) or b.project
        level = getattr(proj, "level", None) or "normal"
        items.append(
            {
                "id": b.id,
                "description": (b.description or "")[:200],
                "party_type": bucket if bucket != "other" else pt,
                "status": b.status,
                "responsible_party": (b.responsible_party or "")[:80],
                "next_action": (b.next_action or "")[:120],
                "next_follow_up": b.next_follow_up.isoformat() if b.next_follow_up else None,
                "project_id": b.project_id,
                "project_name": proj.name if proj else None,
                "project_level": level,
                "is_key_project": level == "key",
                "assignee_name": b.assignee.name if b.assignee else None,
            }
        )
    # 重点项目排前
    items.sort(key=lambda x: (0 if x.get("is_key_project") else 1, x.get("id") or 0))
    return {"counts": counts, "total": len(items), "items": items}


async def get_overview_stats(db: AsyncSession, user: User, permissions: set[str]) -> OverviewStats:
    # 列表聚合（含任务/周报项目维度）；全局提交率与未归类再单独取一次指标
    projects = await list_projects(db, user, permissions)
    pending = 0
    for item in projects:
        if item.max_blocker and item.max_blocker.status != "resolved":
            pending += 1

    task_total = sum(p.task_total for p in projects)
    task_done = sum(p.task_done for p in projects)
    completion = round(task_done / task_total, 4) if task_total else 0.0

    can_reports = "report.view.all" in permissions
    if can_reports:
        (
            _week_map,
            unassigned,
            expected,
            submitted,
            quality_alerts,
            period_start,
            period_end,
            auto_switched,
        ) = await _week_metrics_by_project(
            db, [p.id for p in projects], include_report_stats=True
        )
        # list_projects 已算过项目维周报；此处只需全局字段，避免无权限时重复查库已在上面短路
        submit_rate = round(submitted / expected, 4) if expected else 0.0
    else:
        unassigned = expected = submitted = quality_alerts = 0
        period_start = period_end = None
        auto_switched = False
        submit_rate = 0.0

    return OverviewStats(
        total_projects=len(projects),
        key_count=sum(1 for p in projects if p.level == "key"),
        daily_count=sum(1 for p in projects if p.level == "daily"),
        red_count=sum(1 for p in projects if p.level == "key"),
        yellow_count=sum(1 for p in projects if p.level == "daily"),
        pending_blockers=pending,
        task_total=task_total,
        task_done=task_done,
        task_completion_rate=completion,
        report_period_start=period_start,
        report_period_end=period_end,
        report_auto_switched=auto_switched,
        report_expected=expected,
        report_submitted=submitted,
        report_submit_rate=submit_rate,
        report_quality_alerts=quality_alerts,
        unassigned_report_items=unassigned,
        key_task_count=await _count_key_tasks(db, [p.id for p in projects]),
    )


async def _count_key_tasks(db: AsyncSession, project_ids: list[int]) -> int:
    if not project_ids:
        return 0
    from app.services.task_service import KEY_TASK_PRIORITIES

    result = await db.execute(
        select(func.count(Task.id)).where(
            Task.project_id.in_(project_ids),
            Task.priority.in_(KEY_TASK_PRIORITIES),
            Task.status != "completed",
        )
    )
    return int(result.scalar_one() or 0)


async def list_key_tasks(db: AsyncSession, project_ids: list[int]) -> list:
    """未完成重点任务简要列表（P0/P1）。"""
    from app.schemas import KeyTaskBrief
    from app.services.task_service import KEY_TASK_PRIORITIES

    if not project_ids:
        return []
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.assignee), selectinload(Task.project))
        .where(
            Task.project_id.in_(project_ids),
            Task.priority.in_(KEY_TASK_PRIORITIES),
            Task.status != "completed",
        )
        .order_by(Task.due_date.nulls_last(), Task.id.desc())
        .limit(100)
    )
    rows = list(result.scalars().all())
    out: list[KeyTaskBrief] = []
    for t in rows:
        out.append(
            KeyTaskBrief(
                id=t.id,
                title=t.title,
                project_id=t.project_id,
                project_name=t.project.name if t.project else None,
                assignee_name=t.assignee.name if t.assignee else None,
                due_date=t.due_date,
                priority=t.priority or "p0",
                status=t.status,
            )
        )
    return out


async def get_project_detail(db: AsyncSession, project_id: int) -> ProjectDetail | None:
    result = await db.execute(
        select(Project).options(selectinload(Project.owner)).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        return None
    blocker = await _latest_blocker(db, project.id)
    task_map = await _task_counts_by_project(db, [project_id])
    week_map, *_ = await _week_metrics_by_project(
        db, [project_id], include_report_stats=True
    )
    base = _project_list_item(
        project,
        blocker,
        task_counts=task_map.get(project_id),
        week=week_map.get(project_id),
    )
    return ProjectDetail(
        **base.model_dump(),
        start_date=project.start_date,
        end_date=project.end_date,
        description=project.description,
    )


async def delete_projects_cascade(
    db: AsyncSession, project_ids: list[int]
) -> tuple[dict[str, int], list[str]]:
    """硬删除项目及其关联数据；人本周报条目仅解绑，不整份删除。

    返回 (计数统计, 待删附件相对路径)。调用方应在 commit 后再删落盘文件。
    """
    if not project_ids:
        return {}, []

    task_ids = list(
        (await db.execute(select(Task.id).where(Task.project_id.in_(project_ids)))).scalars().all()
    )
    blocker_ids = list(
        (
            await db.execute(select(Blocker.id).where(Blocker.project_id.in_(project_ids)))
        ).scalars().all()
    )
    counts: dict[str, int] = {}
    stored_paths: list[str] = []

    if task_ids:
        attach_rows = (
            await db.execute(
                select(TaskAttachment.stored_path).where(TaskAttachment.task_id.in_(task_ids))
            )
        ).scalars().all()
        stored_paths = list(attach_rows)

        r = await db.execute(delete(TaskEvent).where(TaskEvent.task_id.in_(task_ids)))
        counts["task_events"] = r.rowcount or 0
        r = await db.execute(delete(TaskAssistant).where(TaskAssistant.task_id.in_(task_ids)))
        counts["task_assistants"] = r.rowcount or 0
        r = await db.execute(delete(TaskReview).where(TaskReview.task_id.in_(task_ids)))
        counts["task_reviews"] = r.rowcount or 0
        r = await db.execute(delete(TaskAttachment).where(TaskAttachment.task_id.in_(task_ids)))
        counts["task_attachments"] = r.rowcount or 0
        await db.execute(
            update(ProgressReportItem)
            .where(ProgressReportItem.linked_task_id.in_(task_ids))
            .values(linked_task_id=None)
        )
        await db.execute(update(Task).where(Task.id.in_(task_ids)).values(source_report_id=None))
        r = await db.execute(delete(Task).where(Task.id.in_(task_ids)))
        counts["tasks"] = r.rowcount or 0

    if blocker_ids:
        await db.execute(
            update(ProgressReportItem)
            .where(ProgressReportItem.linked_blocker_id.in_(blocker_ids))
            .values(linked_blocker_id=None)
        )
        r = await db.execute(delete(BlockerHistory).where(BlockerHistory.blocker_id.in_(blocker_ids)))
        counts["blocker_histories"] = r.rowcount or 0
        r = await db.execute(delete(Blocker).where(Blocker.id.in_(blocker_ids)))
        counts["blockers"] = r.rowcount or 0

    r = await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(project_ids)))
    counts["project_members"] = r.rowcount or 0
    r = await db.execute(
        delete(ProjectLevelChange).where(ProjectLevelChange.project_id.in_(project_ids))
    )
    counts["level_changes"] = r.rowcount or 0
    r = await db.execute(delete(Phase).where(Phase.project_id.in_(project_ids)))
    counts["phases"] = r.rowcount or 0
    r = await db.execute(delete(WeeklyReport).where(WeeklyReport.project_id.in_(project_ids)))
    counts["weekly_reports"] = r.rowcount or 0
    r = await db.execute(
        update(ProgressReportItem)
        .where(ProgressReportItem.project_id.in_(project_ids))
        .values(project_id=None)
    )
    counts["report_items_unlinked"] = r.rowcount or 0

    r = await db.execute(delete(Project).where(Project.id.in_(project_ids)))
    counts["projects"] = r.rowcount or 0
    return counts, stored_paths


async def delete_project_cascade(db: AsyncSession, project_id: int) -> list[str]:
    """删除单个项目，返回待清理的附件相对路径。"""
    _counts, paths = await delete_projects_cascade(db, [project_id])
    return paths


def cleanup_project_attachment_files(stored_paths: list[str]) -> None:
    """commit 之后删除任务附件落盘文件。"""
    for path in stored_paths:
        if path:
            attachment_delete_stored(path)
