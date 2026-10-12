"""方案库 AI 整理：摘要 + 标签；文档走文本，图片走多模态。"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import async_session
from app.models import Solution, SolutionTag
from app.services.llm_resolver import resolve_llm
from app.services.solution_extract import extract_text_from_file
from app.services.solution_storage import absolute_path, is_image_filename

SYSTEM_PROMPT = (
    "你是项目管理知识库助手。根据用户提供的历史方案内容，整理成便于查阅的结构化信息。"
    "只返回 JSON，不要 Markdown，格式："
    '{"title":"简洁中文标题","summary":"80-200字中文摘要","tags":["标签1","标签2"]}'
    "标签 3～8 个，短词，涵盖：方案类型、行业/场景、技术栈或关键方法、适用阶段。"
    "不要编造文件中不存在的具体数据。"
)


def _parse_json_content(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 尝试截取第一个 { ... }
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def _normalize_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in tags:
        name = str(t).strip()[:64]
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
        if len(out) >= 10:
            break
    return out


def _stem_title(filename: str) -> str:
    return Path(filename).stem[:256] or "未命名方案"


async def _call_llm(
    *,
    api_base: str,
    api_key: str,
    model_name: str,
    user_content: str | list[dict[str, Any]],
    timeout: float = 90.0,
) -> dict[str, Any]:
    url = api_base.rstrip("/") + "/chat/completions"
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 800,
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"模型接口 {resp.status_code}：{resp.text[:300]}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("模型返回缺少 choices")
        content = choices[0].get("message", {}).get("content") or ""
        return _parse_json_content(content)


def _image_data_url(path: Path, mime: str) -> str:
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    media = mime if mime.startswith("image/") else "image/jpeg"
    return f"data:{media};base64,{b64}"


async def process_solution(solution_id: int) -> None:
    """后台任务：抽取 + 调用 LLM + 写回摘要标签。"""
    async with async_session() as db:
        result = await db.execute(
            select(Solution)
            .options(selectinload(Solution.tags))
            .where(Solution.id == solution_id)
        )
        sol = result.scalar_one_or_none()
        if not sol:
            return

        sol.status = "processing"
        sol.error_message = ""
        await db.commit()

        try:
            path = absolute_path(sol.stored_path)
        except FileNotFoundError:
            sol.status = "failed"
            sol.error_message = "文件丢失，无法整理"
            await db.commit()
            return

        resolved = await resolve_llm(db, purpose="solution_tag")
        if not resolved:
            # 回退到任意默认用途模型（周报），至少能处理文本
            resolved = await resolve_llm(db, purpose="report_coach")
        if not resolved:
            sol.status = "failed"
            sol.error_message = (
                "未配置 AI 模型。请在「系统管理 → 模型管理」添加用途为「方案打标」的模型，"
                "或配置 OPENAI_API_BASE/KEY。"
            )
            await db.commit()
            return

        try:
            if is_image_filename(sol.original_filename):
                data_url = _image_data_url(path, sol.mime_type or "image/jpeg")
                user_content: str | list[dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": (
                            f"这是一份历史方案相关图片，文件名：{sol.original_filename}。"
                            "请阅读图中文字与内容，整理标题、摘要与标签。"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            else:
                text = extract_text_from_file(path, sol.original_filename)
                if not text.strip():
                    sol.status = "failed"
                    sol.error_message = "未能从文件中抽取到可读文字，请检查文件或改用图片/文本方案"
                    await db.commit()
                    return
                user_content = (
                    f"文件名：{sol.original_filename}\n\n方案正文如下：\n{text}"
                )

            parsed = await _call_llm(
                api_base=resolved.api_base,
                api_key=resolved.api_key,
                model_name=resolved.model_name,
                user_content=user_content,
            )
        except Exception as exc:  # noqa: BLE001
            sol.status = "failed"
            sol.error_message = f"AI 整理失败：{exc}"[:500]
            await db.commit()
            return

        ai_title = str(parsed.get("title") or "").strip()[:256]
        ai_summary = str(parsed.get("summary") or "").strip()
        ai_tags = _normalize_tags(parsed.get("tags"))

        stem = _stem_title(sol.original_filename)
        current = (sol.title or "").strip()
        if ai_title and (not current or current == stem or current == sol.original_filename):
            sol.title = ai_title
        elif not current:
            sol.title = ai_title or stem

        if ai_summary:
            sol.summary = ai_summary

        # 替换 AI 标签，保留手动标签
        manual_names = {t.name for t in sol.tags if t.source == "manual"}
        await db.execute(
            delete(SolutionTag).where(
                SolutionTag.solution_id == sol.id,
                SolutionTag.source == "ai",
            )
        )
        for name in ai_tags:
            if name in manual_names:
                continue
            db.add(SolutionTag(solution_id=sol.id, name=name, source="ai"))

        sol.status = "ready"
        sol.error_message = ""
        await db.commit()


async def reprocess_solution(db: AsyncSession, solution_id: int) -> Solution | None:
    """标记 processing 并触发整理（由调用方安排 BackgroundTasks）。"""
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    sol = result.scalar_one_or_none()
    if not sol:
        return None
    sol.status = "pending"
    sol.error_message = ""
    await db.commit()
    return sol
