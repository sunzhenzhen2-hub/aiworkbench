"""向量模型配置：库优先，.env 回落；内存缓存供 embedding_client 同步读取。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import SystemSetting
from app.services.llm_resolver import mask_api_key

KEY_MODEL = "embedding_model"
KEY_BASE = "embedding_base_url"
KEY_API_KEY = "embedding_api_key"

_KEYS = (KEY_MODEL, KEY_BASE, KEY_API_KEY)

_cache: EmbeddingConfig | None = None
_cache_ready = False


@dataclass
class EmbeddingConfig:
    model: str
    base_url: str
    api_key: str
    source: str  # db | env | mixed

    @property
    def enabled(self) -> bool:
        return bool(self.model.strip() and self.base_url.strip())


def _from_env() -> EmbeddingConfig:
    return EmbeddingConfig(
        model=(settings.embedding_model or "").strip(),
        base_url=(settings.embedding_base_url or "").strip(),
        api_key=(settings.embedding_api_key or "").strip(),
        source="env",
    )


def reset_embedding_cache() -> None:
    """单测用：清缓存。"""
    global _cache, _cache_ready
    _cache = None
    _cache_ready = False


def set_cached_config(cfg: EmbeddingConfig | None, *, ready: bool = True) -> None:
    """探活/单测：直接改进程缓存。"""
    global _cache, _cache_ready
    _cache = cfg
    _cache_ready = ready


def cached_config() -> EmbeddingConfig:
    """同步读取：有缓存用缓存，否则仅 .env。"""
    if _cache_ready and _cache is not None:
        return _cache
    return _from_env()


async def load_embedding_config(db: AsyncSession) -> EmbeddingConfig:
    """库中存在的键覆盖 .env；model/base 以库为准（可空=关 V2）；密钥库空则回落 .env。"""
    global _cache, _cache_ready
    env = _from_env()
    rows = (
        await db.execute(select(SystemSetting).where(SystemSetting.key.in_(_KEYS)))
    ).scalars().all()
    by_key = {r.key: (r.value if r.value is not None else "") for r in rows}

    used_db = False
    if KEY_MODEL in by_key:
        model = str(by_key[KEY_MODEL]).strip()
        used_db = True
    else:
        model = env.model

    if KEY_BASE in by_key:
        base = str(by_key[KEY_BASE]).strip().rstrip("/")
        used_db = True
    else:
        base = env.base_url

    if KEY_API_KEY in by_key and str(by_key[KEY_API_KEY]).strip():
        key = str(by_key[KEY_API_KEY]).strip()
        used_db = True
    else:
        key = env.api_key

    if used_db and (env.model or env.base_url or env.api_key):
        source = "mixed"
    elif used_db:
        source = "db"
    else:
        source = "env"

    cfg = EmbeddingConfig(model=model, base_url=base, api_key=key, source=source)
    _cache = cfg
    _cache_ready = True
    return cfg


async def save_embedding_config(
    db: AsyncSession,
    *,
    model: str,
    base_url: str,
    api_key: str | None = None,
    clear_api_key: bool = False,
) -> EmbeddingConfig:
    """保存。api_key 有值则更新；clear_api_key 清空库密钥；否则保留原库密钥。"""
    model = (model or "").strip()
    base_url = (base_url or "").strip().rstrip("/")
    existing = (
        await db.execute(select(SystemSetting).where(SystemSetting.key.in_(_KEYS)))
    ).scalars().all()
    by_key = {r.key: r for r in existing}

    def upsert(key: str, value: str) -> None:
        row = by_key.get(key)
        if row is None:
            row = SystemSetting(key=key, value=value)
            db.add(row)
            by_key[key] = row
        else:
            row.value = value

    upsert(KEY_MODEL, model)
    upsert(KEY_BASE, base_url)
    if clear_api_key:
        upsert(KEY_API_KEY, "")
    elif api_key is not None and str(api_key).strip():
        upsert(KEY_API_KEY, str(api_key).strip())
    elif KEY_API_KEY not in by_key:
        upsert(KEY_API_KEY, "")

    await db.flush()
    return await load_embedding_config(db)


def public_view(cfg: EmbeddingConfig) -> dict:
    return {
        "embedding_model": cfg.model,
        "embedding_base_url": cfg.base_url,
        "api_key_masked": mask_api_key(cfg.api_key),
        "api_key_configured": bool(cfg.api_key.strip()),
        "enabled": cfg.enabled,
        "source": cfg.source,
        "hint": "模型名与 API 地址都填才启用向量检索（V2）；清空模型名即回退关键词（V1）。",
    }
