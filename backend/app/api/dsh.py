from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import json
import secrets
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.security import ALGORITHM
from app.models import Role, User
from app.rbac.deps import get_current_user, get_user_permissions, get_user_role_codes, require_permission
from app.services import assist_analyze
from app.services import agent_memory
from app.services import agent_native
from app.services.agent_skills import skill_pack_for
from app.services.service_auth import (
    SERVICE_DSH,
    SERVICE_WORKBENCH,
    build_signed_headers,
    parse_allowlist,
    verify_signed_request,
)

router = APIRouter(prefix="/dsh", tags=["智能助手"])


def _dsh_role(user: User) -> str:
    if get_user_role_codes(user) & {"admin", "dept_head"}:
        return "admin"
    return "member"


def _origin_from(raw: str | None, *, allow_http: bool = False) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text if "://" in text else f"https://{text}")
    except Exception:
        return ""
    host = (parsed.netloc or "").strip()
    if not host or "/" in host or " " in host or "@" in host:
        return ""
    if parsed.scheme == "https":
        return f"https://{host}"
    if allow_http and parsed.scheme == "http":
        return f"http://{host}"
    return ""


def embed_origin(request: Request) -> str:
    """嵌页面源：现网必须 https；142 内网 http 也可，否则打不开助手。"""
    allow_http = bool(settings.agent_native)
    for raw in (
        request.headers.get("origin"),
        request.headers.get("referer"),
    ):
        origin = _origin_from(raw, allow_http=allow_http)
        if origin:
            return origin
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if host and "/" not in host:
        if proto == "https":
            return f"https://{host}"
        if allow_http and proto in ("http", ""):
            return f"http://{host}"
    for raw in (
        settings.dingtalk_oauth_public_origin,
        settings.dsh_embed_origin,
    ):
        origin = _origin_from(raw, allow_http=allow_http)
        if origin:
            return origin
    raise HTTPException(status_code=503, detail="智能助手只能从加密后的工作台打开")


def _request_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-real-ip") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    return request.client.host if request.client else ""


def _require_dsh_signature(request: Request, body: str) -> None:
    ok, _reason = verify_signed_request(
        secret=(settings.dsh_to_wb_secret or "").strip(),
        expected_service_id=SERVICE_DSH,
        method=request.method,
        path=request.url.path,
        body=body,
        headers=request.headers,
        ip=_request_ip(request),
        allowlist=parse_allowlist(settings.dsh_service_allowlist),
    )
    if not ok:
        raise HTTPException(status_code=401, detail="机器签名没通过")


def try_ensure_dsh_home(username: str) -> None:
    """登录后预建磁盘屋子；门禁没起来也不挡登录。"""
    name = (username or "").strip()
    if not name:
        return
    try:
        signed_dsh_post("/internal/homes/ensure", {"username": name}, timeout=8)
    except Exception:
        return


def signed_dsh_post(path: str, payload: dict, *, timeout: float = 15) -> dict:
    """工作台 → 助手门禁的机器签名 POST。"""
    secret = (settings.wb_to_dsh_secret or "").strip()
    base = (settings.dsh_internal_url or "").rstrip("/")
    if not secret or not base:
        raise HTTPException(status_code=503, detail="智能助手还没接通，请联系管理员")
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(secret, SERVICE_WORKBENCH, "POST", path, body)
    request = urllib.request.Request(
        f"{base}{path}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if settings.agent_native and exc.code in {404, 405}:
            raise HTTPException(
                status_code=503,
                detail="本机助手门禁还没装好，对话暂时进不去。请先用左侧账本，或用账号密码进工作台。",
            ) from exc
        raise HTTPException(status_code=503, detail="智能助手还没准备好，请稍后再试") from exc
    except OSError as exc:
        raise HTTPException(status_code=503, detail="智能助手还没准备好，请稍后再试") from exc


def _signed_post(path: str, payload: dict) -> dict:
    return signed_dsh_post(path, payload, timeout=8)


@router.post("/ticket")
async def issue_dsh_ticket(
    request: Request,
    user: User = Depends(require_permission("dsh.access")),
) -> dict:
    origin = embed_origin(request)
    secret = (settings.dsh_sso_secret or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="智能助手还没接通，请联系管理员")
    ticket = jwt.encode(
        {
            "typ": "dsh_sso",
            "username": user.username,
            "name": user.name,
            "role": _dsh_role(user),
            "wb_user_id": user.id,
            "jti": secrets.token_hex(16),
            "exp": datetime.now(timezone.utc) + timedelta(seconds=90),
        },
        secret,
        algorithm=ALGORITHM,
    )
    exchanged = _signed_post("/internal/sso/exchange", {"ticket": ticket})
    code = str(exchanged.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=503, detail="智能助手还没准备好，请稍后再试")
    return {
        "embed_url": f"{origin}/sso?code={code}",
        "skill_ids": skill_pack_for(user)["skill_ids"],
        "wb_user_id": user.id,
    }


@router.post("/session-end")
async def end_dsh_session(
    user: User = Depends(get_current_user),
) -> dict:
    try:
        _signed_post("/internal/logout", {"username": user.username, "wb_user_id": user.id})
    except HTTPException:
        if settings.agent_native:
            return {"ok": True}
        raise
    return {"ok": True}


@router.post("/introspect")
async def introspect_dsh_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    raw = (await request.body()).decode("utf-8")
    _require_dsh_signature(request, raw)
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    try:
        user_id = int(payload.get("wb_user_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="账号不对") from exc
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        return {"active": False, "allowed": False, "role": "member"}
    perms = get_user_permissions(user)
    allowed = "dsh.access" in perms
    can_analyze = "assist.analyze" in perms
    return {
        "active": True,
        "allowed": allowed,
        "can_analyze": can_analyze,
        "role": _dsh_role(user) if allowed else "member",
    }


async def _load_signed_user(db: AsyncSession, request: Request) -> tuple[User, dict]:
    raw = (await request.body()).decode("utf-8")
    _require_dsh_signature(request, raw)
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    try:
        user_id = int(payload.get("wb_user_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="账号不对") from exc
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="账号不存在")
    perms = get_user_permissions(user)
    if "assist.analyze" not in perms:
        raise HTTPException(status_code=403, detail="无分析权限")
    return user, payload


@router.post("/context/team")
async def dsh_context_team(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """助手门禁拉团队分析上下文（需 assist.analyze）。"""
    user, _payload = await _load_signed_user(db, request)
    data = await assist_analyze.analyze_team(db, user, get_user_permissions(user))
    return {"ok": True, "kind": "team", "data": data}


@router.post("/context/project")
async def dsh_context_project(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """助手门禁拉项目分析上下文。"""
    user, payload = await _load_signed_user(db, request)
    try:
        project_id = int(payload.get("project_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="缺少项目") from exc
    data = await assist_analyze.analyze_project(
        db, user, get_user_permissions(user), project_id
    )
    if data.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"ok": True, "kind": "project", "data": data}


@router.post("/context/person")
async def dsh_context_person(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """助手门禁拉人员分析上下文。"""
    user, payload = await _load_signed_user(db, request)
    try:
        target_user_id = int(payload.get("target_user_id") or payload.get("user_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="缺少人员") from exc
    data = await assist_analyze.analyze_person(
        db, user, get_user_permissions(user), target_user_id
    )
    if data.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"ok": True, "kind": "person", "data": data}


async def _load_tool_user(db: AsyncSession, request: Request) -> tuple[User, dict]:
    """工具门禁：只要助手权限，必须是本人且账号启用。"""
    raw = (await request.body()).decode("utf-8")
    _require_dsh_signature(request, raw)
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="body is not JSON") from exc
    try:
        user_id = int(payload.get("wb_user_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="账号不对") from exc
    result = await db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == user_id, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="账号不存在")
    if "dsh.access" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无助手权限")
    return user, payload


@router.post("/tools/my-todos")
async def tool_my_todos(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _payload = await _load_tool_user(db, request)
    items = await agent_native.my_todo_briefs(db, user)
    return {"ok": True, "items": items}


@router.post("/tools/my-week-report")
async def tool_my_week_report(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _payload = await _load_tool_user(db, request)
    data = await agent_native.my_week_report(db, user)
    return {"ok": True, **data}


@router.post("/tools/my-projects")
async def tool_my_projects(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _payload = await _load_tool_user(db, request)
    items = await agent_native.list_visible_projects(db, user)
    return {"ok": True, "items": items}


@router.post("/tools/search-cases")
async def tool_search_cases(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    if "solution.view" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无案例库权限")
    query = str(payload.get("text") or payload.get("query") or "").strip()
    data = await agent_native.search_cases(db, query)
    return {"ok": True, **data}


@router.post("/tools/skill-pack")
async def tool_skill_pack(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _payload = await _load_tool_user(db, request)
    pack = skill_pack_for(user)
    return {"ok": True, **pack}


@router.post("/tools/rank-todos")
async def tool_rank_todos(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _payload = await _load_tool_user(db, request)
    items = await agent_native.ranked_todos(db, user)
    return {"ok": True, "items": items}


@router.post("/tools/prefs")
async def tool_prefs(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    row = await agent_native.upsert_prefs(db, user, payload)
    return {"ok": True, **agent_native.prefs_out(row, user.id)}


@router.post("/tools/nudge")
async def tool_nudge(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    try:
        task_id = int(payload.get("task_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="缺少任务") from exc
    event = str(payload.get("event") or "assigned")
    now_raw = payload.get("now_time")
    now_time = agent_native.parse_time(str(now_raw) if now_raw else None)
    result = await agent_native.nudge(
        db, user, event=event, task_id=task_id, now_time=now_time
    )
    return {"ok": True, **result}


@router.post("/tools/draft-reassign")
async def tool_draft_reassign(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    try:
        task_id = int(payload.get("task_id"))
        new_assignee_id = int(payload.get("new_assignee_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="缺少改派对象") from exc
    draft = await agent_native.draft_reassign(
        db,
        user,
        task_id=task_id,
        new_assignee_id=new_assignee_id,
        reason=str(payload.get("reason") or ""),
    )
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/draft-week-report")
async def tool_draft_week_report(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    if "report.submit" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无权写周报")
    draft = await agent_native.draft_week_report(db, user, payload)
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/draft-task")
async def tool_draft_task(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    if "task.create.self" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无权建任务")
    draft = await agent_native.draft_task(db, user, payload)
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/draft-todo")
async def tool_draft_todo(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """起草本人待办（默认「日常任务」）；确认后出现在 my-todos。"""
    user, payload = await _load_tool_user(db, request)
    if "task.create.self" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无权建待办")
    draft = await agent_native.draft_todo(db, user, payload)
    return {
        "ok": True,
        "draft_id": draft.id,
        "status": draft.status,
        "kind": draft.kind,
        "item_count": len((draft.payload or {}).get("items") or []),
    }


@router.post("/tools/draft-project")
async def tool_draft_project(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    if "project.create" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无权建项目")
    draft = await agent_native.draft_project(db, user, payload)
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/draft-case")
async def tool_draft_case(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    if "solution.upload" not in get_user_permissions(user):
        raise HTTPException(status_code=403, detail="无权写案例")
    draft = await agent_native.draft_case(db, user, payload)
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/draft-blocker")
async def tool_draft_blocker(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    draft = await agent_native.draft_blocker(db, user, payload)
    return {"ok": True, "draft_id": draft.id, "status": draft.status, "kind": draft.kind}


@router.post("/tools/confirm-draft")
async def tool_confirm_draft(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    try:
        draft_id = int(payload.get("draft_id"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="缺少草稿") from exc
    result = await agent_native.confirm_draft(db, user, draft_id)
    return result


@router.post("/tools/memory-get")
async def tool_memory_get(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    limit = payload.get("limit")
    try:
        note_limit = int(limit) if limit is not None else 20
    except (TypeError, ValueError):
        note_limit = 20
    data = await agent_memory.memory_get(db, user, note_limit=note_limit)
    return {"ok": True, **data}


@router.post("/tools/memory-search")
async def tool_memory_search(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    query = str(payload.get("query") or payload.get("text") or "").strip()
    try:
        limit = int(payload.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    return await agent_memory.memory_search(db, user, query=query, limit=limit)


@router.post("/tools/memory-upsert")
async def tool_memory_upsert(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, payload = await _load_tool_user(db, request)
    return await agent_memory.memory_upsert(db, user, payload)


@router.post("/tools/memory-ingest-session")
async def tool_memory_ingest_session(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """写入一段会话摘录到本人记忆索引（不是账本）。"""
    user, payload = await _load_tool_user(db, request)
    return await agent_memory.ingest_session_excerpt(
        db,
        user,
        session_id=str(payload.get("session_id") or ""),
        text=str(payload.get("text") or ""),
    )

