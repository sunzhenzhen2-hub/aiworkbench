"""PC 钉钉扫码 OAuth state 持久化：校验与撤销分离，避免双请求竞态。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OAuthState

STATE_TTL = timedelta(minutes=10)


async def save_oauth_state(db: AsyncSession, state: str, redirect_uri: str) -> None:
    db.add(OAuthState(state=state, redirect_uri=redirect_uri))
    await db.commit()


async def validate_oauth_state(db: AsyncSession, state: str) -> str | None:
    """返回 redirect_uri；过期或不存在返回 None。不删除记录。"""
    if not state:
        return None
    result = await db.execute(select(OAuthState).where(OAuthState.state == state))
    row = result.scalar_one_or_none()
    if not row:
        return None
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created < datetime.now(timezone.utc) - STATE_TTL:
        return None
    return row.redirect_uri


async def revoke_oauth_state(db: AsyncSession, state: str) -> None:
    await db.execute(delete(OAuthState).where(OAuthState.state == state))
    await db.commit()


async def purge_expired_oauth_states(db: AsyncSession) -> None:
    cutoff = datetime.now(timezone.utc) - STATE_TTL
    await db.execute(delete(OAuthState).where(OAuthState.created_at < cutoff))
    await db.commit()
