"""向钉钉开放平台换取用户身份。AppSecret 只在服务端使用。

PC 扫码走 OAuth（resolve_userid_by_oauth）；
小程序免登走 JSAPI（resolve_userid）。两套禁止混用。
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.core.config import settings

_token_cache: tuple[str, float] | None = None
NETWORK_MSG = "连不上钉钉，请稍后刷新二维码重试"


class DingTalkError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@asynccontextmanager
async def _client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
        try:
            yield client
        except httpx.TimeoutException as exc:
            raise DingTalkError(NETWORK_MSG, status_code=502) from exc
        except httpx.RequestError as exc:
            raise DingTalkError(NETWORK_MSG, status_code=502) from exc


async def _fetch_access_token() -> str:
    if not settings.dingtalk_app_key or not settings.dingtalk_app_secret:
        raise DingTalkError("钉钉应用未配置", status_code=500)
    async with _client() as client:
        resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={
                "appKey": settings.dingtalk_app_key,
                "appSecret": settings.dingtalk_app_secret,
            },
        )
        data = resp.json()
        token = data.get("accessToken")
        if token:
            return token
        # 兼容旧版 gettoken
        resp = await client.get(
            "https://oapi.dingtalk.com/gettoken",
            params={
                "appkey": settings.dingtalk_app_key,
                "appsecret": settings.dingtalk_app_secret,
            },
        )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise DingTalkError("获取钉钉凭证失败")
        return token


async def get_access_token() -> str:
    global _token_cache
    now = time.time()
    if _token_cache and _token_cache[1] > now + 60:
        return _token_cache[0]
    token = await _fetch_access_token()
    _token_cache = (token, now + 7000)
    return token


async def resolve_userid(auth_code: str) -> str:
    """用一次性免登码换出钉钉 userid（小程序 JSAPI）。测试时可整体替换此函数。"""
    if not auth_code:
        raise DingTalkError("缺少免登码", status_code=400)
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/v2/user/getuserinfo",
            params={"access_token": token},
            json={"code": auth_code},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise DingTalkError("钉钉返回无法解析") from exc
    if data.get("errcode") not in (0, None) and not data.get("result"):
        raise DingTalkError("钉钉登录失败，请重试")
    userid = (data.get("result") or {}).get("userid") or data.get("userid")
    if not userid:
        raise DingTalkError("未能获取钉钉用户")
    return str(userid)


async def _oauth_user_access_token(code: str, redirect_uri: str) -> tuple[str, str]:
    """OAuth 授权码 → userAccessToken + 可选 unionId。"""
    async with _client() as client:
        resp = await client.post(
            "https://api.dingtalk.com/v1.0/oauth2/userAccessToken",
            json={
                "clientId": settings.dingtalk_app_key,
                "clientSecret": settings.dingtalk_app_secret,
                "code": code,
                "grantType": "authorization_code",
                "redirectUri": redirect_uri,
            },
        )
        data = resp.json()
    access_token = data.get("accessToken")
    if not access_token:
        msg = str(data.get("message") or data.get("requestid") or "unknown")
        hint = ""
        if re.search(r"不合法|notFound|40078|nonexistent", msg, re.I):
            hint = "（请确认 redirect_uri 与开放平台白名单一字不差，且与登录页同源）"
        raise DingTalkError(f"获取userAccessToken失败：{msg}{hint}", status_code=401)
    return str(access_token), str(data.get("unionId") or "")


async def _fetch_union_id(user_access_token: str) -> str:
    """从 OAuth userinfo / contact 接口补拿 unionId。"""
    headers = {"x-acs-dingtalk-access-token": user_access_token}
    async with _client() as client:
        resp = await client.get("https://api.dingtalk.com/v1.0/oauth2/userinfo", headers=headers)
        data = resp.json()
        if data.get("unionId"):
            return str(data["unionId"])
        resp = await client.get("https://api.dingtalk.com/v1.0/contact/users/me", headers=headers)
        data = resp.json()
        if data.get("unionId"):
            return str(data["unionId"])
    return ""


async def _userid_by_union_id(union_id: str) -> str:
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/user/getbyunionid",
            params={"access_token": token},
            json={"unionid": union_id},
        )
        data = resp.json()
    if data.get("errcode") not in (0, None):
        raise DingTalkError(
            f"getbyunionid失败: {data.get('errmsg') or data.get('errcode')}",
            status_code=401,
        )
    userid = (data.get("result") or {}).get("userid")
    if not userid:
        raise DingTalkError("未能通过 unionId 解析企业 userid", status_code=401)
    return str(userid)


async def resolve_userid_by_oauth(auth_code: str, redirect_uri: str) -> str:
    """PC 扫码：OAuth authCode → unionId → 企业 userid。禁止走 JSAPI getuserinfo。"""
    if not auth_code:
        raise DingTalkError("缺少钉钉授权码", status_code=400)
    if not settings.dingtalk_app_key or not settings.dingtalk_app_secret:
        raise DingTalkError("钉钉应用未配置", status_code=500)
    if not redirect_uri:
        raise DingTalkError("缺少 redirect_uri", status_code=400)

    user_token, union_id = await _oauth_user_access_token(auth_code, redirect_uri)
    if not union_id:
        union_id = await _fetch_union_id(user_token)
    if not union_id:
        raise DingTalkError(
            "OAuth 换票成功但未拿到 unionId，请检查应用 openid/unionid 权限",
            status_code=401,
        )
    return await _userid_by_union_id(union_id)


async def get_user_profile(userid: str) -> dict:
    """用工号当门牌：只取姓名/工号/岗位，不把手机号带进登录。"""
    ding_userid = (userid or "").strip()
    if not ding_userid:
        raise DingTalkError("缺少钉钉用户", status_code=400)
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/v2/user/get",
            params={"access_token": token},
            json={"userid": ding_userid, "language": "zh_CN"},
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise DingTalkError("钉钉返回无法解析") from exc
    result = _check_oapi(data, "查询钉钉用户")
    return {
        "userid": str(result.get("userid") or ding_userid).strip(),
        "name": str(result.get("name") or "").strip(),
        "job_number": str(result.get("job_number") or "").strip(),
        "title": str(result.get("title") or "").strip(),
    }


async def send_work_notice(userid: str, title: str, content: str) -> None:
    """
    发送钉钉工作通知（骨架）。
    正式环境需在开放平台开通「工作通知」并配置 agent_id；
    未配置时抛出明确错误，由提醒服务记入 errors。
    """
    agent_id = getattr(settings, "dingtalk_agent_id", "") or ""
    if not agent_id:
        raise DingTalkError(
            "未配置 DINGTALK_AGENT_ID，工作通知骨架仅完成扫描未实发",
            status_code=501,
        )
    if not userid:
        raise DingTalkError("缺少钉钉 userid", status_code=400)
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2",
            params={"access_token": token},
            json={
                "agent_id": agent_id,
                "userid_list": userid,
                "msg": {
                    "msgtype": "text",
                    "text": {"content": f"【{title}】{content}"},
                },
            },
        )
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise DingTalkError(data.get("errmsg") or "工作通知发送失败")


def _check_oapi(data: dict, action: str) -> dict:
    if data.get("errcode") not in (0, None):
        raise DingTalkError(
            f"{action}失败: {data.get('errmsg') or data.get('errcode')}",
            status_code=502,
        )
    return data.get("result") or {}


async def list_sub_departments(dept_id: int = 1) -> list[dict]:
    """列出某部门的直接子部门。根部门一般为 1。"""
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/v2/department/listsub",
            params={"access_token": token},
            json={"dept_id": dept_id},
        )
        data = resp.json()
    if data.get("errcode") not in (0, None):
        raise DingTalkError(
            f"获取子部门失败: {data.get('errmsg') or data.get('errcode')}",
            status_code=502,
        )
    # v2 listsub 的 result 可能是 list，也可能包在 result 里
    result = data.get("result")
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and isinstance(result.get("list"), list):
        return result["list"]
    return []


async def list_all_departments() -> list[dict]:
    """广度优先遍历整棵部门树（含根下所有层级）。"""
    found: list[dict] = []
    queue: list[int] = [1]
    seen: set[int] = set()
    while queue:
        parent = queue.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        children = await list_sub_departments(parent)
        for dep in children:
            dep_id = int(dep.get("dept_id") or dep.get("id") or 0)
            if not dep_id or dep_id in seen:
                continue
            found.append(dep)
            queue.append(dep_id)
    return found


async def find_department_by_name(name: str) -> dict:
    """按部门名称精确匹配；找不到则报错。"""
    target = (name or "").strip()
    if not target:
        raise DingTalkError("部门名称不能为空", status_code=400)
    depts = await list_all_departments()
    for dep in depts:
        if (dep.get("name") or "").strip() == target:
            return dep
    raise DingTalkError(f"未找到部门「{target}」，请确认钉钉通讯录名称", status_code=404)


async def list_department_user_page(dept_id: int, cursor: int = 0, size: int = 100) -> tuple[list[dict], int | None]:
    """分页拉取部门直属成员。返回 (members, next_cursor|None)。"""
    token = await get_access_token()
    async with _client() as client:
        resp = await client.post(
            "https://oapi.dingtalk.com/topapi/v2/user/list",
            params={"access_token": token},
            json={"dept_id": dept_id, "cursor": cursor, "size": size},
        )
        data = resp.json()
    result = _check_oapi(data, "拉取部门成员")
    members = result.get("list") or []
    next_cursor = result.get("next_cursor")
    has_more = bool(result.get("has_more"))
    if not has_more:
        return members, None
    try:
        return members, int(next_cursor) if next_cursor is not None else None
    except (TypeError, ValueError):
        return members, None


async def list_department_users(dept_id: int, *, include_children: bool = True) -> list[dict]:
    """
    拉取部门成员（姓名/工号/手机/userid）。
    include_children=True 时包含所有下级部门成员；按 userid 去重。
    """
    dept_ids = [dept_id]
    if include_children:
        # 从该部门起再扫子树
        queue = [dept_id]
        seen = {dept_id}
        while queue:
            parent = queue.pop(0)
            for dep in await list_sub_departments(parent):
                child_id = int(dep.get("dept_id") or dep.get("id") or 0)
                if child_id and child_id not in seen:
                    seen.add(child_id)
                    dept_ids.append(child_id)
                    queue.append(child_id)

    by_userid: dict[str, dict] = {}
    for did in dept_ids:
        cursor: int | None = 0
        while cursor is not None:
            page, cursor = await list_department_user_page(did, cursor=cursor or 0)
            for raw in page:
                userid = str(raw.get("userid") or "").strip()
                if not userid:
                    continue
                by_userid[userid] = {
                    "userid": userid,
                    "name": str(raw.get("name") or "").strip(),
                    "job_number": str(raw.get("job_number") or "").strip(),
                    "mobile": str(raw.get("mobile") or "").strip(),
                    "title": str(raw.get("title") or "").strip(),
                }
    return list(by_userid.values())
