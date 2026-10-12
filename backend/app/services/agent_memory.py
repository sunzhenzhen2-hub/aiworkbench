"""一人一助手长期记忆：偏好/摘要笔记 + 可检索片段。V1 关键词；V2 可选向量。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AgentMemoryChunk, AgentMemoryNote, AgentUserPref, User
from app.services import embedding_client as emb

_ALLOWED_KINDS = frozenset({"pref", "work_summary", "fact"})
_ALLOWED_SOURCES = frozenset({"user", "agent", "ledger"})
_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _tokens(query: str) -> list[str]:
    parts = _TOKEN_RE.findall((query or "").lower())
    # 中文单字噪声多，保留长度>=2；英文同理
    out = [p for p in parts if len(p) >= 2]
    return out[:12] or ([parts[0]] if parts else [])


def _keyword_score(text: str, tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    hay = (text or "").lower()
    hits = sum(1 for t in tokens if t in hay)
    return hits / len(tokens)


async def memory_get(db: AsyncSession, user: User, *, note_limit: int = 20) -> dict[str, Any]:
    prefs = (
        await db.execute(select(AgentUserPref).where(AgentUserPref.user_id == user.id))
    ).scalar_one_or_none()
    prefs_out: dict[str, Any] = {}
    if prefs:
        prefs_out = {
            "quiet_start": prefs.quiet_start.isoformat() if prefs.quiet_start else None,
            "quiet_end": prefs.quiet_end.isoformat() if prefs.quiet_end else None,
            "digest_mode": prefs.digest_mode,
            "prefer_p0": prefs.prefer_p0,
            "channel_inbox": prefs.channel_inbox,
            "channel_dingtalk": prefs.channel_dingtalk,
            "channel_dsh": prefs.channel_dsh,
        }
    notes = (
        await db.execute(
            select(AgentMemoryNote)
            .where(AgentMemoryNote.user_id == user.id)
            .order_by(AgentMemoryNote.updated_at.desc())
            .limit(max(1, min(note_limit, 50)))
        )
    ).scalars().all()
    work_summary = next((n for n in notes if n.kind == "work_summary"), None)
    prefs_notes = [n for n in notes if n.kind == "pref"]
    recent = [n for n in notes if n.kind == "fact"]
    return {
        "vector_enabled": emb.vector_enabled(),
        "prefs": prefs_out,
        "work_summary": _note_out(work_summary) if work_summary else None,
        "pref_notes": [_note_out(n) for n in prefs_notes[:10]],
        "recent_facts": [_note_out(n) for n in recent[:10]],
        "hint": "记忆不是账本；待办/任务/周报须 MCP draft→confirm。",
    }


def _note_out(note: AgentMemoryNote) -> dict[str, Any]:
    return {
        "id": note.id,
        "kind": note.kind,
        "title": note.title,
        "body": note.body,
        "source": note.source,
        "updated_at": _iso(note.updated_at),
    }


async def memory_upsert(db: AsyncSession, user: User, payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "fact").strip()
    if kind not in _ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail="kind 须为 pref / work_summary / fact")
    source = str(payload.get("source") or "agent").strip()
    if source not in _ALLOWED_SOURCES:
        raise HTTPException(status_code=400, detail="source 无效")
    title = str(payload.get("title") or "").strip()[:256]
    body = str(payload.get("body") or "").strip()
    if not body and not title:
        raise HTTPException(status_code=400, detail="缺少 title 或 body")
    note_id = payload.get("id")
    note: AgentMemoryNote | None = None
    if note_id is not None:
        try:
            nid = int(note_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="id 无效") from exc
        note = (
            await db.execute(
                select(AgentMemoryNote).where(
                    AgentMemoryNote.id == nid, AgentMemoryNote.user_id == user.id
                )
            )
        ).scalar_one_or_none()
        if not note:
            raise HTTPException(status_code=404, detail="记忆不存在")
    elif kind == "work_summary":
        # 每人一条滚动工作摘要
        note = (
            await db.execute(
                select(AgentMemoryNote).where(
                    AgentMemoryNote.user_id == user.id,
                    AgentMemoryNote.kind == "work_summary",
                )
            )
        ).scalar_one_or_none()
    if note is None:
        note = AgentMemoryNote(user_id=user.id, kind=kind, source=source)
        db.add(note)
    note.kind = kind
    note.source = source
    if title:
        note.title = title
    elif kind == "work_summary" and not note.title:
        note.title = "工作摘要"
    note.body = body or note.body
    note.updated_at = datetime.now(timezone.utc)
    await db.flush()
    # 同步一条可检索 chunk（笔记全文）
    await _upsert_chunk(
        db,
        user,
        source_type="note",
        source_ref=f"note:{note.id}",
        text=f"{note.title}\n{note.body}".strip(),
    )
    await db.commit()
    await db.refresh(note)
    return {"ok": True, "note": _note_out(note)}


async def _upsert_chunk(
    db: AsyncSession,
    user: User,
    *,
    source_type: str,
    source_ref: str,
    text: str,
) -> AgentMemoryChunk:
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="空文本不可入库")
    chunk = (
        await db.execute(
            select(AgentMemoryChunk).where(
                AgentMemoryChunk.user_id == user.id,
                AgentMemoryChunk.source_type == source_type,
                AgentMemoryChunk.source_ref == source_ref,
            )
        )
    ).scalar_one_or_none()
    if chunk is None:
        chunk = AgentMemoryChunk(
            user_id=user.id,
            source_type=source_type,
            source_ref=source_ref,
            text=text,
        )
        db.add(chunk)
    else:
        chunk.text = text
        chunk.updated_at = datetime.now(timezone.utc)
    if emb.vector_enabled():
        try:
            chunk.embedding = await emb.embed_one(text[:4000])
        except Exception:
            # V2 失败不挡 V1 写入
            chunk.embedding = chunk.embedding
    await db.flush()
    return chunk


async def memory_search(
    db: AsyncSession,
    user: User,
    *,
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    q = (query or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="缺少 query")
    limit = max(1, min(int(limit or 10), 50))
    tokens = _tokens(q)
    # 本人笔记 + chunks
    notes = (
        await db.execute(select(AgentMemoryNote).where(AgentMemoryNote.user_id == user.id))
    ).scalars().all()
    chunks = (
        await db.execute(select(AgentMemoryChunk).where(AgentMemoryChunk.user_id == user.id))
    ).scalars().all()

    candidates: list[dict[str, Any]] = []
    for note in notes:
        blob = f"{note.title}\n{note.body}"
        score = _keyword_score(blob, tokens)
        if score <= 0:
            continue
        candidates.append(
            {
                "kind": "note",
                "id": note.id,
                "note_kind": note.kind,
                "title": note.title,
                "text": blob[:800],
                "source_ref": f"note:{note.id}",
                "keyword_score": score,
                "vector_score": 0.0,
            }
        )
    for chunk in chunks:
        score = _keyword_score(chunk.text, tokens)
        if score <= 0 and not emb.vector_enabled():
            continue
        candidates.append(
            {
                "kind": "chunk",
                "id": chunk.id,
                "source_type": chunk.source_type,
                "source_ref": chunk.source_ref,
                "text": (chunk.text or "")[:800],
                "keyword_score": score,
                "vector_score": 0.0,
                "_embedding": chunk.embedding,
            }
        )

    mode = "keyword"
    if emb.vector_enabled():
        mode = "hybrid"
        try:
            qvec = await emb.embed_one(q[:2000])
            for row in candidates:
                vec = row.pop("_embedding", None)
                if isinstance(vec, list) and vec:
                    row["vector_score"] = emb.cosine_similarity(qvec, vec)
                else:
                    row["vector_score"] = 0.0
        except Exception:
            mode = "keyword"
            for row in candidates:
                row.pop("_embedding", None)
    else:
        for row in candidates:
            row.pop("_embedding", None)

    def _rank(row: dict[str, Any]) -> float:
        return float(row.get("keyword_score") or 0) * 0.55 + float(row.get("vector_score") or 0) * 0.45

    # 无关键词命中但有向量时：保留 vector_score>0.35 的
    filtered = [
        r
        for r in candidates
        if float(r.get("keyword_score") or 0) > 0
        or (mode == "hybrid" and float(r.get("vector_score") or 0) >= 0.35)
    ]
    filtered.sort(key=_rank, reverse=True)
    hits = filtered[:limit]
    return {
        "ok": True,
        "mode": mode,
        "query": q,
        "items": hits,
        "count": len(hits),
    }


async def ingest_session_excerpt(
    db: AsyncSession,
    user: User,
    *,
    session_id: str,
    text: str,
) -> dict[str, Any]:
    """把一段会话摘录入库（工具或后台调用）。"""
    sid = str(session_id or "").strip()[:200]
    if not sid:
        raise HTTPException(status_code=400, detail="缺少 session_id")
    chunk = await _upsert_chunk(
        db,
        user,
        source_type="session",
        source_ref=sid,
        text=text,
    )
    await db.commit()
    return {
        "ok": True,
        "chunk_id": chunk.id,
        "source_ref": chunk.source_ref,
        "has_embedding": bool(chunk.embedding),
    }


def _safe_username(username: str) -> str:
    name = (username or "").strip()
    if not name or ".." in name or "/" in name or "\\" in name:
        raise ValueError("bad username")
    return name


def read_user_session_texts(username: str, *, max_sessions: int = 20, max_chars: int = 4000) -> list[dict[str, str]]:
    """只读本人磁盘会话目录，抽出可索引文本（不跨用户）。"""
    name = _safe_username(username)
    root = Path(settings.dsh_users_root).resolve() / name / "home" / "sessions"
    if not root.is_dir():
        return []
    out: list[dict[str, str]] = []
    session_dirs = sorted(
        [p for p in root.rglob("session-*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for folder in session_dirs[:max_sessions]:
        # 优先未压缩的可读文本旁路；压缩 jsonl.zstd 由工具侧传摘录
        plain = folder / "excerpt.txt"
        if plain.is_file():
            text = plain.read_text(encoding="utf-8", errors="replace")[:max_chars]
            if text.strip():
                out.append({"session_id": folder.name, "text": text.strip()})
    return out


async def ingest_workspace_excerpts(db: AsyncSession, user: User) -> dict[str, Any]:
    """扫描本人会话 excerpt.txt（若有）写入 chunks。不解压 zstd，避免重依赖。"""
    rows = read_user_session_texts(user.username)
    count = 0
    for row in rows:
        await _upsert_chunk(
            db,
            user,
            source_type="session",
            source_ref=row["session_id"],
            text=row["text"],
        )
        count += 1
    await db.commit()
    return {"ok": True, "ingested": count, "vector_enabled": emb.vector_enabled()}
