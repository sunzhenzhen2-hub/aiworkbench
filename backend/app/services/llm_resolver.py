"""按用途解析启用的 LLM 配置；专用优先，通用次之，.env 回退。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import LlmModel

GENERAL_PURPOSE = "general"


@dataclass
class ResolvedLlm:
    api_base: str
    api_key: str
    model_name: str
    source: str  # db | env
    name: str = ""


def mask_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return "（未设置）"
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def _row_to_resolved(row: LlmModel) -> ResolvedLlm | None:
    if not row.api_base or not row.api_key:
        return None
    return ResolvedLlm(
        api_base=row.api_base.rstrip("/"),
        api_key=row.api_key,
        model_name=row.model_name or "gpt-4o-mini",
        source="db",
        name=row.name,
    )


async def _pick_enabled(db: AsyncSession, purpose: str) -> LlmModel | None:
    """同用途：优先默认启用项，否则按 sort_order/id 取第一条启用项。"""
    result = await db.execute(
        select(LlmModel)
        .where(
            LlmModel.purpose == purpose,
            LlmModel.is_enabled.is_(True),
            LlmModel.is_default.is_(True),
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row:
        return row
    result = await db.execute(
        select(LlmModel)
        .where(LlmModel.purpose == purpose, LlmModel.is_enabled.is_(True))
        .order_by(LlmModel.sort_order, LlmModel.id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def resolve_llm(db: AsyncSession, purpose: str = "report_coach") -> ResolvedLlm | None:
    # 1) 该用途专用配置
    row = await _pick_enabled(db, purpose)
    resolved = _row_to_resolved(row) if row else None
    if resolved:
        return resolved

    # 2) 未命中专用时，回退「通用」用途（查 general 本身时不再递归）
    if purpose != GENERAL_PURPOSE:
        row = await _pick_enabled(db, GENERAL_PURPOSE)
        resolved = _row_to_resolved(row) if row else None
        if resolved:
            return resolved

    # 3) .env
    if settings.openai_api_base and settings.openai_api_key:
        return ResolvedLlm(
            api_base=settings.openai_api_base.rstrip("/"),
            api_key=settings.openai_api_key,
            model_name="gpt-4o-mini",
            source="env",
            name="环境变量",
        )
    return None


async def clear_other_defaults(db: AsyncSession, purpose: str, keep_id: int | None) -> None:
    result = await db.execute(
        select(LlmModel).where(LlmModel.purpose == purpose, LlmModel.is_default.is_(True))
    )
    for row in result.scalars().all():
        if keep_id is not None and row.id == keep_id:
            continue
        row.is_default = False
