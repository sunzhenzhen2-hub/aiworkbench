"""可选 AI 辅导：优先用库中默认模型，否则 .env，再否则纯规则。"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.llm_resolver import ResolvedLlm, resolve_llm
from app.services.report_quality import _is_vague, validate_report_items

# 改写建议里的字段 → 给人看的中文名（禁止把英文键名展示给员工）
FIELD_LABELS: dict[str, str] = {
    "task_title": "任务概述",
    "result_text": "做成了什么",
    "quant_value": "量化数字",
    "stuck_detail": "卡在哪",
    "stuck_next_action": "卡点下一步",
    "stuck_follow_up": "下次跟进日",
    "stuck_need_who": "需要谁帮忙",
    "next_goal": "下周目标",
    "next_due_date": "完成日期",
    "risk_reason": "风险说明",
    "risk_coordinator": "协调对象",
    "tags": "工作标签",
}

_FIELD_TOKEN_RE = re.compile(
    r"`?(project_id|task_title|result_text|prior_done|next_goal|next_due_date|"
    r"risk_reason|risk_coordinator|stuck_status|stuck_need_who|stuck_follow_up|"
    r"linked_blocker_id|linked_task_id|chain_id|quant_value|no_action|"
    r"risk_level|risk_impact_date)`?",
    re.I,
)


def _strip_tech_jargon(text: str) -> str:
    """把模型漏出的英文字段名换成白话。"""
    if not text:
        return ""
    mapping = {
        "project_id": "所属项目",
        "task_title": "任务概述",
        "result_text": "「做成了什么」",
        "prior_done": "上期那件是否完成",
        "next_goal": "下周目标",
        "next_due_date": "完成日期",
        "risk_reason": "风险说明",
        "risk_coordinator": "协调对象",
        "stuck_status": "是否卡住",
        "stuck_need_who": "需要谁帮忙",
        "stuck_follow_up": "下次跟进日",
        "linked_blocker_id": "关联卡点",
        "linked_task_id": "关联交办",
        "chain_id": "任务链",
        "quant_value": "量化数字",
        "no_action": "无实质动作",
        "risk_level": "风险档位",
        "risk_impact_date": "风险影响日",
    }

    def repl(m: re.Match[str]) -> str:
        key = m.group(1).lower()
        return mapping.get(key, "该项")

    return _FIELD_TOKEN_RE.sub(repl, text)


def _items_for_coach(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只把员工能理解的字段送给模型，避免它盯着内部 id。"""
    out: list[dict[str, Any]] = []
    for idx, it in enumerate(items, start=1):
        if it.get("no_action"):
            out.append({"序号": idx, "本周无实质动作": True, "任务概述": (it.get("task_title") or "")[:80]})
            continue
        stuck = it.get("stuck_status") or "none"
        stuck_label = {"none": "无", "blocker": "有卡点", "coordination": "需协调"}.get(stuck, stuck)
        risk = it.get("risk_level") or "normal"
        risk_label = {"normal": "正常", "watch": "需关注", "escalate": "需升级"}.get(risk, risk)
        row: dict[str, Any] = {
            "序号": idx,
            "任务概述": (it.get("task_title") or "").strip(),
            "做成了什么": (it.get("result_text") or "").strip(),
            "量化数字": (it.get("quant_value") or "").strip(),
            "是否卡住": stuck_label,
            "下周目标": (it.get("next_goal") or "").strip(),
            "完成日期": str(it.get("next_due_date") or "") or None,
            "风险": risk_label,
            "工作标签": it.get("tags") or [],
        }
        if stuck != "none":
            row["卡在哪"] = (it.get("stuck_detail") or "").strip()
            row["卡点下一步"] = (it.get("stuck_next_action") or "").strip()
            row["下次跟进日"] = str(it.get("stuck_follow_up") or "") or None
            row["需要谁帮忙"] = (it.get("stuck_need_who") or "").strip()
        if risk in ("watch", "escalate"):
            row["风险说明"] = (it.get("risk_reason") or "").strip()
            if risk == "escalate":
                row["协调对象"] = (it.get("risk_coordinator") or "").strip()
        out.append(row)
    return out


def _clean_list(values: Any, *, limit: int = 5) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    for v in values:
        s = _strip_tech_jargon(str(v or "").strip())
        if not s:
            continue
        # 过滤「必须写 risk_reason 即使正常」这类过度要求
        if any(
            bad in s
            for bad in (
                "人天",
                "人时",
                "UAT",
                "代码合并",
                "接口联调",
                "测试用例",
                "空白卷",
                "证明不是漏填",
                "管理失控",
                "无法追溯",
            )
        ):
            continue
        # 仍残留英文键名则丢弃
        if re.search(r"[a-z]+_[a-z]+", s):
            continue
        cleaned.append(s[:120])
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_rewrites(values: Any, *, limit: int = 5) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, Any]] = []
    for row in values:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("item_index") or 0)
        except (TypeError, ValueError):
            continue
        field = str(row.get("field") or "result_text").strip()
        if field not in FIELD_LABELS:
            rev = {v: k for k, v in FIELD_LABELS.items()}
            field = rev.get(field, "result_text")
        suggestion = _strip_tech_jargon(str(row.get("suggestion") or "").strip())
        if not suggestion or idx < 1:
            continue
        if field == "tags":
            # 只保留固定词表相关的提示
            suggestion = (
                "可选：售前 / 售中 / 售后 / 运维 / 项目管理 / 流程跟进 / 客户对接 / 方案汇报"
            )
        if field == "next_due_date" and re.search(r"202[0-3]", suggestion):
            suggestion = "请选一个具体完成日（如本周五）"
        out.append(
            {
                "item_index": idx,
                "field": field,
                "field_label": FIELD_LABELS.get(field, "该项"),
                "suggestion": suggestion[:160],
            }
        )
        if len(out) >= limit:
            break
    return out


def rule_based_coach(theme: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    ok, hard, soft = validate_report_items(items)
    clarity: list[str] = []
    risk_alerts: list[str] = []
    rewrites: list[dict] = []
    missing_quant: list[str] = []

    for idx, it in enumerate(items, start=1):
        if it.get("no_action"):
            continue
        title = (it.get("task_title") or "").strip()
        result = (it.get("result_text") or "").strip()
        if not title:
            clarity.append(f"第{idx}条：先写一句「任务概述」，别人才能知道你在忙哪件事。")
        if _is_vague(result):
            clarity.append(f"第{idx}条：「做成了什么」太空/太虚，请写成可核对的结果。")
            rewrites.append(
                {
                    "item_index": idx,
                    "field": "result_text",
                    "field_label": "做成了什么",
                    "suggestion": "例：完成点位勘察 3 处；或 新接入 141 路，累计 2043 路",
                }
            )
        if not (it.get("quant_value") or "").strip() and result and not _is_vague(result):
            missing_quant.append(f"第{idx}条：能补一个数字更好（路数/百分比/份数），没有也可先交。")
        if not (it.get("next_goal") or "").strip():
            clarity.append(f"第{idx}条：还没写「下周目标」。")
        elif not it.get("next_due_date"):
            clarity.append(f"第{idx}条：下周目标有了，请再选一个完成日期。")
        tags = it.get("tags") or []
        if not tags and (title or result):
            clarity.append(f"第{idx}条：建议选 1～2 个工作标签（售前/运维等），方便汇总。")
        stuck = it.get("stuck_status") or "none"
        if stuck == "none" and any(
            k in result for k in ("滞后", "停机", "离线", "卡住", "超期", "未恢复", "不配合")
        ):
            risk_alerts.append(f"第{idx}条：文字里像有卡点，请勾选「有卡点」或「需协调」，别只写在正文里。")
        if (it.get("risk_level") or "normal") == "escalate" and not (
            it.get("risk_coordinator") or ""
        ).strip():
            clarity.append(f"第{idx}条：已选「需升级」，请写清要找谁协调。")

    for msg in soft:
        msg = _strip_tech_jargon(msg)
        if "风险" in msg or "漏报" in msg:
            risk_alerts.append(msg)
        elif "量化" in msg or "数字" in msg:
            missing_quant.append(msg)
        else:
            clarity.append(msg)

    for msg in hard:
        clarity.append(f"须修改：{_strip_tech_jargon(msg)}")

    has_env = bool(settings.openai_api_key and settings.openai_api_base)
    return {
        "enabled": True,
        "clarity_questions": clarity[:6],
        "risk_alerts": risk_alerts[:6],
        "rewrite_suggestions": rewrites[:6],
        "missing_quant": missing_quant[:6],
        "message": "已用规则帮你检查（未调用大模型）" if not has_env else "",
    }


_COACH_SYSTEM = """你是项目支撑部周报小教练。读者是一线员工，不会编程。
只帮他写清楚「做成了什么 / 卡住吗 / 下周何时完成」，不要代写终稿。

禁止：英文字段名（result_text 等）、人天/UAT/接口联调、恐吓语气；
禁止：风险=正常时硬加风险；禁止把「写得虚」升级成风险警报（写进清晰度提醒即可）；
禁止：强迫填任务链；禁止自创工作标签。
可提醒：空话（跟进中/无进度）、有故障却勾无卡点、下周目标缺日期、可补数字。
工作标签只能建议：售前、售中、售后、运维、项目管理、流程跟进、客户对接、方案汇报。
口吻：短句白话，每条≤40字。没问题就返回空数组。

只输出一个 JSON 对象，不要 markdown，不要解释：
{"clarity_questions":[],"risk_alerts":[],"missing_quant":[],"rewrite_suggestions":[{"item_index":1,"field":"result_text","suggestion":"可粘贴例句"}]}
field 仅限 task_title/result_text/next_goal/next_due_date/stuck_detail/risk_reason/tags；每类最多4条。
例句贴合监控接入、合同、巡检、驻点等场景。"""


def _loose_extract_coach_fields(blob: str) -> dict[str, Any] | None:
    """JSON 非法时（中文夹杂未转义引号、截断）用宽松规则抠字段。"""
    if not blob:
        return None
    text = re.sub(r"```(?:json)?", "", blob).replace("```", "")
    out: dict[str, Any] = {
        "clarity_questions": [],
        "risk_alerts": [],
        "missing_quant": [],
        "rewrite_suggestions": [],
    }

    def _items_from_array(section: str) -> list[str]:
        items: list[str] = []
        for line in section.splitlines():
            s = line.strip().rstrip(",")
            if not s or s in {"[", "]"}:
                continue
            # 去掉首尾引号；中间若被中文拆坏，仍保留可读句子
            if s.startswith('"'):
                s = s[1:]
            if s.endswith('"'):
                s = s[:-1]
            s = s.strip().strip('"').strip()
            if len(s) < 4:
                continue
            # 过滤纯 key 行
            if s.endswith(":") or s.startswith("{"):
                continue
            items.append(s[:120])
        return items

    for key in ("clarity_questions", "risk_alerts", "missing_quant"):
        m = re.search(rf'"{key}"\s*:\s*\[([\s\S]*?)\]', text)
        if not m:
            continue
        out[key] = _items_from_array(m.group(1))[:5]

    for m in re.finditer(
        r'"item_index"\s*:\s*(\d+)\s*,\s*"field"\s*:\s*"(\w+)"\s*,\s*"suggestion"\s*:\s*"([^"\n]*)',
        text,
    ):
        sug = (m.group(3) or "").strip()
        if not sug:
            continue
        out["rewrite_suggestions"].append(
            {
                "item_index": int(m.group(1)),
                "field": m.group(2),
                "suggestion": sug[:160],
            }
        )

    if not any(out[k] for k in ("clarity_questions", "risk_alerts", "missing_quant", "rewrite_suggestions")):
        return None
    return out


def _extract_json_obj(text: str) -> dict[str, Any]:
    """从可能含 <think> / markdown 代码块的回复中抠出 JSON 对象。"""
    raw = text or ""
    without_think = re.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
    think_bodies = re.findall(r"<think>([\s\S]*?)</think>", raw)
    # 未闭合 think：仍尝试抽取
    if "<think>" in raw and "</think>" not in raw:
        think_bodies.append(raw.split("<think>", 1)[-1])
    candidates = [without_think, *think_bodies, raw]

    def _prep(blob: str) -> str:
        b = blob.strip()
        b = re.sub(r"```(?:json)?\s*", "", b)
        b = b.replace("```", "")
        return b.strip()

    def _try_load(frag: str) -> dict[str, Any] | None:
        try:
            obj, _ = json.JSONDecoder().raw_decode(frag)
            if isinstance(obj, dict) and (
                "clarity_questions" in obj
                or "rewrite_suggestions" in obj
                or "risk_alerts" in obj
            ):
                return obj
        except json.JSONDecodeError:
            pass
        # 截断修复：丢掉不完整的 rewrite 段，保留前面数组
        cut = re.search(r',\s*"rewrite_suggestions"\s*:\s*\[', frag)
        if cut:
            head = frag[: cut.start()]
            opens = head.count("{") - head.count("}")
            opens_b = head.count("[") - head.count("]")
            head2 = head + "]" * max(0, opens_b) + "}" * max(0, opens)
            try:
                obj = json.loads(head2)
                if isinstance(obj, dict) and (
                    "clarity_questions" in obj or "risk_alerts" in obj
                ):
                    return obj
            except json.JSONDecodeError:
                pass
        fixed = frag
        fixed = re.sub(r",\s*\"[^\"]*$", "", fixed)
        fixed = re.sub(r",\s*$", "", fixed)
        if fixed.count('"') % 2 == 1:
            fixed = fixed.rsplit('"', 1)[0]
            fixed = re.sub(r",\s*$", "", fixed)
        opens = fixed.count("{") - fixed.count("}")
        opens_b = fixed.count("[") - fixed.count("]")
        fixed += "]" * max(0, opens_b) + "}" * max(0, opens)
        try:
            obj = json.loads(fixed)
            if isinstance(obj, dict) and (
                "clarity_questions" in obj
                or "rewrite_suggestions" in obj
                or "risk_alerts" in obj
            ):
                return obj
        except json.JSONDecodeError:
            return None
        return None

    for blob in candidates:
        if not blob or "{" not in blob:
            continue
        prepared = _prep(blob)
        starts = [i for i, ch in enumerate(prepared) if ch == "{"]
        for start in reversed(starts[-8:]):
            obj = _try_load(prepared[start:])
            if obj:
                return obj

    # 最后宽松抠字段（对抗坏引号）
    for blob in candidates:
        loose = _loose_extract_coach_fields(blob)
        if loose:
            return loose
    raise ValueError("无法解析教练 JSON")


async def ai_coach(
    theme: str,
    items: list[dict[str, Any]],
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    base = rule_based_coach(theme, items)
    resolved = None
    if db is not None:
        resolved = await resolve_llm(db, purpose="report_coach")
    elif settings.openai_api_base and settings.openai_api_key:
        resolved = ResolvedLlm(
            api_base=settings.openai_api_base.rstrip("/"),
            api_key=settings.openai_api_key,
            model_name="gpt-4o-mini",
            source="env",
            name="环境变量",
        )

    if not resolved:
        base["enabled"] = True
        base["message"] = (
            "当前未配置 AI，已用规则给出提醒。"
            "请在「系统管理 → 模型管理」添加模型，或配置 OPENAI_API_BASE/KEY。"
        )
        return base

    # 几乎全空时：直接用规则，避免模型长篇说教
    substantive = [
        it
        for it in items
        if not it.get("no_action")
        and (
            (it.get("task_title") or "").strip()
            or (it.get("result_text") or "").strip()
            or (it.get("next_goal") or "").strip()
        )
    ]
    if not substantive:
        base["message"] = "先写一两句「做成了什么」，再点检查会更有用。"
        base["clarity_questions"] = [
            "还几乎是空的：先写任务概述，再写本周做成了什么（一句就行）。"
        ]
        base["risk_alerts"] = []
        base["missing_quant"] = []
        base["rewrite_suggestions"] = []
        # #region agent log
        try:
            import time as _t
            open("/home/aiworkbench/.cursor/debug-e6f1fb.log", "a").write(
                json.dumps(
                    {
                        "sessionId": "e6f1fb",
                        "hypothesisId": "A",
                        "location": "report_ai_coach.py:empty-short-circuit",
                        "message": "empty report short-circuit",
                        "data": {"theme": (theme or "")[:40], "item_count": len(items)},
                        "timestamp": int(_t.time() * 1000),
                        "runId": "adversarial",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            pass
        # #endregion
        return base

    prompt = {"role": "system", "content": _COACH_SYSTEM}
    user_msg = {
        "role": "user",
        "content": json.dumps(
            {
                "本周主题": theme or "",
                "任务卡片": _items_for_coach(items),
            },
            ensure_ascii=False,
            default=str,
        ),
    }
    url = resolved.api_base.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {resolved.api_key}"},
                json={
                    "model": resolved.model_name,
                    "messages": [prompt, user_msg],
                    "temperature": 0.1,
                    "max_tokens": 2000,
                },
            )
            resp.raise_for_status()
            msg = resp.json()["choices"][0]["message"]
            content = msg.get("content") or ""
            if not content.strip():
                content = msg.get("reasoning_content") or msg.get("reasoning") or ""
            data = _extract_json_obj(content)

            clarity = _clean_list(data.get("clarity_questions"), limit=4)
            if not clarity:
                clarity = base["clarity_questions"][:4]
            else:
                # 规则里「须修改」硬提示保留
                for c in base["clarity_questions"]:
                    if c.startswith("须修改：") and c not in clarity:
                        clarity.append(c)
                clarity = clarity[:5]

            risks = _clean_list(data.get("risk_alerts"), limit=3)
            # 过滤「风险正常但描述不清」类伪风险
            risks = [
                r
                for r in risks
                if not (("正常" in r) and ("风险" in r) and ("空话" in r or "不清" in r or "矛盾" in r))
            ]
            for r in base["risk_alerts"]:
                if r not in risks:
                    risks.append(r)
            risks = risks[:4]

            quants = _clean_list(data.get("missing_quant"), limit=3) or base["missing_quant"][:3]
            rewrites = _clean_rewrites(data.get("rewrite_suggestions"), limit=4) or base[
                "rewrite_suggestions"
            ][:4]

            out = {
                "enabled": True,
                "clarity_questions": clarity,
                "risk_alerts": risks,
                "rewrite_suggestions": rewrites,
                "missing_quant": quants,
                "message": (
                    f"AI（{resolved.name or resolved.model_name}）已用白话检查，"
                    "请自己改完再提交，责任仍在你。"
                ),
            }
            # #region agent log
            try:
                import time as _t

                blob = json.dumps(out, ensure_ascii=False)
                open("/home/aiworkbench/.cursor/debug-e6f1fb.log", "a").write(
                    json.dumps(
                        {
                            "sessionId": "e6f1fb",
                            "hypothesisId": "B-E",
                            "location": "report_ai_coach.py:ai-success",
                            "message": "coach success",
                            "data": {
                                "theme": (theme or "")[:40],
                                "clarity_n": len(clarity),
                                "risk_n": len(risks),
                                "quant_n": len(quants),
                                "rewrite_n": len(rewrites),
                                "has_field_leak": any(
                                    k in blob
                                    for k in (
                                        "result_text",
                                        "project_id",
                                        "chain_id",
                                        "prior_done",
                                    )
                                ),
                                "has_dev_jargon": any(
                                    k in blob for k in ("人天", "UAT", "接口联调", "空白卷")
                                ),
                                "clarity_sample": clarity[:2],
                                "risk_sample": risks[:2],
                                "content_len": len(content),
                            },
                            "timestamp": int(_t.time() * 1000),
                            "runId": "adversarial",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
            except Exception:
                pass
            # #endregion
            return out
    except Exception as e:
        # #region agent log
        try:
            import time as _t

            open("/home/aiworkbench/.cursor/debug-e6f1fb.log", "a").write(
                json.dumps(
                    {
                        "sessionId": "e6f1fb",
                        "hypothesisId": "F",
                        "location": "report_ai_coach.py:ai-fallback",
                        "message": "coach fallback",
                        "data": {
                            "error_type": type(e).__name__,
                            "error": str(e)[:200],
                            "content_len": len(locals().get("content") or ""),
                            "content_head": (locals().get("content") or "")[:300],
                            "content_tail": (locals().get("content") or "")[-400:],
                            "has_think": "<think>" in (locals().get("content") or ""),
                        },
                        "timestamp": int(_t.time() * 1000),
                        "runId": "adversarial-f",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception:
            pass
        # #endregion
        base["message"] = "AI 调用失败，已回退到规则检查。"
        return base
