"""周报任务卡工作标签：售前/售中/售后/运维等固定词表。"""

from __future__ import annotations

from typing import Any

# 固定业务标签（可多选；入库与填写页共用）
WORK_TAG_OPTIONS: list[str] = [
    "售前",
    "售中",
    "售后",
    "运维",
    "项目管理",
    "流程跟进",
    "客户对接",
    "方案汇报",
]

_ALLOWED = set(WORK_TAG_OPTIONS)

# 常见同义/旧表「业务域」映射到标准标签
_ALIASES: dict[str, str] = {
    "售前支持": "售前",
    "售前支撑": "售前",
    "售前方案": "售前",
    "方案": "方案汇报",
    "方案输出": "方案汇报",
    "汇报": "方案汇报",
    "客户沟通": "客户对接",
    "客户侧": "客户对接",
    "驻点": "运维",
    "驻场": "运维",
    "运维保障": "运维",
    "实施": "售中",
    "交付": "售中",
    "验收": "售中",
    "售后服务": "售后",
    "项目跟进": "流程跟进",
    "进度跟进": "流程跟进",
    "协调": "流程跟进",
}


def normalize_work_tags(raw: Any, *, max_tags: int = 4) -> list[str]:
    """规范化标签列表：只保留词表内标签，去重保序。"""
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace("，", ",").replace("/", ",").split(",")]
        values = [p for p in parts if p]
    elif isinstance(raw, (list, tuple, set)):
        values = [str(x).strip() for x in raw if str(x).strip()]
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        tag = _ALIASES.get(v, v)
        if tag not in _ALLOWED:
            # 模糊包含：如「售前支持工作」
            hit = next((opt for opt in WORK_TAG_OPTIONS if opt in tag or tag in opt), None)
            if not hit:
                continue
            tag = hit
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_tags:
            break
    return out


def guess_work_tags(
    title: str = "",
    result: str = "",
    next_goal: str = "",
    stuck: str = "",
    *,
    max_tags: int = 3,
) -> list[str]:
    """无模型时的关键词规则打标（至少返回 1 个）。"""
    text = f"{title} {result} {next_goal} {stuck}"
    rules: list[tuple[str, tuple[str, ...]]] = [
        ("售前", ("售前", "方案", "报价", "投标", "签约", "合同", "立项", "勘察")),
        ("售中", ("实施", "安装", "部署", "上线", "验收", "交付", "调试", "接入")),
        ("售后", ("售后", "保修", "返修", "中毒", "告警处置")),
        ("运维", ("运维", "驻点", "驻场", "值班", "告警", "巡检", "会议保障", "视频会议", "封禁")),
        ("客户对接", ("沟通", "业主", "客户", "对接", "协调会", "派出所")),
        ("方案汇报", ("汇报", "演示", "讲解", "上会", "PPT")),
        ("流程跟进", ("跟进", "催促", "流程", "审批", "采购", "材料")),
        ("项目管理", ("项目经理", "统筹", "排期", "里程碑", "协助", "支援")),
    ]
    hits: list[str] = []
    for tag, kws in rules:
        if any(k in text for k in kws):
            hits.append(tag)
        if len(hits) >= max_tags:
            break
    return hits or ["流程跟进"]


def resolve_item_tags(raw: Any, *, title: str = "", result: str = "", next_goal: str = "", stuck: str = "") -> list[str]:
    """优先用已有/模型标签，空则规则猜。"""
    tags = normalize_work_tags(raw)
    if tags:
        return tags
    return guess_work_tags(title, result, next_goal, stuck)
