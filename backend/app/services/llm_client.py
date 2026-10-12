"""OpenAI 兼容 chat/completions 连通探测与模型列表。"""

from __future__ import annotations

import httpx


async def test_openai_compatible(
    api_base: str, api_key: str, model_name: str, *, timeout: float = 15.0
) -> tuple[bool, str]:
    base = (api_base or "").rstrip("/")
    if not base:
        return False, "请填写 API Base"
    if not api_key:
        return False, "请填写 API Key"
    if not model_name:
        return False, "请填写模型名称"

    url = f"{base}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                    "temperature": 0,
                },
            )
            if resp.status_code >= 400:
                detail = resp.text[:200]
                return False, f"接口返回 {resp.status_code}：{detail}"
            data = resp.json()
            if not data.get("choices"):
                return False, "返回格式异常：缺少 choices"
            return True, "连通成功"
    except httpx.TimeoutException:
        return False, "连接超时，请检查地址或网络"
    except Exception as exc:  # noqa: BLE001
        return False, f"调用失败：{exc}"


async def list_openai_compatible_models(
    api_base: str, api_key: str, *, timeout: float = 20.0
) -> tuple[bool, list[str], str]:
    """
    GET {api_base}/models，解析 data[].id。
    返回 (ok, model_ids, message)。
    """
    base = (api_base or "").rstrip("/")
    if not base:
        return False, [], "请填写 API Base"
    if not (api_key or "").strip():
        return False, [], "请填写 API Key"

    url = f"{base}/models"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {api_key.strip()}"},
            )
            if resp.status_code >= 400:
                detail = resp.text[:200]
                return False, [], f"接口返回 {resp.status_code}：{detail}"
            data = resp.json()
            raw = data.get("data")
            if not isinstance(raw, list):
                return False, [], "返回格式异常：缺少 data 列表"
            ids: list[str] = []
            seen: set[str] = set()
            for item in raw:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("id") or "").strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                ids.append(mid)
            ids.sort(key=lambda x: x.lower())
            if not ids:
                return False, [], "未获取到任何模型，请确认密钥与接口地址"
            return True, ids, f"共 {len(ids)} 个模型"
    except httpx.TimeoutException:
        return False, [], "连接超时，请检查地址或网络"
    except Exception as exc:  # noqa: BLE001
        return False, [], f"调用失败：{exc}"
