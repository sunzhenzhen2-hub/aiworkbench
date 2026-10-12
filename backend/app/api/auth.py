from __future__ import annotations

import asyncio
import json
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models import Role, User
from app.rbac.deps import get_current_user, get_user_permissions, to_user_out
from app.schemas import (
    ChangePasswordRequest,
    DingTalkBindRequest,
    DingTalkConfigOut,
    DingTalkLoginRequest,
    DingTalkLoginResponse,
    DingTalkScanRequest,
    LoginRequest,
    TokenResponse,
    UserOut,
)
from app.schemas.password import password_policy_response
from app.api.dsh import try_ensure_dsh_home
from app.services import dingtalk as dingtalk_service
from app.services.dingtalk import DingTalkError
from app.services import dingtalk_jit
from app.services import oauth_state as oauth_state_service
from app.services.rate_limit import dingtalk_config_limiter, dingtalk_scan_limiter

router = APIRouter(prefix="/auth", tags=["认证"])

BIND_TICKET_MINUTES = 10
NEED_BIND_MSG = "请使用系统账号绑定钉钉"
UNAUTHORIZED_MSG = "请联系管理员在电脑端「系统管理」中开通钉钉免登"
CALLBACK_PATH = "/aiworkbench/api/auth/dingtalk-callback?embed=1"
DEFAULT_SDK = "https://g.alicdn.com/dingding/h5-dingtalk-login/0.30.1/ddlogin.js"
ALLOWED_SDK_PREFIXES = ("https://g.alicdn.com/",)
SCAN_RESULT_TTL_SEC = 120

# 同一 auth_code+state 短时复用；缓存条目绑定 state，防仅凭 auth_code 偷票
_scan_result_cache: dict[str, tuple[float, str, DingTalkLoginResponse]] = {}
# 已消费的 bind_ticket jti（一次性）
_consumed_bind_jtis: set[str] = set()

DINGTALK_CALLBACK_HTML = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>钉钉登录</title></head>
<body><p>授权完成，请返回登录页…</p>
<script>
(function(){
  var p = new URLSearchParams(window.location.search);
  var authCode = p.get('authCode') || p.get('code') || '';
  var state = p.get('state') || '';
  if (!authCode) return;
  var payload = { type: 'dingtalk-oauth', authCode: authCode, state: state };
  // VPN 下登录页与回调页可能不同源，用 '*' 投递；鉴权靠 state + 后端兑码
  try { if (window.opener) window.opener.postMessage(payload, '*'); } catch (e) {}
  try { if (window.parent && window.parent !== window) window.parent.postMessage(payload, '*'); } catch (e) {}
})();
</script></body></html>"""

DINGTALK_CALLBACK_PLACEHOLDER = """<!DOCTYPE html><html><head><meta charset="UTF-8"><title>钉钉回调</title></head>
<body><p>dingtalk-callback</p></body></html>"""


# #region agent log
_DEBUG_LOG = Path("/home/aiworkbench/.cursor/debug-fd7d89.log")


def _dbg(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": "fd7d89",
            "runId": "post-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


# #endregion


def _issue_token(user: User) -> DingTalkLoginResponse:
    token = create_access_token({"sub": str(user.id)})
    return DingTalkLoginResponse(
        status="ok",
        access_token=token,
        must_change_password=user.must_change_password,
    )


def _make_bind_ticket(dingtalk_user_id: str) -> str:
    jti = secrets.token_hex(16)
    return create_access_token(
        {"typ": "dt_bind", "dingtalk_user_id": dingtalk_user_id, "jti": jti},
        expires_delta=timedelta(minutes=BIND_TICKET_MINUTES),
    )


def _read_bind_ticket(ticket: str) -> tuple[str, str]:
    payload = decode_access_token(ticket)
    if not payload or payload.get("typ") != "dt_bind" or not payload.get("dingtalk_user_id"):
        raise HTTPException(status_code=400, detail="绑定已过期，请重新打开应用")
    jti = str(payload.get("jti") or "")
    if not jti:
        raise HTTPException(status_code=400, detail="绑定已过期，请重新扫码")
    if jti in _consumed_bind_jtis:
        raise HTTPException(status_code=400, detail="绑定票据已使用，请重新扫码")
    return str(payload["dingtalk_user_id"]), jti


async def _load_user_by_dingtalk(db: AsyncSession, dingtalk_user_id: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.dingtalk_user_id == dingtalk_user_id)
    )
    return result.scalar_one_or_none()


async def _load_user_by_username(db: AsyncSession, username: str) -> User | None:
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.username == username)
    )
    return result.scalar_one_or_none()


def _ensure_dingtalk_login(user: User) -> None:
    if "dingtalk.login" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail=UNAUTHORIZED_MSG)


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _parse_origin(raw: str | None) -> str | None:
    """从 Origin / Referer / 配置串提取 scheme://host[:port]，非法则 None。"""
    text = (raw or "").strip()
    if not text:
        return None
    # Referer 是完整 URL；Origin / 配置可能已是 origin
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.netloc or "").strip()
    if not host or "/" in host or " " in host or "@" in host:
        return None
    return f"{parsed.scheme}://{host}"


def _build_redirect_uri(request: Request) -> str:
    """拼 OAuth redirect_uri。

    VPN 场景下 nginx 的 Host/$scheme 常是内网 http://IP，与浏览器 https 域名不一致，
    会导致 HTTPS 页无法跳转到 HTTP 回调（混合内容）从而「扫码无跳转」。
    优先：配置公开源 → 浏览器 Origin/Referer → Host + X-Forwarded-Proto。
    仍不信任 X-Forwarded-Host。
    """
    configured = _parse_origin(settings.dingtalk_oauth_public_origin)
    if configured:
        return f"{configured}{CALLBACK_PATH}"

    for header in ("origin", "referer"):
        from_browser = _parse_origin(request.headers.get(header))
        if from_browser:
            return f"{from_browser}{CALLBACK_PATH}"

    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = "http"
    host = (request.headers.get("host") or request.url.netloc or "").split(",")[0].strip()
    if not host or "/" in host or " " in host:
        raise HTTPException(status_code=400, detail="非法 Host")
    return f"{proto}://{host}{CALLBACK_PATH}"


def _safe_sdk_url(url: str | None) -> str:
    candidate = (url or "").strip() or DEFAULT_SDK
    if any(candidate.startswith(p) for p in ALLOWED_SDK_PREFIXES):
        return candidate
    return DEFAULT_SDK


def _get_cached_scan(auth_code: str, state: str) -> DingTalkLoginResponse | None:
    hit = _scan_result_cache.get(auth_code)
    if not hit:
        return None
    until, cached_state, payload = hit
    if until <= time.time():
        _scan_result_cache.pop(auth_code, None)
        return None
    if not state or state != cached_state:
        # #region agent log
        _dbg(
            "H1",
            "auth.py:_get_cached_scan",
            "cache_hit_rejected_state_mismatch",
            {"has_state": bool(state), "state_match": False},
        )
        # #endregion
        return None
    # #region agent log
    _dbg("H1", "auth.py:_get_cached_scan", "cache_hit_allowed", {"state_match": True})
    # #endregion
    return payload


def _cache_scan(auth_code: str, state: str, payload: DingTalkLoginResponse) -> None:
    _scan_result_cache[auth_code] = (time.time() + SCAN_RESULT_TTL_SEC, state, payload)
    now = time.time()
    expired = [k for k, (u, _, _) in _scan_result_cache.items() if u <= now]
    for k in expired:
        _scan_result_cache.pop(k, None)


def _finish_dingtalk_login(user: User) -> DingTalkLoginResponse:
    try_ensure_dsh_home(user.username)
    return _issue_token(user)


async def _login_by_dingtalk_userid(db: AsyncSession, dingtalk_user_id: str) -> DingTalkLoginResponse:
    try:
        profile = await dingtalk_service.get_user_profile(dingtalk_user_id)
    except DingTalkError as exc:
        detail = (
            "连不上钉钉，请稍后刷新二维码重试"
            if exc.status_code == 502
            else "未能读取钉钉工号，请联系管理员"
        )
        raise HTTPException(status_code=exc.status_code, detail=detail) from exc
    job_number = str(profile.get("job_number") or "")
    await dingtalk_jit.release_system_dingtalk_binding(
        db,
        dingtalk_user_id=dingtalk_user_id,
        job_number=job_number,
    )
    user = await dingtalk_jit.provision_user_by_job(
        db,
        dingtalk_user_id=dingtalk_user_id,
        job_number=job_number,
        name=str(profile.get("name") or ""),
        title=str(profile.get("title") or ""),
    )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")
    _ensure_dingtalk_login(user)
    if user.must_change_password:
        user.must_change_password = False
        await db.commit()
        await db.refresh(user)
    return _finish_dingtalk_login(user)


@router.get("/password-policy")
async def get_password_policy() -> dict:
    return password_policy_response()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")
    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token,
        must_change_password=user.must_change_password,
    )


@router.get("/dingtalk-config", response_model=DingTalkConfigOut)
async def dingtalk_config(request: Request, db: AsyncSession = Depends(get_db)) -> DingTalkConfigOut:
    if not dingtalk_config_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    if not settings.dingtalk_app_key:
        raise HTTPException(status_code=400, detail="钉钉应用未配置 AppKey")
    state = secrets.token_hex(16)
    redirect_uri = _build_redirect_uri(request)
    # #region agent log
    _dbg(
        "H2",
        "auth.py:dingtalk_config",
        "redirect_uri_built",
        {
            "host_header": (request.headers.get("host") or "")[:80],
            "xff_host": (request.headers.get("x-forwarded-host") or "")[:80],
            "redirect_host": redirect_uri.split("/")[2] if "://" in redirect_uri else "",
        },
    )
    # #endregion
    try:
        await oauth_state_service.save_oauth_state(db, state, redirect_uri)
        await oauth_state_service.purge_expired_oauth_states(db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="登录状态初始化失败") from exc
    return DingTalkConfigOut(
        client_id=settings.dingtalk_app_key,
        corp_id=settings.dingtalk_corp_id or "",
        redirect_uri=redirect_uri,
        state=state,
        sdk_url=_safe_sdk_url(settings.dingtalk_login_sdk_url),
    )


@router.get("/dingtalk-callback", response_class=HTMLResponse)
async def dingtalk_callback(
    authCode: str | None = None,
    code: str | None = None,
    embed: str | None = None,
) -> HTMLResponse:
    auth_code = authCode or code or ""
    is_embed = str(embed or "") == "1"
    html = DINGTALK_CALLBACK_HTML if (auth_code and is_embed) else DINGTALK_CALLBACK_PLACEHOLDER
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@router.post("/dingtalk-scan", response_model=DingTalkLoginResponse)
async def dingtalk_scan(
    body: DingTalkScanRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DingTalkLoginResponse:
    if not dingtalk_scan_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    auth_code = (body.auth_code or "").strip()
    state = (body.state or "").strip()
    if not auth_code:
        raise HTTPException(status_code=400, detail="缺少 auth_code")
    if not state:
        raise HTTPException(status_code=400, detail="缺少 state，请刷新二维码")

    cached = _get_cached_scan(auth_code, state)
    if cached:
        return cached

    redirect_uri = await oauth_state_service.validate_oauth_state(db, state)
    if not redirect_uri:
        raise HTTPException(status_code=400, detail="无效或过期的 state，请刷新二维码")

    try:
        dingtalk_user_id = await asyncio.wait_for(
            dingtalk_service.resolve_userid_by_oauth(auth_code, redirect_uri),
            timeout=8,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=502, detail="连不上钉钉，请稍后刷新二维码重试") from exc
    except DingTalkError as exc:
        cached_after = _get_cached_scan(auth_code, state)
        if cached_after:
            return cached_after
        # 不把钉钉原始细节原样放大给前端
        raise HTTPException(status_code=exc.status_code, detail="钉钉授权失败，请刷新二维码重试") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="连不上钉钉，请稍后刷新二维码重试") from exc

    result = await _login_by_dingtalk_userid(db, dingtalk_user_id)
    await oauth_state_service.revoke_oauth_state(db, state)
    _cache_scan(auth_code, state, result)
    return result


@router.post("/dingtalk", response_model=DingTalkLoginResponse)
async def dingtalk_login(body: DingTalkLoginRequest, db: AsyncSession = Depends(get_db)) -> DingTalkLoginResponse:
    try:
        dingtalk_user_id = await dingtalk_service.resolve_userid(body.auth_code)
    except DingTalkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="连不上钉钉，请稍后刷新二维码重试") from exc

    return await _login_by_dingtalk_userid(db, dingtalk_user_id)


@router.post("/dingtalk/bind", response_model=DingTalkLoginResponse)
async def dingtalk_bind(body: DingTalkBindRequest, db: AsyncSession = Depends(get_db)) -> DingTalkLoginResponse:
    dingtalk_user_id, jti = _read_bind_ticket(body.bind_ticket)
    user = await _load_user_by_username(db, body.username)
    if not user or not user.hashed_password or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已禁用")
    _ensure_dingtalk_login(user)
    taken = await _load_user_by_dingtalk(db, dingtalk_user_id)
    if taken and taken.id != user.id:
        raise HTTPException(status_code=400, detail="该钉钉账号已绑定其他用户")
    if user.dingtalk_user_id and user.dingtalk_user_id != dingtalk_user_id:
        raise HTTPException(status_code=400, detail="该系统账号已绑定其他钉钉用户")
    user.dingtalk_user_id = dingtalk_user_id
    # 完成钉钉绑定后走免登，同样不强制改密
    user.must_change_password = False
    await db.commit()
    _consumed_bind_jtis.add(jti)
    # 防止集合无限增长：超过 5000 时清空（票据本身有 TTL）
    if len(_consumed_bind_jtis) > 5000:
        _consumed_bind_jtis.clear()
    return _issue_token(user)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not user.hashed_password or not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    if body.old_password == body.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与原密码相同")
    user.hashed_password = hash_password(body.new_password)
    user.must_change_password = False
    await db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return to_user_out(user)
