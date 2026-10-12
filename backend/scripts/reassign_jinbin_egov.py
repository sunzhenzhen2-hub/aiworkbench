#!/usr/bin/env python3
"""金滨事项归入「富阳区电子政务网驻场运维」（周报卡片 + 交办任务）。

用法（在 backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/reassign_jinbin_egov.py
  PYTHONPATH=. .venv/bin/python scripts/reassign_jinbin_egov.py --apply

默认 dry-run。改库前请先：
  bash /home/aiworkbench/scripts/backup.sh

规则：
- 目标人：金滨
- 目标项目：富阳区电子政务网驻场运维（不存在则创建）
- 周报：除明确日常杂项外，默认归驻场；可用 LLM 二分类（无模型则关键词）
- 交办：负责人或创建人为金滨、且当前不在目标项目/日常任务，且文案像驻场运维 → 改 project_id
- 不删项目、不改他人数据、不碰「日常任务」立项本身
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import or_, select
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
    Task,
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

PERSON_NAME = "金滨"
TARGET_PROJECT_NAME = "富阳区电子政务网驻场运维"
TARGET_DESCRIPTION = "数据局驻场技术人员：政务外网、零信任、视联网会议保障等驻场运维"
ROLE_HINT = "富阳区数据局驻场运维（电子政务网/政务外网/零信任/视联网等）"
ONSITE_PHASE = "运维保障"

# 明显日常/非驻场关键词（命中则归日常）
_DAILY_HINTS = re.compile(
    r"(文旅|投标|售前方案|旅游推广|党建活动|团建|年会|招聘|报销|请假)",
    re.I,
)
# 驻场运维关键词
_ONSITE_HINTS = re.compile(
    r"(政务|电子政务|外网|零信任|视联网|机房|巡检|运维|故障|VPN|专线|堡垒机|准入|驻场|数据局)",
    re.I,
)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 未返回 JSON: {(text or '')[:300]}")
    return json.loads(cleaned[start : end + 1])


def _rule_label(title: str, result: str) -> str:
    blob = f"{title} {result}"
    if _DAILY_HINTS.search(blob) and not _ONSITE_HINTS.search(blob):
        return "daily"
    return "onsite"


async def ensure_onsite_project(db, *, owner: User) -> Project:
    row = (
        await db.execute(select(Project).where(Project.name == TARGET_PROJECT_NAME))
    ).scalar_one_or_none()
    if row:
        row.owner_id = owner.id
        row.project_type = "integration"
        row.level = "normal"
        row.current_phase = ONSITE_PHASE
        row.description = TARGET_DESCRIPTION
        row.status = "normal"
    else:
        row = Project(
            name=TARGET_PROJECT_NAME,
            project_type="integration",
            level="normal",
            status="normal",
            current_phase=ONSITE_PHASE,
            owner_id=owner.id,
            description=TARGET_DESCRIPTION,
        )
        db.add(row)
        await db.flush()
        await seed_standard_phases(db, row.id)

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

    active = list(
        (await db.execute(select(User).where(User.is_active.is_(True)))).scalars().all()
    )
    for u in active:
        if u.id == owner.id:
            continue
        if u.id not in existing:
            db.add(ProjectMember(project_id=row.id, user_id=u.id, member_role="member"))
    await db.flush()
    return row


async def classify_items_llm(
    llm,
    items: list[ProgressReportItem],
) -> dict[int, str]:
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
        "1) onsite=驻场运维本职（机房/网络/零信任/城域网/巡检/故障/会议保障等）\n"
        "2) daily=明确非驻场（文旅推广、售前投标、与驻场岗位无关的行政杂项等）\n"
        "3) 拿不准一律 onsite\n"
        "4) 必须对输入的每个 id 给出一条 decision"
    )
    user_msg = (
        f"员工：{PERSON_NAME}\n岗位：{ROLE_HINT}\n"
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
    out: dict[int, str] = {}
    for d in data.get("decisions") or []:
        if not isinstance(d, dict):
            continue
        try:
            iid = int(d.get("id"))
        except (TypeError, ValueError):
            continue
        label = str(d.get("label") or "onsite").strip().lower()
        out[iid] = "daily" if label == "daily" else "onsite"
    for it in items:
        out.setdefault(it.id, "onsite")
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description="金滨→电子政务网驻场运维归档")
    parser.add_argument("--apply", action="store_true", help="写入数据库；默认 dry-run")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="不用大模型，仅关键词规则分类",
    )
    args = parser.parse_args()

    async with async_session() as db:
        user = (
            await db.execute(select(User).where(User.name == PERSON_NAME))
        ).scalar_one_or_none()
        if not user:
            raise SystemExit(f"找不到用户: {PERSON_NAME}")

        daily = await ensure_daily_project(db)
        project = await ensure_onsite_project(db, owner=user)
        print(
            f"目标人={PERSON_NAME}(id={user.id}) "
            f"项目「{project.name}」id={project.id} 日常任务 id={daily.id}"
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
        items = [it for r in reports for it in r.items if not it.no_action]
        print(f"周报条目数={len(items)}")

        # 仅处理当前不在目标项目的卡片（已在目标项目的不动）
        to_classify = [it for it in items if it.project_id != project.id]
        labels: dict[int, str] = {}
        if to_classify:
            llm = None
            if not args.no_llm:
                llm = await resolve_llm(db, "general") or await resolve_llm(db, "report_coach")
            if llm:
                print(f"分类模型: {llm.name} / {llm.model_name}")
                try:
                    labels = await classify_items_llm(llm, to_classify)
                except Exception as exc:
                    print(f"LLM 分类失败，回退关键词: {exc}")
                    labels = {
                        it.id: _rule_label(it.task_title or "", it.result_text or "")
                        for it in to_classify
                    }
            else:
                print("无 LLM，使用关键词规则分类")
                labels = {
                    it.id: _rule_label(it.task_title or "", it.result_text or "")
                    for it in to_classify
                }

        item_moves: list[str] = []
        onsite_n = daily_n = skip_n = 0
        for it in items:
            if it.project_id == project.id:
                skip_n += 1
                continue
            label = labels.get(it.id, "onsite")
            title = (it.task_title or "")[:80]
            if label == "daily":
                if it.project_id == daily.id:
                    skip_n += 1
                    continue
                item_moves.append(
                    f"  item#{it.id} → 日常任务 | {title} (from project_id={it.project_id})"
                )
                if args.apply:
                    it.project_id = daily.id
                daily_n += 1
            else:
                item_moves.append(
                    f"  item#{it.id} → 驻场 | {title} (from project_id={it.project_id})"
                )
                if args.apply:
                    it.project_id = project.id
                    it.tags = normalize_work_tags([*(it.tags or []), "售后", "运维"])
                onsite_n += 1

        # 交办任务：负责人或创建人为金滨，且不在目标/日常
        tasks = list(
            (
                await db.execute(
                    select(Task).where(
                        or_(Task.assignee_id == user.id, Task.created_by_id == user.id)
                    )
                )
            ).scalars().all()
        )
        task_moves: list[str] = []
        task_n = 0
        for t in tasks:
            if t.project_id in (project.id, daily.id):
                continue
            blob = f"{t.title or ''} {t.goal or ''}"
            # 明显日常且无驻场词 → 不搬到驻场
            if _DAILY_HINTS.search(blob) and not _ONSITE_HINTS.search(blob):
                continue
            # 无驻场词也不硬搬，避免把别人项目里的交办误迁
            if not _ONSITE_HINTS.search(blob):
                continue
            task_moves.append(
                f"  task#{t.id} → 驻场 | {(t.title or '')[:80]} (from project_id={t.project_id})"
            )
            if args.apply:
                t.project_id = project.id
                # 阶段清空，避免指向旧项目 phase
                t.phase_id = None
            task_n += 1

        print("\n==== 周报拟变更 ====")
        print(f"驻场={onsite_n} 日常={daily_n} 已正确跳过={skip_n}")
        for line in item_moves:
            print(line)

        print("\n==== 交办拟变更 ====")
        print(f"迁入驻场={task_n}")
        for line in task_moves:
            print(line)

        if args.apply:
            await db.commit()
            print("\n[apply] 已提交")
        else:
            await db.rollback()
            print("\n[dry-run] 未写库。确认后加 --apply；改库前请先 bash scripts/backup.sh")


if __name__ == "__main__":
    asyncio.run(main())
