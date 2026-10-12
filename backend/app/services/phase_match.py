"""根据周报/任务文字猜测应挂到的项目阶段。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Phase, Project
from app.services.project_phases import resolve_phase_name_in_map
from app.services.project_service import DAILY_PROJECT_NAME, seed_standard_phases

# 阶段名 -> 文中可能出现的叫法（长的优先匹配；避免「手表测试」误挂测试阶段）
_PHASE_ALIASES: list[tuple[str, list[str]]] = [
    ("需求分析", ["需求分析", "需求调研", "需求梳理", "需求沟通", "需求评审"]),
    ("立项", ["立项", "项目启动", "启动会"]),
    ("方案设计", ["方案设计", "实施方案"]),
    ("设计", ["详细设计", "概要设计", "UI设计", "原型设计", "设计评审"]),
    ("采购到货", ["采购到货", "设备到货", "采购下单", "到货验收"]),
    ("现场实施", ["现场实施", "现场安装", "施工", "布线", "安装调试"]),
    ("联调测试", ["联调测试", "集成测试", "联调"]),
    ("开发", ["实施/开发", "功能开发", "编码", "研发", "开发实施", "开发"]),
    ("测试", ["验收测试", "测试阶段", "功能测试"]),
    ("上线", ["正式上线", "灰度上线", "上线", "发布", "投产"]),
    ("运维保障", ["运维保障", "运维巡检", "巡检", "售后运维"]),
    ("验收交付", ["验收交付", "交付验收"]),
    ("验收", ["初验", "终验", "项目验收"]),
    ("收尾", ["收尾", "结项", "归档"]),
]


def _blob(*parts: str | None) -> str:
    return " ".join((p or "").strip() for p in parts if (p or "").strip())


def guess_phase_names_from_text(*parts: str | None) -> list[str]:
    """按别名长度降序返回可能的阶段名（可多项，供按项目实际阶段择优）。"""
    text = _blob(*parts)
    if not text:
        return []
    candidates: list[tuple[int, str]] = []
    for phase_name, aliases in _PHASE_ALIASES:
        for alias in aliases:
            if alias and alias in text:
                candidates.append((len(alias), phase_name))
                break
    if not candidates:
        return []
    candidates.sort(key=lambda x: x[0], reverse=True)
    # 同长度保留全部，便于软件「开发」与集成「联调测试」按项目择优
    out: list[str] = []
    seen: set[str] = set()
    for _, name in candidates:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def guess_phase_name_from_text(*parts: str | None) -> str | None:
    """从文字里猜标准阶段名；猜不到返回 None（由项目当前阶段兜底）。"""
    names = guess_phase_names_from_text(*parts)
    return names[0] if names else None


async def guess_phase_id(
    db: AsyncSession,
    project_id: int,
    *parts: str | None,
    current_phase: str | None = None,
) -> int | None:
    """
    为项目下任务猜 phase_id。
    日常任务项目不挂阶段；没有阶段时先种标准阶段。
    """
    project = await db.get(Project, project_id)
    if not project or project.name == DAILY_PROJECT_NAME:
        return None

    await seed_standard_phases(db, project_id, project.project_type)
    rows = list(
        (
            await db.execute(
                select(Phase).where(Phase.project_id == project_id).order_by(Phase.sort_order)
            )
        ).scalars().all()
    )
    if not rows:
        return None

    by_name = {p.name: p for p in rows}
    for guessed in guess_phase_names_from_text(*parts):
        resolved = resolve_phase_name_in_map(guessed, by_name)
        if resolved is not None:
            return getattr(resolved, "id")

    # 文字里直接写了某个阶段全名（短名如「测试」不做子串匹配，避免「手表测试」误挂）
    blob = _blob(*parts)
    for name, phase in by_name.items():
        if name and len(name) >= 3 and name in blob:
            return phase.id

    cur = (current_phase or project.current_phase or "").strip()
    if cur and cur in by_name:
        return by_name[cur].id
    for name, phase in by_name.items():
        if cur and (cur in name or name in cur):
            return phase.id
    resolved_cur = resolve_phase_name_in_map(cur, by_name)
    if resolved_cur is not None:
        return getattr(resolved_cur, "id")

    for phase in rows:
        if phase.status == "in_progress":
            return phase.id
    return rows[0].id
