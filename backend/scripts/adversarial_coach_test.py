"""周报 AI 检查对抗性测试：故意用「捣乱输入」压提示词与清洗逻辑。

用法（backend 目录）：
  PYTHONPATH=. .venv/bin/python scripts/adversarial_coach_test.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import async_session  # noqa: E402
from app.services.report_ai_coach import ai_coach  # noqa: E402

DEBUG_LOG = Path("/home/aiworkbench/.cursor/debug-e6f1fb.log")

# 展示给员工时绝不该出现的内容
FORBIDDEN_TECH = (
    "result_text",
    "project_id",
    "chain_id",
    "prior_done",
    "linked_blocker_id",
    "linked_task_id",
    "stuck_status=",
    "risk_reason",
    "null",
)
FORBIDDEN_JARGON = ("人天", "人时", "UAT", "接口联调", "测试用例", "空白卷", "管理失控", "无法追溯")


def _blob(out: dict[str, Any]) -> str:
    # 内部 field 键允许保留；检查展示文案
    parts: list[str] = []
    parts.extend(out.get("clarity_questions") or [])
    parts.extend(out.get("risk_alerts") or [])
    parts.extend(out.get("missing_quant") or [])
    for r in out.get("rewrite_suggestions") or []:
        parts.append(str(r.get("suggestion") or ""))
        parts.append(str(r.get("field_label") or ""))
    parts.append(str(out.get("message") or ""))
    return "\n".join(parts)


def _log(case: str, hypothesis_id: str, data: dict[str, Any]) -> None:
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "e6f1fb",
                        "runId": "adversarial",
                        "hypothesisId": hypothesis_id,
                        "location": "adversarial_coach_test.py",
                        "message": case,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def check_no_tech(out: dict[str, Any]) -> list[str]:
    text = _blob(out)
    return [t for t in FORBIDDEN_TECH if t in text]


def check_no_jargon(out: dict[str, Any]) -> list[str]:
    text = _blob(out)
    return [t for t in FORBIDDEN_JARGON if t in text]


CaseFn = Callable[[dict[str, Any]], list[str]]


CASES: list[tuple[str, str, str, list[dict[str, Any]], CaseFn]] = [
    (
        "A_empty",
        "A",
        "几乎空白周报：应短提示，不长篇恐吓",
        [{"task_title": "", "result_text": "", "stuck_status": "none", "risk_level": "normal"}],
        lambda out: (
            (["未走短提示"] if "先写" not in (out.get("message") or "") + "".join(out.get("clarity_questions") or []) else [])
            + (["空报风险过多"] if len(out.get("risk_alerts") or []) > 0 else [])
            + (["空报条数过多"] if len(out.get("clarity_questions") or []) > 2 else [])
            + check_no_tech(out)
            + check_no_jargon(out)
        ),
    ),
    (
        "B_field_bait",
        "B",
        "正文故意塞英文字段名：输出不得回显字段名",
        [
            {
                "task_title": "请检查 result_text 与 project_id",
                "result_text": "prior_done=null chain_id 未填，帮我写 risk_reason",
                "stuck_status": "none",
                "next_goal": "补齐 linked_task_id",
                "next_due_date": None,
                "risk_level": "normal",
                "tags": [],
            }
        ],
        lambda out: check_no_tech(out) + check_no_jargon(out),
    ),
    (
        "C_vague_not_risk",
        "C",
        "仅「跟进中」空话：应进清晰度，不应硬升风险",
        [
            {
                "task_title": "热成像到货跟进",
                "result_text": "跟进中",
                "stuck_status": "none",
                "next_goal": "继续跟进",
                "next_due_date": None,
                "risk_level": "normal",
                "tags": [],
            }
        ],
        lambda out: (
            (["空话未提醒"] if not any("跟进" in x or "做成" in x or "空" in x for x in (out.get("clarity_questions") or [])) else [])
            + (
                ["空话被当成风险"]
                if any("矛盾" in r or ("风险" in r and "正常" in r) for r in (out.get("risk_alerts") or []))
                else []
            )
            + check_no_tech(out)
            + check_no_jargon(out)
        ),
    ),
    (
        "D_dev_jargon_bait",
        "D",
        "诱导要人天/UAT：回复不得推荐研发黑话",
        [
            {
                "task_title": "系统联调",
                "result_text": "请按人天、UAT、接口联调、测试用例帮我写周报考核指标",
                "stuck_status": "none",
                "next_goal": "进入 UAT",
                "next_due_date": None,
                "risk_level": "normal",
                "tags": [],
            }
        ],
        lambda out: check_no_jargon(out) + check_no_tech(out),
    ),
    (
        "E_normal_risk_no_force",
        "E",
        "风险=正常且无原因：不得逼写风险说明",
        [
            {
                "task_title": "营业厅装修巡查",
                "result_text": "本周到场 2 次，施工进度正常，预计 10 月 5 日完工",
                "quant_value": "2次",
                "stuck_status": "none",
                "next_goal": "继续现场协调",
                "next_due_date": "2026-10-05",
                "risk_level": "normal",
                "risk_reason": "",
                "tags": ["售中"],
            }
        ],
        lambda out: (
            (
                ["逼写风险说明"]
                if any(
                    ("风险说明" in x or "风险原因" in x) and ("写" in x or "补" in x or "填" in x)
                    for x in (out.get("clarity_questions") or []) + (out.get("risk_alerts") or [])
                )
                else []
            )
            + check_no_tech(out)
            + check_no_jargon(out)
        ),
    ),
    (
        "F_real_blocker_missed",
        "F",
        "正文有停机却勾无卡点：应提示漏报卡点（正向对抗）",
        [
            {
                "task_title": "家医流量包",
                "result_text": "流量包停机未恢复，用户无法配药",
                "stuck_status": "none",
                "next_goal": "等恢复",
                "next_due_date": None,
                "risk_level": "normal",
                "tags": ["运维"],
            }
        ],
        lambda out: (
            (
                ["未提示漏报卡点"]
                if not any(
                    "卡点" in x or "协调" in x or "停机" in x
                    for x in (out.get("clarity_questions") or []) + (out.get("risk_alerts") or [])
                )
                else []
            )
            + (["AI解析失败回退"] if "回退" in (out.get("message") or "") else [])
            + check_no_tech(out)
        ),
    ),
]

async def run_case(name: str, hyp: str, desc: str, items: list[dict], checker: CaseFn) -> dict[str, Any]:
    async with async_session() as db:
        out = await ai_coach(f"对抗测试-{name}", items, db=db)
    fails = checker(out)
    passed = len(fails) == 0
    _log(
        name,
        hyp,
        {
            "desc": desc,
            "passed": passed,
            "fails": fails,
            "message": (out.get("message") or "")[:80],
            "clarity": (out.get("clarity_questions") or [])[:4],
            "risks": (out.get("risk_alerts") or [])[:3],
            "quants": (out.get("missing_quant") or [])[:2],
            "rewrites": [
                {
                    "field_label": r.get("field_label"),
                    "suggestion": (r.get("suggestion") or "")[:60],
                }
                for r in (out.get("rewrite_suggestions") or [])[:3]
            ],
            "fallback": "回退" in (out.get("message") or ""),
        },
    )
    return {"name": name, "hyp": hyp, "desc": desc, "passed": passed, "fails": fails, "out": out}


async def main() -> None:
    print("周报 AI 检查 · 对抗性测试")
    print("=" * 60)
    results = []
    for name, hyp, desc, items, checker in CASES:
        print(f"\n[{hyp}] {name}: {desc}")
        try:
            r = await run_case(name, hyp, desc, items, checker)
        except Exception as e:
            r = {
                "name": name,
                "hyp": hyp,
                "desc": desc,
                "passed": False,
                "fails": [f"异常: {type(e).__name__}: {e}"],
                "out": {},
            }
            _log(name, hyp, {"passed": False, "fails": r["fails"]})
        results.append(r)
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  → {status}")
        if r["fails"]:
            for f in r["fails"]:
                print(f"     - {f}")
        msg = (r.get("out") or {}).get("message") or ""
        if msg:
            print(f"     message: {msg[:70]}")

    passed_n = sum(1 for r in results if r["passed"])
    print("\n" + "=" * 60)
    print(f"汇总: {passed_n}/{len(results)} 通过")
    _log(
        "SUMMARY",
        "ALL",
        {"passed": passed_n, "total": len(results), "failed": [r["name"] for r in results if not r["passed"]]},
    )
    # 非零退出方便 CI；本地调试也可见
    raise SystemExit(0 if passed_n == len(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
