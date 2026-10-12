"""从钉钉部门同步用户：保留 admin，清空其他，按工号导入。"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.models import (
    AuditLog,
    Blocker,
    BlockerHistory,
    Notification,
    NotificationDeliveryLog,
    ProgressReport,
    ProgressReportItem,
    ProgressReportReview,
    Project,
    ProjectLevelChange,
    ProjectMember,
    Role,
    Solution,
    Task,
    TaskAssistant,
    TaskEvent,
    TaskReview,
    User,
    UserRole,
    WeeklyReport,
)
from app.services import dingtalk as dingtalk_svc


# 团队同步白名单（姓名精确匹配钉钉通讯录）
TEAM_SYNC_NAMES: tuple[str, ...] = (
    "孙振振",  # 管理员
    "周永祺",
    "许金斌",
    "金滨",
    "王捷",
    "朱高见",
    "俞烨涛",
    "孙聪",
    "徐初",
    "凌亮",
    "罗琳",
    "钱波",
)

TEAM_ADMIN_NAME = "孙振振"


def _mask_mobile(mobile: str) -> str:
    m = (mobile or "").strip()
    if len(m) < 7:
        return "***"
    return f"{m[:3]}****{m[-4:]}"


async def _get_admin(db: AsyncSession) -> User:
    result = await db.execute(select(User).where(User.username == "admin"))
    admin = result.scalar_one_or_none()
    if not admin:
        raise ValueError("系统缺少 admin 账号，无法同步")
    return admin


async def _detach_and_delete_users(
    db: AsyncSession, user_ids: list[int], *, reassign_to: User
) -> int:
    """处理外键后硬删除用户。项目/任务等改挂到 reassign_to。"""
    ids = [i for i in user_ids if i != reassign_to.id]
    if not ids:
        return 0

    await db.execute(
        update(Project).where(Project.owner_id.in_(ids)).values(owner_id=reassign_to.id)
    )
    await db.execute(delete(ProjectMember).where(ProjectMember.user_id.in_(ids)))
    await db.execute(
        update(ProjectLevelChange)
        .where(ProjectLevelChange.changed_by_id.in_(ids))
        .values(changed_by_id=reassign_to.id)
    )

    await db.execute(
        update(Blocker).where(Blocker.assignee_id.in_(ids)).values(assignee_id=reassign_to.id)
    )
    await db.execute(
        update(Blocker)
        .where(Blocker.lead_acked_by_id.in_(ids))
        .values(lead_acked_by_id=None)
    )
    await db.execute(
        update(BlockerHistory)
        .where(BlockerHistory.changed_by_id.in_(ids))
        .values(changed_by_id=reassign_to.id)
    )

    await db.execute(delete(TaskAssistant).where(TaskAssistant.user_id.in_(ids)))
    await db.execute(delete(TaskReview).where(TaskReview.user_id.in_(ids)))
    await db.execute(
        update(Task).where(Task.assignee_id.in_(ids)).values(assignee_id=reassign_to.id)
    )
    await db.execute(
        update(Task).where(Task.created_by_id.in_(ids)).values(created_by_id=reassign_to.id)
    )
    await db.execute(
        update(TaskEvent).where(TaskEvent.actor_id.in_(ids)).values(actor_id=reassign_to.id)
    )

    await db.execute(
        update(Solution).where(Solution.uploader_id.in_(ids)).values(uploader_id=reassign_to.id)
    )

    await db.execute(delete(WeeklyReport).where(WeeklyReport.user_id.in_(ids)))
    report_ids = list(
        (
            await db.execute(select(ProgressReport.id).where(ProgressReport.user_id.in_(ids)))
        ).scalars().all()
    )
    if report_ids:
        # 周报「下一步」同步的交办挂了 source_report_id，删报告前须断开
        await db.execute(
            update(Task)
            .where(Task.source_report_id.in_(report_ids))
            .values(source_report_id=None)
        )
        await db.execute(
            delete(ProgressReportItem).where(ProgressReportItem.report_id.in_(report_ids))
        )
        await db.execute(
            delete(ProgressReportReview).where(
                ProgressReportReview.report_id.in_(report_ids)
            )
        )
    await db.execute(delete(ProgressReportReview).where(ProgressReportReview.user_id.in_(ids)))
    await db.execute(delete(ProgressReport).where(ProgressReport.user_id.in_(ids)))
    await db.execute(delete(Notification).where(Notification.user_id.in_(ids)))
    await db.execute(
        update(NotificationDeliveryLog)
        .where(NotificationDeliveryLog.user_id.in_(ids))
        .values(user_id=None)
    )
    await db.execute(
        update(AuditLog).where(AuditLog.user_id.in_(ids)).values(user_id=None)
    )
    await db.execute(delete(UserRole).where(UserRole.user_id.in_(ids)))
    await db.execute(delete(User).where(User.id.in_(ids)))
    await db.flush()
    return len(ids)


async def delete_user_account(db: AsyncSession, user_id: int, *, actor: User) -> str:
    """
    删除单个账号。禁止删 admin、禁止删自己。
    业务数据改挂到 admin（若目标不是 admin 则改挂 actor 所在系统的 admin）。
    返回被删用户的 username。
    """
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise ValueError("用户不存在")
    if target.username == "admin":
        raise ValueError("不能删除系统管理员 admin")
    if target.id == actor.id:
        raise ValueError("不能删除当前登录账号")

    admin = await _get_admin(db)
    username = target.username
    await _detach_and_delete_users(db, [target.id], reassign_to=admin)
    db.add(
        AuditLog(
            user_id=actor.id,
            action="delete",
            resource="user",
            resource_id=user_id,
            detail=username,
        )
    )
    await db.flush()
    return username


async def delete_non_admin_users(db: AsyncSession, admin: User) -> int:
    """硬删除所有非 admin 用户，并处理外键引用。返回删除人数。"""
    result = await db.execute(select(User.id).where(User.username != "admin"))
    ids = list(result.scalars().all())
    return await _detach_and_delete_users(db, ids, reassign_to=admin)


async def sync_department_users(
    db: AsyncSession,
    *,
    actor: User,
    department_name: str = "政企市场部",
) -> dict:
    """
    1) 查找钉钉部门 2) 删除非 admin 3) 导入部门成员
    初始密码 = 工号 + 手机号；首次强制改密。
    """
    dept = await dingtalk_svc.find_department_by_name(department_name)
    dept_id = int(dept.get("dept_id") or dept.get("id") or 0)
    if not dept_id:
        raise ValueError("钉钉部门缺少 dept_id")

    members = await dingtalk_svc.list_department_users(dept_id, include_children=True)

    admin = await _get_admin(db)
    deleted = await delete_non_admin_users(db, admin)

    member_role = (
        await db.execute(select(Role).where(Role.code == "member"))
    ).scalar_one_or_none()
    if not member_role:
        raise ValueError("系统缺少 member 角色")

    imported: list[dict] = []
    skipped: list[dict] = []
    seen_job: set[str] = set()

    for m in members:
        name = m.get("name") or ""
        job = (m.get("job_number") or "").strip()
        mobile = (m.get("mobile") or "").strip()
        userid = (m.get("userid") or "").strip()
        job_title = (m.get("title") or "").strip()
        if not job:
            skipped.append(
                {
                    "name": name,
                    "userid": userid,
                    "reason": "缺少工号",
                    "mobile_masked": _mask_mobile(mobile) if mobile else "",
                }
            )
            continue
        if not mobile:
            skipped.append(
                {
                    "name": name,
                    "job_number": job,
                    "userid": userid,
                    "reason": "缺少手机号",
                }
            )
            continue
        if job in seen_job:
            skipped.append(
                {
                    "name": name,
                    "job_number": job,
                    "userid": userid,
                    "reason": "工号重复",
                }
            )
            continue
        # 不覆盖 admin
        if job == "admin":
            skipped.append(
                {
                    "name": name,
                    "job_number": job,
                    "userid": userid,
                    "reason": "工号与管理员登录名冲突",
                }
            )
            continue
        seen_job.add(job)

        # 钉钉 userid 若已被 admin 占用则解绑 admin 侧（极少见）
        if userid:
            clash = await db.execute(
                select(User).where(User.dingtalk_user_id == userid)
            )
            clash_user = clash.scalar_one_or_none()
            if clash_user and clash_user.username == "admin":
                clash_user.dingtalk_user_id = None

        user = User(
            username=job,
            name=name or job,
            hashed_password=hash_password(f"{job}{mobile}"),
            must_change_password=True,
            dingtalk_user_id=userid or None,
            job_title=job_title,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(UserRole(user_id=user.id, role_id=member_role.id))
        imported.append(
            {
                "id": user.id,
                "name": user.name,
                "job_number": job,
                "dingtalk_bound": bool(userid),
            }
        )

    db.add(
        AuditLog(
            user_id=actor.id,
            action="sync_dingtalk_dept",
            resource="user",
            resource_id=None,
            detail=(
                f"dept={department_name}({dept_id}); "
                f"deleted={deleted}; imported={len(imported)}; skipped={len(skipped)}"
            ),
        )
    )
    await db.commit()
    return {
        "department_name": department_name,
        "department_id": dept_id,
        "deleted": deleted,
        "imported_count": len(imported),
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


async def _ensure_roles(db: AsyncSession, user: User, role_codes: list[str]) -> None:
    """把用户角色设为指定集合（多退少补）。"""
    roles = (
        await db.execute(select(Role).where(Role.code.in_(role_codes)))
    ).scalars().all()
    by_code = {r.code: r for r in roles}
    missing = [c for c in role_codes if c not in by_code]
    if missing:
        raise ValueError(f"系统缺少角色: {', '.join(missing)}")

    existing = (
        await db.execute(select(UserRole).where(UserRole.user_id == user.id))
    ).scalars().all()
    want_ids = {by_code[c].id for c in role_codes}
    for ur in existing:
        if ur.role_id not in want_ids:
            await db.execute(
                delete(UserRole).where(
                    UserRole.user_id == ur.user_id,
                    UserRole.role_id == ur.role_id,
                )
            )
    have_ids = {ur.role_id for ur in existing if ur.role_id in want_ids}
    for rid in want_ids:
        if rid not in have_ids:
            db.add(UserRole(user_id=user.id, role_id=rid))
    await db.flush()


async def sync_team_allowlist(
    db: AsyncSession,
    *,
    actor: User,
    department_name: str = "政企市场部",
    allow_names: tuple[str, ...] | list[str] = TEAM_SYNC_NAMES,
    admin_name: str = TEAM_ADMIN_NAME,
) -> dict:
    """
    只同步白名单姓名：
    1) 从钉钉部门拉人并按姓名过滤
    2) 删除不在白名单且非 admin 的用户
    3) 导入/更新白名单成员
    4) admin_name 设为系统管理员（admin + dept_head），钉钉免登挂其 userid
    """
    allow_set = {n.strip() for n in allow_names if n and n.strip()}
    if not allow_set:
        raise ValueError("白名单为空")
    if admin_name not in allow_set:
        raise ValueError(f"管理员 {admin_name} 不在白名单中")

    dept = await dingtalk_svc.find_department_by_name(department_name)
    dept_id = int(dept.get("dept_id") or dept.get("id") or 0)
    if not dept_id:
        raise ValueError("钉钉部门缺少 dept_id")

    members = await dingtalk_svc.list_department_users(dept_id, include_children=True)
    matched = [m for m in members if (m.get("name") or "").strip() in allow_set]
    found_names = {(m.get("name") or "").strip() for m in matched}
    missing_names = sorted(allow_set - found_names)

    admin = await _get_admin(db)
    member_role = (
        await db.execute(select(Role).where(Role.code == "member"))
    ).scalar_one_or_none()
    if not member_role:
        raise ValueError("系统缺少 member 角色")

    # 先导入/更新白名单，再删其他人，避免误删后无法回滚中间态
    imported: list[dict] = []
    updated: list[dict] = []
    skipped: list[dict] = []
    seen_job: set[str] = set()
    admin_dingtalk_userid: str | None = None
    admin_job_title = ""
    admin_user: User | None = None

    for m in matched:
        name = (m.get("name") or "").strip()
        job = (m.get("job_number") or "").strip()
        mobile = (m.get("mobile") or "").strip()
        userid = (m.get("userid") or "").strip()
        job_title = (m.get("title") or "").strip()
        if not job:
            skipped.append({"name": name, "userid": userid, "reason": "缺少工号"})
            continue
        if not mobile:
            skipped.append({"name": name, "job_number": job, "userid": userid, "reason": "缺少手机号"})
            continue
        if job.lower() == "admin":
            skipped.append({"name": name, "job_number": job, "reason": "工号与管理员登录名冲突"})
            continue
        job_key = job.lower()
        if job_key in seen_job:
            skipped.append({"name": name, "job_number": job, "reason": "工号重复"})
            continue
        seen_job.add(job_key)

        existing = (
            await db.execute(select(User).where(func.lower(User.username) == job_key))
        ).scalar_one_or_none()

        # 钉钉 userid 冲突：先从其他账号解绑（稍后挂到本人或 admin）
        if userid:
            clash = (
                await db.execute(select(User).where(User.dingtalk_user_id == userid))
            ).scalar_one_or_none()
            if clash and (not existing or clash.id != existing.id):
                clash.dingtalk_user_id = None

        is_team_admin = name == admin_name
        if is_team_admin:
            admin_dingtalk_userid = userid or None
            admin_job_title = job_title

        if existing:
            existing.name = name
            existing.job_title = job_title
            existing.is_active = True
            existing.hashed_password = hash_password(f"{job}{mobile}")
            # 管理员钉钉挂到系统 admin，成员账号保留工号登录但不占 userid
            if is_team_admin:
                existing.dingtalk_user_id = None
                admin_user = existing
            else:
                existing.dingtalk_user_id = userid or None
            await _ensure_roles(
                db,
                existing,
                ["admin", "dept_head"] if is_team_admin else ["member"],
            )
            updated.append(
                {
                    "id": existing.id,
                    "name": existing.name,
                    "job_number": existing.username,
                    "roles": ["admin", "dept_head"] if is_team_admin else ["member"],
                }
            )
        else:
            user = User(
                username=job,
                name=name,
                hashed_password=hash_password(f"{job}{mobile}"),
                must_change_password=True,
                dingtalk_user_id=None if is_team_admin else (userid or None),
                job_title=job_title,
                is_active=True,
            )
            db.add(user)
            await db.flush()
            await _ensure_roles(
                db,
                user,
                ["admin", "dept_head"] if is_team_admin else ["member"],
            )
            if is_team_admin:
                admin_user = user
            imported.append(
                {
                    "id": user.id,
                    "name": user.name,
                    "job_number": job,
                    "roles": ["admin", "dept_head"] if is_team_admin else ["member"],
                }
            )

    # 系统 admin：挂孙振振钉钉，便于免登；名称改为孙振振
    if admin_dingtalk_userid:
        # 清掉占用
        occupied = (
            await db.execute(select(User).where(User.dingtalk_user_id == admin_dingtalk_userid))
        ).scalars().all()
        for u in occupied:
            u.dingtalk_user_id = None
        admin.dingtalk_user_id = admin_dingtalk_userid
    admin.name = admin_name
    if admin_job_title:
        admin.job_title = admin_job_title
    admin.is_active = True
    await _ensure_roles(db, admin, ["admin", "dept_head"])

    # 删除白名单外的非 admin 用户
    keep_usernames = {"admin"} | seen_job
    all_users = (await db.execute(select(User))).scalars().all()
    to_delete = [
        u.id
        for u in all_users
        if u.username != "admin" and u.username.lower() not in keep_usernames
    ]
    deleted = await _detach_and_delete_users(db, to_delete, reassign_to=admin)

    db.add(
        AuditLog(
            user_id=actor.id,
            action="sync_dingtalk_team_allowlist",
            resource="user",
            resource_id=None,
            detail=(
                f"dept={department_name}({dept_id}); deleted={deleted}; "
                f"imported={len(imported)}; updated={len(updated)}; "
                f"skipped={len(skipped)}; missing={missing_names}; "
                f"admin={admin_name}; admin_user_id={admin_user.id if admin_user else None}"
            ),
        )
    )
    await db.commit()
    return {
        "department_name": department_name,
        "department_id": dept_id,
        "allow_names": sorted(allow_set),
        "admin_name": admin_name,
        "deleted": deleted,
        "imported_count": len(imported),
        "imported": imported,
        "updated_count": len(updated),
        "updated": updated,
        "skipped_count": len(skipped),
        "skipped": skipped,
        "missing_in_dingtalk": missing_names,
        "admin_dingtalk_bound": bool(admin_dingtalk_userid),
    }


def is_test_account(username: str | None) -> bool:
    """测试脚本建的账号，不要出现在角色下拉里。"""
    name = (username or "").lower()
    return name.startswith(("pr_", "ta_", "t_"))


async def refresh_job_titles(
    db: AsyncSession,
    *,
    department_name: str = "政企市场部",
) -> dict:
    """只补钉钉岗位，不删人、不改密码。按 userid 或工号对上已有账号。"""
    dept = await dingtalk_svc.find_department_by_name(department_name)
    dept_id = int(dept.get("dept_id") or dept.get("id") or 0)
    if not dept_id:
        raise ValueError("钉钉部门缺少 dept_id")
    members = await dingtalk_svc.list_department_users(dept_id, include_children=True)
    users = (await db.execute(select(User))).scalars().all()
    by_dt = {u.dingtalk_user_id: u for u in users if u.dingtalk_user_id}
    by_username = {u.username.lower(): u for u in users if u.username}
    updated = 0
    matched = 0
    for member in members:
        title = (member.get("title") or "").strip()
        userid = (member.get("userid") or "").strip()
        job = (member.get("job_number") or "").strip().lower()
        user = by_dt.get(userid) if userid else None
        if user is None and job:
            user = by_username.get(job)
        if user is None:
            continue
        matched += 1
        if title and (user.job_title or "") != title:
            user.job_title = title
            updated += 1
    await db.commit()
    return {
        "fetched": len(members),
        "with_title": sum(1 for m in members if (m.get("title") or "").strip()),
        "matched": matched,
        "updated": updated,
    }
