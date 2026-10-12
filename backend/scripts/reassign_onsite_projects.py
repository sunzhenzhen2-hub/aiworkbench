#!/usr/bin/env python3
"""金滨 / 王捷驻场运维项目归档：建项目 + LLM 区分驻场/日常 + 清理伪项目空壳。

用法（在 backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/reassign_onsite_projects.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.models import (  # noqa: E402
    Phase,
    ProgressReport,
    ProgressReportItem,
    Project,
    ProjectMember,
    User,
)
from app.services.llm_resolver import resolve_llm  # noqa: E402
from app.services.project_phases import STANDARD_PHASES  # noqa: E402
from app.services.project_service import (  # noqa: E402
    DAILY_PROJECT_NAME,
    ensure_daily_project,
    seed_standard_phases,
)
from app.services.work_tags import normalize_work_tags  # noqa: E402

# 驻场项目定义
ONSITE_SPECS: list[dict[str, Any]] = [
    {
        "person": "金滨",
        "project_name": "富阳区电子政务网驻场运维",
        "description": "数据局驻场技术人员：政务外网、零信任、视联网会议保障等驻场运维",
        "role_hint": "富阳区数据局驻场运维（电子政务网/政务外网/零信任/视联网等）",
    },
    {
        "person": "王捷",
        "project_name": "富阳区教育城域网驻场运维",
        "description": "教育局驻场技术人员：教育城域网、机房巡检、校园监控与终端安全等",
        "role_hint": "富阳区教育局驻场运维（教育城域网/机房/校园监控/学校终端等）",
    },
]

ONSITE_PHASE = "运维保障"
PROTECTED_PROJECT_NAMES = {
    DAILY_PROJECT_NAME,
    "富阳区电子政务网驻场运维",
    "富阳区教育城域网驻场运维",
}


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 未返回 JSON: {(text or '')[:300]}")
    return json.loads(cleaned[start : end + 1])


async def ensure_onsite_project(db, *, name: str, owner: User, description: str) -> Project:
    row = (
        await db.execute(select(Project).where(Project.name == name))
    ).scalar_one_or_none()
    if row:
        row.owner_id = owner.id
        row.project_type = "integration"
        row.level = "normal"
        row.current_phase = ONSITE_PHASE
        row.description = description
        row.status = "normal"
    else:
        row = Project(
            name=name,
            project_type="integration",
            level="normal",
            status="normal",
            current_phase=ONSITE_PHASE,
            owner_id=owner.id,
            description=description,
        )
        db.add(row)
        await db.flush()
        await seed_standard_phases(db, row.id)

    # 阶段：运维保障标为进行中
    phases = list(
        (await db.execute(select(Phase).where(Phase.project_id == row.id))).scalars().all()
    )
    if not phases:
        await seed_standard_phases(db, row.id)
        phases = list(
            (await db.execute(select(Phase).where(Phase.project_id == row.id))).scalars().all()
        )
    for ph in phases:
        if ph.name == ONSITE_PHASE:
            ph.status = "in_progress"
        elif ph.name in STANDARD_PHASES[: STANDARD_PHASES.index(ONSITE_PHASE)]:
            if ph.status == "not_started":
                ph.status = "done"

    # 成员：本人 lead，其余活跃成员 member
    active = list(
        (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    )
    existing = {
        m.user_id: m
        for m in (
            await db.execute(select(ProjectMember).where(ProjectMember.project_id == row.id))
        ).scalars().all()
    }
    if owner.id in existing:
        existing[owner.id].member_role = "lead"
    else:
        db.add(ProjectMember(project_id=row.id, user_id=owner.id, member_role="lead"))
    for u in active:
        if u.id == owner.id:
            continue
        if u.id in existing:
            if existing[u.id].member_role == "lead" and u.id != owner.id:
                existing[u.id].member_role = "member"
        else:
            db.add(ProjectMember(project_id=row.id, user_id=u.id, member_role="member"))
    await db.flush()
    return row


async def classify_items(
    llm,
    *,
    person: str,
    role_hint: str,
    items: list[ProgressReportItem],
) -> dict[int, str]:
    """返回 item_id -> onsite|daily。失败或拿不准默认 onsite。"""
    if not items:
        return {}
    payload = [
        {
            "id": it.id,
            "title": (it.task_title or "")[:120],
            "result": (it.result_text or "")[:400],
        }
        for it in items
    ]
    system = (
        "你是项目归档助手。判断员工周报事项是否属于「驻场运维服务」。\n"
        "只输出 JSON：{\"decisions\":[{\"id\":数字,\"label\":\"onsite|daily\",\"reason\":\"≤20字\"}]}\n"
        "规则：\n"
        "1) onsite=驻场运维本职（机房/网络/零信任/城域网/巡检/故障/会议保障/校园终端安全等）\n"
        "2) daily=明确非驻场（文旅推广、售前投标、与驻场岗位无关的行政杂项等）\n"
        "3) 拿不准一律 onsite\n"
        "4) 必须对输入的每个 id 给出一条 decision"
    )
    user_msg = (
        f"员工：{person}\n岗位：{role_hint}\n"
        f"事项列表：\n{json.dumps(payload, ensure_ascii=False)}"
    )
    url = llm.api_base.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=120.0) as client:
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
                "max_tokens": 4000,
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    data = _extract_json(content)
    decisions = data.get("decisions") or []
    out: dict[int, str] = {}
    for d in decisions:
        if not isinstance(d, dict):
            continue
        try:
            iid = int(d.get("id"))
        except (TypeError, ValueError):
            continue
        label = str(d.get("label") or "onsite").strip().lower()
        out[iid] = "daily" if label == "daily" else "onsite"
    # 缺省补全为 onsite
    for it in items:
        out.setdefault(it.id, "onsite")
    return out


async def cleanup_empty_shells(db, owner_ids: set[int], keep_ids: set[int]) -> list[str]:
    """删除：归属指定人、无条目引用、且不在保护名单的空壳项目。"""
    owned_q = select(Project).where(Project.owner_id.in_(owner_ids))
    if keep_ids:
        owned_q = owned_q.where(Project.id.notin_(list(keep_ids)))
    owned = list((await db.execute(owned_q)).scalars().all())
    deleted: list[str] = []
    for p in owned:
        if p.name in PROTECTED_PROJECT_NAMES:
            continue
        cnt = (
            await db.execute(
                select(func.count())
                .select_from(ProgressReportItem)
                .where(ProgressReportItem.project_id == p.id)
            )
        ).scalar() or 0
        if cnt > 0:
            continue
        # 清成员与阶段后删项目
        members = (
            await db.execute(select(ProjectMember).where(ProjectMember.project_id == p.id))
        ).scalars().all()
        for m in members:
            await db.delete(m)
        phases = (
            await db.execute(select(Phase).where(Phase.project_id == p.id))
        ).scalars().all()
        for ph in phases:
            await db.delete(ph)
        deleted.append(f"{p.id}:{p.name}")
        await db.delete(p)
    await db.flush()
    return deleted


async def main() -> None:
    async with async_session() as db:
        llm = await resolve_llm(db, "general") or await resolve_llm(db, "report_coach")
        if not llm:
            raise SystemExit("未配置可用 LLM")
        print(f"使用模型: {llm.name} / {llm.model_name}")

        daily = await ensure_daily_project(db)
        print(f"日常任务 id={daily.id}")

        summary: dict[str, Any] = {}
        keep_project_ids = {daily.id}
        owner_ids: set[int] = set()

        for spec in ONSITE_SPECS:
            person = spec["person"]
            user = (
                await db.execute(select(User).where(User.name == person))
            ).scalar_one_or_none()
            if not user:
                raise SystemExit(f"找不到用户: {person}")
            owner_ids.add(user.id)
            project = await ensure_onsite_project(
                db,
                name=spec["project_name"],
                owner=user,
                description=spec["description"],
            )
            keep_project_ids.add(project.id)
            print(
                f"\n=== {person} → 项目「{project.name}」 id={project.id} "
                f"phase={project.current_phase} type={project.project_type} ==="
            )

            reports = list(
                (
                    await db.execute(
                        select(ProgressReport)
                        .options(selectinload(ProgressReport.items))
                        .where(ProgressReport.user_id == user.id)
                    )
                ).scalars().all()
            )
            items = [it for r in reports for it in r.items]
            print(f"条目数={len(items)}")

            labels = await classify_items(
                llm,
                person=person,
                role_hint=spec["role_hint"],
                items=items,
            )
            onsite_n = daily_n = 0
            details: list[str] = []
            for it in items:
                label = labels.get(it.id, "onsite")
                if label == "daily":
                    it.project_id = daily.id
                    daily_n += 1
                    details.append(f"  daily  #{it.id} {it.task_title}")
                else:
                    it.project_id = project.id
                    tags = normalize_work_tags([*(it.tags or []), "售后", "运维"])
                    it.tags = tags
                    onsite_n += 1
                    details.append(f"  onsite #{it.id} {it.task_title}")
            await db.flush()
            summary[person] = {
                "project_id": project.id,
                "project_name": project.name,
                "onsite": onsite_n,
                "daily": daily_n,
            }
            print(f"归档: onsite={onsite_n} daily={daily_n}")
            for line in details:
                print(line)

        deleted = await cleanup_empty_shells(db, owner_ids, keep_project_ids)
        await db.commit()

        print("\n==== 汇总 ====")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"删除空壳项目 {len(deleted)} 个:")
        for d in deleted:
            print(f"  - {d}")


if __name__ == "__main__":
    asyncio.run(main())
