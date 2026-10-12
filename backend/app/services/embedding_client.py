"""OpenAI 兼容 embeddings 客户端（约定 BGE-M3）。未配模型时不调用。"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.embedding_settings import cached_config


class EmbeddingError(RuntimeError):
    pass


def vector_enabled() -> bool:
    return bool(cached_config().enabled)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化。要求已配置 embedding_model + embedding_base_url（库或 .env）。"""
    cfg = cached_config()
    if not cfg.enabled:
        raise EmbeddingError("向量未启用：请在系统设置配置向量模型，或填写 EMBEDDING_MODEL / EMBEDDING_BASE_URL")
    cleaned = [str(t or "").strip() for t in texts if str(t or "").strip()]
    if not cleaned:
        return []
    base = cfg.base_url.rstrip("/")
    url = f"{base}/embeddings" if base.endswith("/v1") else f"{base}/v1/embeddings"
    headers: dict[str, str] = {"content-type": "application/json"}
    key = (cfg.api_key or "").strip()
    if key:
        headers["authorization"] = f"Bearer {key}"
    payload: dict[str, Any] = {
        "model": cfg.model.strip(),
        "input": cleaned if len(cleaned) > 1 else cleaned[0],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    rows = data.get("data") or []
    if not isinstance(rows, list) or not rows:
        raise EmbeddingError("embeddings 响应缺少 data")
    ordered = sorted(rows, key=lambda r: int(r.get("index") or 0))
    out: list[list[float]] = []
    for row in ordered:
        vec = row.get("embedding")
        if not isinstance(vec, list) or not vec:
            raise EmbeddingError("embeddings 条目缺少向量")
        out.append([float(x) for x in vec])
    if len(out) != len(cleaned):
        raise EmbeddingError(f"向量条数不匹配: got {len(out)} want {len(cleaned)}")
    return out


async def embed_one(text: str) -> list[float]:
    vecs = await embed_texts([text])
    return vecs[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))
