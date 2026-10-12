from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.dsh import signed_dsh_post
from app.core.database import get_db
from app.core.security import hash_password
from app.models import (
    AuditLog,
    LlmModel,
    NotificationDeliveryLog,
    NotificationTemplate,
    Permission,
    Role,
    User,
    UserRole,
)
from app.rbac.deps import require_permission, to_user_out
from app.schemas import (
    AdminDingTalkBindRequest,
    AdminResetPasswordRequest,
    DingTalkCandidateOut,
    DshUsageSession,
    DshUsageSessionsOut,
    DshUsageSummaryOut,
    DshUsageUserSummary,
    DshUsageVisit,
    DshUsageVisitsOut,
    LlmModelCreate,
    LlmModelDiscoverIn,
    LlmModelDiscoverOut,
    LlmModelOut,
    LlmModelTestOut,
    LlmModelUpdate,
    NotificationDeliveryLogListOut,
    NotificationDeliveryLogOut,
    NotificationTemplateOut,
    NotificationTemplateUpdate,
    PermissionOut,
    RoleOut,
    UserCreateRequest,
    UserOut,
)
from app.services.llm_client import list_openai_compatible_models, test_openai_compatible
from app.services.llm_resolver import clear_other_defaults, mask_api_key
from app.services.notify_pipeline import format_time_opt, parse_hhmm
from app.services.reminders import (
    send_blocker_overdue_notices,
    send_phase_overdue_notices,
    send_weekly_report_overdue_notices,
)
from app.services.user_sync import delete_user_account, sync_department_users, sync_team_allowlist
from app.services import dingtalk as dingtalk_svc
from app.services.dingtalk import DingTalkError
from app.services import embedding_client as emb_client
from app.services import embedding_settings as emb_settings


def _mask_mobile(mobile: str) -> str:
    m = (mobile or "").strip()
    if len(m) < 7:
        return ""
    return f"{m[:3]}****{m[-4:]}"


router = APIRouter(prefix="/admin", tags=["系统管理"])


class RolePermissionUpdate(BaseModel):
    permission_codes: list[str]


class UserRoleUpdate(BaseModel):
    role_codes: list[str]


class SyncDingTalkDeptRequest(BaseModel):
    department_name: str = "政企市场部"


class SyncDingTalkTeamRequest(BaseModel):
    department_name: str = "政企市场部"
    # 为空则使用服务端默认 TEAM_SYNC_NAMES
    allow_names: list[str] | None = None
    admin_name: str = "孙振振"


@router.get("/users", response_model=list[UserOut])
async def list_users(
    user: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> list[UserOut]:
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions))
    )
    users = result.scalars().all()
    return [to_user_out(u) for u in users]


@router.patch("/users/{user_id}/roles")
async def update_user_roles(
    user_id: int,
    body: UserRoleUpdate,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    roles_result = await db.execute(select(Role).where(Role.code.in_(body.role_codes)))
    target.roles = list(roles_result.scalars().all())
    db.add(AuditLog(user_id=admin.id, action="update_roles", resource="user", resource_id=user_id))
    await db.commit()
    return {"ok": True}


@router.post("/users", response_model=UserOut)
async def create_user(
    body: UserCreateRequest,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")
    user = User(
        username=body.username,
        name=body.name,
        email=body.email,
        hashed_password=hash_password(body.password),
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    if body.role_codes:
        roles_result = await db.execute(select(Role).where(Role.code.in_(body.role_codes)))
        for role in roles_result.scalars().all():
            db.add(UserRole(user_id=user.id, role_id=role.id))
    db.add(AuditLog(user_id=admin.id, action="create", resource="user", resource_id=user.id))
    await db.commit()
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == user.id)
    )
    user = result.scalar_one()
    return to_user_out(user)


@router.post("/users/sync-dingtalk-dept")
async def sync_users_from_dingtalk_dept(
    body: SyncDingTalkDeptRequest | None = None,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """保留 admin，删除其他用户，从钉钉指定部门导入（登录名=工号，初始密码=工号+手机号）。"""
    req = body or SyncDingTalkDeptRequest()
    try:
        return await sync_department_users(
            db, actor=admin, department_name=req.department_name or "政企市场部"
        )
    except DingTalkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/users/sync-dingtalk-team")
async def sync_users_from_dingtalk_team(
    body: SyncDingTalkTeamRequest | None = None,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """按姓名白名单同步团队；孙振振等指定人为管理员；删除白名单外账号。"""
    req = body or SyncDingTalkTeamRequest()
    try:
        kwargs: dict = {
            "actor": admin,
            "department_name": req.department_name or "政企市场部",
            "admin_name": req.admin_name or "孙振振",
        }
        if req.allow_names:
            kwargs["allow_names"] = req.allow_names
        return await sync_team_allowlist(db, **kwargs)
    except DingTalkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/dingtalk/candidates", response_model=list[DingTalkCandidateOut])
async def list_dingtalk_bind_candidates(
    for_user_id: int | None = Query(None, description="正在绑定的系统用户；其现有绑定仍可选"),
    department_name: str = Query("政企市场部"),
    unbound_only: bool = Query(
        False,
        description="为 true 时只返回尚未被其他人绑定的账号；默认返回全部并标注占用",
    ),
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> list[DingTalkCandidateOut]:
    """从钉钉部门通讯录拉取人员，供绑定下拉框选用。"""
    _ = admin
    try:
        dept = await dingtalk_svc.find_department_by_name(department_name)
        dept_id = int(dept.get("dept_id") or dept.get("id") or 0)
        if not dept_id:
            raise DingTalkError("部门 ID 无效", status_code=502)
        members = await dingtalk_svc.list_department_users(dept_id, include_children=True)
    except DingTalkError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    bound_rows = await db.execute(
        select(User.id, User.username, User.dingtalk_user_id).where(
            User.dingtalk_user_id.is_not(None)
        )
    )
    # userid -> (user_id, username)
    bound_map: dict[str, tuple[int, str]] = {}
    for uid, uname, dtid in bound_rows.all():
        if dtid:
            bound_map[str(dtid)] = (uid, uname)

    prefer_job = ""
    if for_user_id is not None:
        target = (
            await db.execute(select(User.username, User.name).where(User.id == for_user_id))
        ).one_or_none()
        if target:
            prefer_job = (target[0] or "").strip().upper()

    out: list[DingTalkCandidateOut] = []
    for m in members:
        userid = str(m.get("userid") or "").strip()
        if not userid:
            continue
        occupied = bound_map.get(userid)
        bound_username: str | None = None
        if occupied:
            occ_user_id, occ_username = occupied
            if for_user_id is not None and occ_user_id == for_user_id:
                bound_username = None  # 本人当前绑定，仍可选
            else:
                bound_username = occ_username
                if unbound_only:
                    continue
        out.append(
            DingTalkCandidateOut(
                userid=userid,
                name=str(m.get("name") or "").strip() or userid,
                job_number=str(m.get("job_number") or "").strip(),
                mobile_masked=_mask_mobile(str(m.get("mobile") or "")),
                bound_username=bound_username,
            )
        )

    def _sort_key(item: DingTalkCandidateOut) -> tuple:
        job = (item.job_number or "").strip().upper()
        # 工号与当前系统账号一致的排最前，未占用的优先，再按工号/姓名
        match = 0 if prefer_job and job == prefer_job else 1
        free = 0 if not item.bound_username else 1
        return (match, free, job or item.name or item.userid)

    out.sort(key=_sort_key)
    return out


@router.patch("/users/{user_id}/dingtalk", response_model=UserOut)
async def bind_user_dingtalk(
    user_id: int,
    body: AdminDingTalkBindRequest,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == user_id)
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    userid = (body.dingtalk_user_id or "").strip() or None
    if userid:
        taken = await db.execute(select(User).where(User.dingtalk_user_id == userid, User.id != user_id))
        if taken.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="该钉钉账号已绑定其他用户")
    target.dingtalk_user_id = userid
    db.add(AuditLog(user_id=admin.id, action="bind_dingtalk", resource="user", resource_id=user_id))
    await db.commit()
    await db.refresh(target)
    result = await db.execute(
        select(User).options(selectinload(User.roles).selectinload(Role.permissions)).where(User.id == target.id)
    )
    return to_user_out(result.scalar_one())


@router.patch("/users/{user_id}/password")
async def reset_user_password(
    user_id: int,
    body: AdminResetPasswordRequest,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在")
    target.hashed_password = hash_password(body.new_password)
    target.must_change_password = True
    db.add(AuditLog(user_id=admin.id, action="reset_password", resource="user", resource_id=user_id))
    await db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(require_permission("admin.user")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除账号：项目/任务等改挂 admin；禁止删 admin 与当前登录账号。"""
    try:
        username = await delete_user_account(db, user_id, actor=admin)
        await db.commit()
    except ValueError as exc:
        msg = str(exc)
        code = 404 if msg == "用户不存在" else 400
        raise HTTPException(status_code=code, detail=msg) from exc
    return {"ok": True, "username": username}


@router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    user: User = Depends(require_permission("admin.role")),
    db: AsyncSession = Depends(get_db),
) -> list[RoleOut]:
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).order_by(Role.id)
    )
    return [
        RoleOut(
            id=r.id,
            code=r.code,
            name=r.name,
            description=r.description,
            permissions=[p.code for p in r.permissions],
        )
        for r in result.scalars().all()
    ]


@router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    user: User = Depends(require_permission("admin.role")),
    db: AsyncSession = Depends(get_db),
) -> list[PermissionOut]:
    result = await db.execute(select(Permission).order_by(Permission.id))
    return [PermissionOut.model_validate(p) for p in result.scalars().all()]


@router.patch("/roles/{role_id}/permissions")
async def update_role_permissions(
    role_id: int,
    body: RolePermissionUpdate,
    admin: User = Depends(require_permission("admin.role")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(Role).options(selectinload(Role.permissions)).where(Role.id == role_id)
    )
    role = result.scalar_one_or_none()
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    perms_result = await db.execute(select(Permission).where(Permission.code.in_(body.permission_codes)))
    role.permissions = list(perms_result.scalars().all())
    db.add(AuditLog(user_id=admin.id, action="update_permissions", resource="role", resource_id=role_id))
    await db.commit()
    return {"ok": True}


@router.get("/audit-logs")
async def audit_logs(
    user: User = Depends(require_permission("admin.audit")),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource": log.resource,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "created_at": log.created_at.isoformat(),
        }
        for log in result.scalars().all()
    ]


def _llm_out(row: LlmModel) -> LlmModelOut:
    return LlmModelOut(
        id=row.id,
        name=row.name,
        provider=row.provider,
        api_base=row.api_base,
        api_key_masked=mask_api_key(row.api_key),
        model_name=row.model_name,
        purpose=row.purpose,
        is_enabled=row.is_enabled,
        is_default=row.is_default,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/models", response_model=list[LlmModelOut])
async def list_models(
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> list[LlmModelOut]:
    result = await db.execute(select(LlmModel).order_by(LlmModel.sort_order, LlmModel.id))
    return [_llm_out(r) for r in result.scalars().all()]


@router.post("/models/discover", response_model=LlmModelDiscoverOut)
async def discover_models(
    body: LlmModelDiscoverIn,
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> LlmModelDiscoverOut:
    """用 Base + Key 拉取远端 OpenAI 兼容模型列表（编辑时可传 model_id 复用已存密钥）。"""
    _ = user
    api_base = (body.api_base or "").strip()
    api_key = (body.api_key or "").strip()
    if not api_key and body.model_id is not None:
        result = await db.execute(select(LlmModel).where(LlmModel.id == body.model_id))
        row = result.scalar_one_or_none()
        if not row:
            return LlmModelDiscoverOut(ok=False, message="模型配置不存在")
        api_key = (row.api_key or "").strip()
        if not api_base:
            api_base = row.api_base or ""
    ok, models, message = await list_openai_compatible_models(api_base, api_key)
    return LlmModelDiscoverOut(ok=ok, models=models, message=message)


@router.post("/models", response_model=LlmModelOut)
async def create_model(
    body: LlmModelCreate,
    admin: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> LlmModelOut:
    if not body.api_base.strip() or not body.model_name.strip() or not body.name.strip():
        raise HTTPException(status_code=400, detail="名称、API Base、模型名不能为空")
    row = LlmModel(
        name=body.name.strip(),
        provider=body.provider or "openai_compatible",
        api_base=body.api_base.strip().rstrip("/"),
        api_key=body.api_key.strip(),
        model_name=body.model_name.strip(),
        purpose=body.purpose or "report_coach",
        is_enabled=body.is_enabled,
        is_default=body.is_default,
        sort_order=body.sort_order,
    )
    db.add(row)
    await db.flush()
    if row.is_default:
        await clear_other_defaults(db, row.purpose, row.id)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="create",
            resource="llm_model",
            resource_id=row.id,
            detail=f"name={row.name};model={row.model_name}",
        )
    )
    await db.commit()
    await db.refresh(row)
    return _llm_out(row)


@router.patch("/models/{model_id}", response_model=LlmModelOut)
async def update_model(
    model_id: int,
    body: LlmModelUpdate,
    admin: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> LlmModelOut:
    result = await db.execute(select(LlmModel).where(LlmModel.id == model_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    data = body.model_dump(exclude_unset=True)
    if "api_key" in data:
        key = data.pop("api_key")
        if key is not None and str(key).strip() != "":
            row.api_key = str(key).strip()
    for field, value in data.items():
        if field in ("api_base", "name", "model_name") and isinstance(value, str):
            value = value.strip()
            if field == "api_base":
                value = value.rstrip("/")
        setattr(row, field, value)
    if row.is_default:
        await clear_other_defaults(db, row.purpose, row.id)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="update",
            resource="llm_model",
            resource_id=row.id,
            detail=f"name={row.name};model={row.model_name}",
        )
    )
    await db.commit()
    await db.refresh(row)
    return _llm_out(row)


@router.delete("/models/{model_id}")
async def delete_model(
    model_id: int,
    admin: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(select(LlmModel).where(LlmModel.id == model_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    await db.delete(row)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="delete",
            resource="llm_model",
            resource_id=model_id,
            detail=f"name={row.name}",
        )
    )
    await db.commit()
    return {"ok": True}


@router.post("/models/{model_id}/test", response_model=LlmModelTestOut)
async def test_model(
    model_id: int,
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> LlmModelTestOut:
    result = await db.execute(select(LlmModel).where(LlmModel.id == model_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="模型不存在")
    ok, message = await test_openai_compatible(row.api_base, row.api_key, row.model_name)
    return LlmModelTestOut(ok=ok, message=message)


async def _username_display_map(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(User.username, User.name))
    return {str(username): str(name or "") for username, name in result.all()}


def _usage_payload(
    *,
    from_: str | None,
    to: str | None,
    username: str | None,
) -> dict:
    payload: dict = {}
    if from_:
        payload["from"] = from_
    if to:
        payload["to"] = to
    if username:
        payload["username"] = username.strip()
    return payload


@router.get("/dsh-usage/summary", response_model=DshUsageSummaryOut)
async def dsh_usage_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> DshUsageSummaryOut:
    raw = signed_dsh_post("/internal/usage/summary", _usage_payload(from_=from_, to=to, username=None))
    names = await _username_display_map(db)
    items = [
        DshUsageUserSummary(
            username=str(row.get("username") or ""),
            name=names.get(str(row.get("username") or "")) or None,
            open_count=int(row.get("open_count") or 0),
            last_open_at=row.get("last_open_at"),
            prompt_count=int(row.get("prompt_count") or 0),
            turns=int(row.get("turns") or 0),
            steps=int(row.get("steps") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cache_read_tokens=int(row.get("cache_read_tokens") or 0),
            cache_write_tokens=int(row.get("cache_write_tokens") or 0),
            session_count=int(row.get("session_count") or 0),
        )
        for row in (raw.get("items") or [])
        if row.get("username")
    ]
    return DshUsageSummaryOut.model_validate(
        {"from": raw.get("from"), "to": raw.get("to"), "items": items}
    )


@router.get("/dsh-usage/visits", response_model=DshUsageVisitsOut)
async def dsh_usage_visits(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    username: str | None = Query(None),
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> DshUsageVisitsOut:
    raw = signed_dsh_post(
        "/internal/usage/visits",
        _usage_payload(from_=from_, to=to, username=username),
    )
    names = await _username_display_map(db)
    items = [
        DshUsageVisit(
            username=str(row.get("username") or ""),
            name=names.get(str(row.get("username") or "")) or None,
            started_at=str(row.get("started_at") or ""),
            ended_at=row.get("ended_at"),
            duration_sec=row.get("duration_sec"),
            source=str(row.get("source") or ""),
            ip=str(row.get("ip") or ""),
        )
        for row in (raw.get("items") or [])
        if row.get("username") and row.get("started_at")
    ]
    return DshUsageVisitsOut.model_validate(
        {"from": raw.get("from"), "to": raw.get("to"), "items": items}
    )


@router.get("/dsh-usage/sessions", response_model=DshUsageSessionsOut)
async def dsh_usage_sessions(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    username: str | None = Query(None),
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> DshUsageSessionsOut:
    raw = signed_dsh_post(
        "/internal/usage/sessions",
        _usage_payload(from_=from_, to=to, username=username),
    )
    names = await _username_display_map(db)
    items = [
        DshUsageSession(
            username=str(row.get("username") or ""),
            name=names.get(str(row.get("username") or "")) or None,
            session_id=str(row.get("session_id") or ""),
            model=str(row.get("model") or ""),
            provider=str(row.get("provider") or ""),
            turns=int(row.get("turns") or 0),
            steps=int(row.get("steps") or 0),
            input_tokens=int(row.get("input_tokens") or 0),
            output_tokens=int(row.get("output_tokens") or 0),
            cache_read_tokens=int(row.get("cache_read_tokens") or 0),
            cache_write_tokens=int(row.get("cache_write_tokens") or 0),
            last_prompt_at=row.get("last_prompt_at"),
        )
        for row in (raw.get("items") or [])
        if row.get("username") and row.get("session_id")
    ]
    return DshUsageSessionsOut.model_validate(
        {"from": raw.get("from"), "to": raw.get("to"), "items": items}
    )


@router.post("/reminders/blocker-overdue")
async def run_blocker_overdue_reminder(
    user: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发：卡点跟进逾期提醒。"""
    return await send_blocker_overdue_notices(db)


@router.post("/reminders/phase-overdue")
async def run_phase_overdue_reminder(
    user: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发：阶段计划逾期提醒。"""
    return await send_phase_overdue_notices(db)


@router.post("/reminders/weekly-report-overdue")
async def run_weekly_report_overdue_reminder(
    user: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """手动触发：周报未交提醒。"""
    return await send_weekly_report_overdue_notices(db)


def _template_out(row: NotificationTemplate) -> NotificationTemplateOut:
    return NotificationTemplateOut(
        id=row.id,
        event_key=row.event_key,
        name=row.name,
        description=row.description or "",
        title_template=row.title_template,
        body_template=row.body_template,
        channel_inbox=bool(row.channel_inbox),
        channel_dingtalk=bool(row.channel_dingtalk),
        is_enabled=bool(row.is_enabled),
        schedule_cron=row.schedule_cron or "",
        max_per_day=int(row.max_per_day or 0),
        quiet_start=format_time_opt(row.quiet_start),
        quiet_end=format_time_opt(row.quiet_end),
        updated_at=row.updated_at,
    )


@router.get("/notification-templates", response_model=list[NotificationTemplateOut])
async def list_notification_templates(
    user: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
) -> list[NotificationTemplateOut]:
    result = await db.execute(
        select(NotificationTemplate).order_by(NotificationTemplate.id)
    )
    return [_template_out(r) for r in result.scalars().all()]


@router.patch("/notification-templates/{template_id}", response_model=NotificationTemplateOut)
async def update_notification_template(
    template_id: int,
    body: NotificationTemplateUpdate,
    admin: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
) -> NotificationTemplateOut:
    row = await db.get(NotificationTemplate, template_id)
    if not row:
        raise HTTPException(status_code=404, detail="模板不存在")
    data = body.model_dump(exclude_unset=True)
    if "quiet_start" in data:
        try:
            row.quiet_start = parse_hhmm(data.pop("quiet_start"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if "quiet_end" in data:
        try:
            row.quiet_end = parse_hhmm(data.pop("quiet_end"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key, val in data.items():
        setattr(row, key, val)
    db.add(
        AuditLog(
            user_id=admin.id,
            action="update_notification_template",
            resource="notification_template",
            resource_id=template_id,
            detail=row.event_key,
        )
    )
    await db.commit()
    await db.refresh(row)
    return _template_out(row)


@router.get("/notification-logs", response_model=NotificationDeliveryLogListOut)
async def list_notification_logs(
    user: User = Depends(require_permission("admin.notify")),
    db: AsyncSession = Depends(get_db),
    event_key: str | None = Query(default=None),
    channel: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> NotificationDeliveryLogListOut:
    filters = []
    if event_key:
        filters.append(NotificationDeliveryLog.event_key == event_key)
    if channel:
        filters.append(NotificationDeliveryLog.channel == channel)
    if status:
        filters.append(NotificationDeliveryLog.status == status)

    count_q = select(func.count()).select_from(NotificationDeliveryLog)
    list_q = select(NotificationDeliveryLog)
    if filters:
        count_q = count_q.where(*filters)
        list_q = list_q.where(*filters)

    total = int((await db.execute(count_q)).scalar_one() or 0)
    result = await db.execute(
        list_q.order_by(NotificationDeliveryLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    rows = list(result.scalars().all())
    user_ids = {r.user_id for r in rows if r.user_id}
    name_map: dict[int, str] = {}
    if user_ids:
        ures = await db.execute(select(User).where(User.id.in_(user_ids)))
        name_map = {u.id: u.name for u in ures.scalars().all()}

    items = [
        NotificationDeliveryLogOut(
            id=r.id,
            event_key=r.event_key,
            template_id=r.template_id,
            user_id=r.user_id,
            user_name=name_map.get(r.user_id or 0, ""),
            channel=r.channel,
            title=r.title or "",
            content=r.content or "",
            status=r.status,
            error_message=r.error_message or "",
            ref_type=r.ref_type or "",
            ref_id=r.ref_id or "",
            created_at=r.created_at,
        )
        for r in rows
    ]
    return NotificationDeliveryLogListOut(total=total, items=items)


class EmbeddingSettingsUpdate(BaseModel):
    embedding_model: str = ""
    embedding_base_url: str = ""
    api_key: str | None = None
    clear_api_key: bool = False


@router.get("/embedding-settings")
async def get_embedding_settings(
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _ = user
    cfg = await emb_settings.load_embedding_config(db)
    return emb_settings.public_view(cfg)


@router.put("/embedding-settings")
async def put_embedding_settings(
    body: EmbeddingSettingsUpdate,
    admin: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    cfg = await emb_settings.save_embedding_config(
        db,
        model=body.embedding_model,
        base_url=body.embedding_base_url,
        api_key=body.api_key,
        clear_api_key=bool(body.clear_api_key),
    )
    db.add(
        AuditLog(
            user_id=admin.id,
            action="update",
            resource="embedding_settings",
            resource_id=None,
            detail=f"model={cfg.model};base={cfg.base_url};enabled={cfg.enabled}",
        )
    )
    await db.commit()
    return {"ok": True, **emb_settings.public_view(cfg)}


@router.post("/embedding-settings/test")
async def test_embedding_settings(
    body: EmbeddingSettingsUpdate | None = None,
    user: User = Depends(require_permission("admin.model")),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """用当前已存配置，或请求体临时值探活（不落库）。"""
    _ = user
    saved = await emb_settings.load_embedding_config(db)
    model = saved.model
    base = saved.base_url
    key = saved.api_key
    if body is not None:
        if (body.embedding_model or "").strip() or (body.embedding_base_url or "").strip():
            model = (body.embedding_model or "").strip() or model
            base = (body.embedding_base_url or "").strip().rstrip("/") or base
        if body.clear_api_key:
            key = ""
        elif body.api_key is not None and str(body.api_key).strip():
            key = str(body.api_key).strip()
    if not model or not base:
        return {"ok": False, "message": "请先填写模型名与 API 地址"}
    prev = emb_settings.cached_config()
    emb_settings.set_cached_config(
        emb_settings.EmbeddingConfig(model=model, base_url=base, api_key=key, source="test")
    )
    try:
        vec = await emb_client.embed_one("连通性测试")
        return {
            "ok": True,
            "message": f"连通成功，向量维度 {len(vec)}",
            "dimensions": len(vec),
        }
    except Exception as exc:
        return {"ok": False, "message": str(exc)[:300]}
    finally:
        emb_settings.set_cached_config(prev)
