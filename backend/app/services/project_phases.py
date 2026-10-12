"""项目阶段规范词表（按类型模板 + 当前阶段与时间线同步）。"""

from __future__ import annotations

# 软件 / 集众融合默认阶段
SOFTWARE_PHASES: list[str] = [
    "需求分析",
    "立项",
    "设计",
    "开发",
    "测试",
    "上线",
    "运维保障",
    "验收",
    "收尾",
]

# 系统集成默认阶段
INTEGRATION_PHASES: list[str] = [
    "需求分析",
    "立项",
    "方案设计",
    "采购到货",
    "现场实施",
    "联调测试",
    "验收交付",
    "运维保障",
    "收尾",
]

# 历史统一词表（含「实施/开发」），用于识别未改动过的旧默认模板
LEGACY_STANDARD_PHASES: list[str] = [
    "需求分析",
    "立项",
    "设计",
    "实施/开发",
    "测试",
    "上线",
    "运维保障",
    "验收",
    "收尾",
]

PHASES_BY_TYPE: dict[str, list[str]] = {
    "software": SOFTWARE_PHASES,
    "fusion": SOFTWARE_PHASES,
    "integration": INTEGRATION_PHASES,
}

# 兼容旧引用：默认视为软件模板
STANDARD_PHASES: list[str] = list(SOFTWARE_PHASES)


def phases_for(project_type: str | None) -> list[str]:
    """按项目类型返回默认阶段序列（拷贝）。"""
    key = (project_type or "software").strip().lower()
    return list(PHASES_BY_TYPE.get(key, SOFTWARE_PHASES))


def all_known_phase_names() -> set[str]:
    names: set[str] = set(LEGACY_STANDARD_PHASES)
    for seq in PHASES_BY_TYPE.values():
        names.update(seq)
    return names


def is_standard_phase(name: str) -> bool:
    return (name or "").strip() in all_known_phase_names()


def ordered_phase_names_for_display() -> list[str]:
    """工作台汇总排序：软件优先，再补集成专有名，最后历史名。"""
    seen: set[str] = set()
    out: list[str] = []
    for name in SOFTWARE_PHASES + INTEGRATION_PHASES + LEGACY_STANDARD_PHASES:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def is_pristine_default_timeline(
    phase_rows: list,
    *,
    project_type: str | None,
) -> bool:
    """
    阶段是否仍是「某类型系统默认全量模板且无人改过」。
    无人改过：无备注、无计划日；状态为首段进行中、其余未开始。
    """
    if not phase_rows:
        return True
    candidates = [
        phases_for(project_type),
        LEGACY_STANDARD_PHASES,
        SOFTWARE_PHASES,
        INTEGRATION_PHASES,
    ]
    names = [str(getattr(p, "name", "") or "").strip() for p in phase_rows]
    if names not in candidates:
        return False
    for i, p in enumerate(phase_rows):
        note = (getattr(p, "note", None) or "").strip()
        if note:
            return False
        if getattr(p, "planned_end_date", None) is not None:
            return False
        status = getattr(p, "status", None) or "not_started"
        expected = "in_progress" if i == 0 else "not_started"
        if status != expected:
            return False
    return True


def resolve_phase_name_in_map(guessed: str | None, by_name: dict[str, object]) -> object | None:
    """把猜到的规范名映射到项目实际阶段行（含新旧词兼容）。"""
    if not guessed:
        return None
    if guessed in by_name:
        return by_name[guessed]
    aliases = {
        "开发": ["实施/开发"],
        "实施/开发": ["开发"],
        "方案设计": ["设计"],
        "设计": ["方案设计"],
        "现场实施": ["实施/开发", "开发"],
        "联调测试": ["测试", "实施/开发"],
        "验收交付": ["验收"],
        "验收": ["验收交付"],
    }
    for alt in aliases.get(guessed, []):
        if alt in by_name:
            return by_name[alt]
    return None
