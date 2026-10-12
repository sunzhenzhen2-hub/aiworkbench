"""统一 AI 平台：按 purpose 调模型，强制 JSON 结构化输出；无模型时由调用方规则兜底。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_resolver import ResolvedLlm, resolve_llm

logger = logging.getLogger(__name__)

# 业务用途一览（Admin 可按 purpose 配默认模型；general 为全站兜底）
AI_PURPOSES = (
    "general",
    "report_coach",
    "report_structure",
    "report_review",
    "solution_tag",
    "task_draft",
    "task_breakdown",
    "task_polish",
    "task_review",
    "project_draft",
    "blocker_draft",
    "overview_brief",
    "solution_search",
    "assist_analyze",
)


def extract_json_obj(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def resolve_with_fallback(
    db: AsyncSession, purpose: str, *fallbacks: str
) -> ResolvedLlm | None:
    resolved = await resolve_llm(db, purpose)
    if resolved:
        return resolved
    for p in fallbacks:
        resolved = await resolve_llm(db, p)
        if resolved:
            return resolved
    return await resolve_llm(db, "report_coach")


async def run_structured(
    db: AsyncSession,
    *,
    purpose: str,
    system: str,
    user: str,
    fallback_purposes: tuple[str, ...] = ("report_coach",),
    temperature: float = 0.2,
    max_tokens: int = 1200,
    timeout: float = 45.0,
) -> tuple[dict[str, Any] | None, str]:
    """
    调用 LLM 并解析为 JSON 对象。
    返回 (data|None, source) source= llm | empty | error
    """
    resolved = await resolve_with_fallback(db, purpose, *fallback_purposes)
    if not resolved:
        return None, "empty"

    url = f"{resolved.api_base}/chat/completions"
    payload = {
        "model": resolved.model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {resolved.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code >= 400:
                logger.warning("AI %s HTTP %s: %s", purpose, resp.status_code, resp.text[:200])
                return None, "error"
            data = resp.json()
            content = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            )
            parsed = extract_json_obj(content)
            if not parsed:
                logger.warning("AI %s JSON parse fail: %s", purpose, content[:200])
                return None, "error"
            return parsed, "llm"
    except Exception:  # noqa: BLE001
        logger.exception("AI %s call failed", purpose)
        return None, "error"


async def run_text_stream(
    db: AsyncSession,
    *,
    purpose: str,
    system: str,
    user: str,
    fallback_purposes: tuple[str, ...] = ("report_coach",),
    temperature: float = 0.3,
    max_tokens: int = 800,
    timeout: float = 60.0,
):
    """
    流式调用 LLM。
    Yields: ("delta", text) | ("done", source)，source 为 llm | empty | error。
    """
    resolved = await resolve_with_fallback(db, purpose, *fallback_purposes)
    if not resolved:
        yield ("done", "empty")
        return

    url = f"{resolved.api_base}/chat/completions"
    payload = {
        "model": resolved.model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {resolved.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread())[:200]
                    logger.warning("AI %s stream HTTP %s: %s", purpose, resp.status_code, body)
                    yield ("done", "error")
                    return
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data:"):
                        data_str = line[5:].strip()
                    else:
                        data_str = line.strip()
                    if not data_str or data_str == "[DONE]":
                        if data_str == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content") or ""
                    if delta:
                        yield ("delta", delta)
        yield ("done", "llm")
    except Exception:  # noqa: BLE001
        logger.exception("AI %s stream failed", purpose)
        yield ("done", "error")
