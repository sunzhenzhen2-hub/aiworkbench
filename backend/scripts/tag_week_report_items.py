"""为本周已入库周报任务卡补打工作标签（LLM）。

用法（backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/tag_week_report_items.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.models import ProgressReport, ProgressReportItem, User  # noqa: E402
from app.services.llm_resolver import resolve_llm  # noqa: E402
from app.services.work_tags import WORK_TAG_OPTIONS, normalize_work_tags  # noqa: E402

PERIOD_START = date(2026, 9, 7)


def _extract_json(text: str) -> Any:
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    # 也可能是 {"items":[...]}
    if start >= 0 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            pass
    o_start = cleaned.find("{")
    o_end = cleaned.rfind("}")
    if o_start < 0 or o_end <= o_start:
        raise ValueError(f"无 JSON: {(text or '')[:300]}")
    return json.loads(cleaned[o_start : o_end + 1])


async def tag_batch(llm, items: list[dict[str, Any]]) -> dict[int, list[str]]:
    """返回 item_id -> tags。"""
    system = (
        "你是周报标签助手。为每条任务卡打 1～3 个工作标签。\n"
        "只输出 JSON 数组（不要 markdown）：\n"
        '[{"id":123,"tags":["售前","客户对接"]}]\n'
        f"tags 只能从：{ '、'.join(WORK_TAG_OPTIONS) }\n"
        "依据标题与结果文字判断；驻点/会议保障偏运维；签约/方案偏售前；"
        "实施安装验收偏售中；故障售后偏售后；催进度/流程偏流程跟进；"
        "与业主沟通偏客户对接；方案讲解偏方案汇报；统筹多项目偏项目管理。"
    )
    user_msg = json.dumps({"items": items}, ensure_ascii=False)
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
            },
        )
        if resp.status_code >= 400:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:400]}")
        content = resp.json()["choices"][0]["message"]["content"]
        if not content:
            msg = resp.json()["choices"][0]["message"]
            content = msg.get("reasoning_content") or ""
    data = _extract_json(content)
    if isinstance(data, dict):
        data = data.get("items") or data.get("results") or []
    out: dict[int, list[str]] = {}
    if not isinstance(data, list):
        return out
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            iid = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        tags = normalize_work_tags(row.get("tags"))
        if tags:
            out[iid] = tags
    return out


def _rule_fallback(title: str, result: str, next_goal: str) -> list[str]:
    text = f"{title} {result} {next_goal}"
    hits: list[str] = []
    rules = [
        ("售前", ("售前", "方案", "报价", "投标", "签约", "合同", "立项")),
        ("售中", ("实施", "安装", "部署", "上线", "验收", "交付", "调试")),
        ("售后", ("售后", "保修", "返修")),
        ("运维", ("运维", "驻点", "驻场", "值班", "告警", "巡检", "会议保障", "视频会议")),
        ("客户对接", ("沟通", "业主", "客户", "对接", "协调会")),
        ("方案汇报", ("汇报", "演示", "讲解", "PPT")),
        ("流程跟进", ("跟进", "催促", "流程", "审批", "采购")),
        ("项目管理", ("项目经理", "统筹", "排期", "里程碑")),
    ]
    for tag, kws in rules:
        if any(k in text for k in kws):
            hits.append(tag)
        if len(hits) >= 3:
            break
    return hits or ["流程跟进"]


async def main() -> None:
    async with async_session() as db:
        llm = await resolve_llm(db, "report_structure") or await resolve_llm(db, "report_coach")
        if not llm:
            raise SystemExit("无可用 LLM")
        print(f"模型: {llm.name} / {llm.model_name}")

        q = await db.execute(
            select(ProgressReport)
            .options(selectinload(ProgressReport.items), selectinload(ProgressReport.author))
            .where(
                ProgressReport.period_type == "week",
                ProgressReport.period_start == PERIOD_START,
            )
        )
        reports = q.scalars().all()
        all_items: list[ProgressReportItem] = []
        for r in reports:
            all_items.extend(sorted(r.items, key=lambda x: x.sort_order))
        print(f"待打标 {len(all_items)} 条（{len(reports)} 份周报）")

        # 按人分批，每批最多 20 条
        by_report: dict[int, list[ProgressReportItem]] = {}
        for it in all_items:
            by_report.setdefault(it.report_id, []).append(it)

        tagged = 0
        for report in reports:
            items = by_report.get(report.id) or []
            if not items:
                continue
            author = report.author.name if report.author else "?"
            payload = [
                {
                    "id": it.id,
                    "task_title": it.task_title,
                    "result_text": (it.result_text or "")[:200],
                    "next_goal": (it.next_goal or "")[:120],
                    "stuck_detail": (it.stuck_detail or "")[:120],
                }
                for it in items
            ]
            print(f"\n=== {author} report={report.id} n={len(items)} ===")
            mapping: dict[int, list[str]] = {}
            try:
                mapping = await tag_batch(llm, payload)
            except Exception as e:
                print(f"  LLM 失败，改用规则: {e}")
            for it in items:
                tags = mapping.get(it.id) or _rule_fallback(
                    it.task_title or "", it.result_text or "", it.next_goal or ""
                )
                tags = normalize_work_tags(tags) or ["流程跟进"]
                it.tags = tags
                tagged += 1
                print(f"  #{it.id} {tags} | {(it.task_title or '')[:40]}")
            await db.commit()

        print(f"\n完成打标 {tagged} 条")


if __name__ == "__main__":
    asyncio.run(main())
