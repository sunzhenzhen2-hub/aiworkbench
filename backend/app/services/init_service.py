from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Permission, Role, RolePermission, User, UserRole
from app.rbac.permissions import DEFAULT_PERMISSIONS, DEFAULT_ROLES
from app.core.security import hash_password
from app.services.notification_templates_seed import seed_notification_templates

# 演示账号初始密码（首次登录须修改；8位+大小写字母+数字混合）
DEMO_USERS = [
    ("admin", "系统管理员", "InitAdmin2026", ["admin", "dept_head"]),
    ("manager", "张主管", "InitManager2026", ["dept_head"]),
    ("owner1", "李项目", "InitOwner2026", ["project_owner"]),
    ("member1", "王成员", "InitMember2026", ["member"]),
    ("member2", "赵成员", "InitMember22026", ["member"]),
]


async def init_rbac(db: AsyncSession) -> None:
    """补全新权限点与新角色；已有角色的授权不覆盖，避免后台勾选被重启冲掉。"""
    perm_map: dict[str, Permission] = {}
    newly_created: set[str] = set()
    for code, name, desc in DEFAULT_PERMISSIONS:
        result = await db.execute(select(Permission).where(Permission.code == code))
        perm = result.scalar_one_or_none()
        if not perm:
            perm = Permission(code=code, name=name, description=desc)
            db.add(perm)
            newly_created.add(code)
        perm_map[code] = perm
    await db.flush()

    for role_code, cfg in DEFAULT_ROLES.items():
        result = await db.execute(select(Role).where(Role.code == role_code))
        role = result.scalar_one_or_none()
        if not role:
            role = Role(code=role_code, name=cfg["name"], description=cfg["description"])
            db.add(role)
            await db.flush()
            for perm_code in cfg["permissions"]:
                if perm_code in perm_map:
                    db.add(RolePermission(role_id=role.id, permission_id=perm_map[perm_code].id))
            continue

        if not newly_created:
            continue
        existing = await db.execute(
            select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
        )
        existing_ids = {row[0] for row in existing.all()}
        for perm_code in cfg["permissions"]:
            if perm_code not in newly_created:
                continue
            perm = perm_map[perm_code]
            if perm.id not in existing_ids:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))

    # 升级补丁：管理员/部门负责人必须能写周报（仅补缺，不删权限）
    await _ensure_role_permissions(
        db,
        perm_map,
        {
            "dept_head": [
                "report.submit",
                "report.view.all",
                "project.members",
                "project.delete",
                "task.create.self",
                "task.assign",
                "solution.view",
                "solution.upload",
                "dsh.access",
                "assist.analyze",
                "stats.view",
            ],
            "project_owner": [
                "project.view.all",
                "project.create",
                "task.create.self",
                "task.assign",
                "solution.view",
                "solution.upload",
                "dsh.access",
            ],
            "member": [
                "project.view.all",
                "project.create",
                "task.create.self",
                "solution.view",
                "solution.upload",
                "dsh.access",
                "dingtalk.login",
            ],
            "admin": [
                "report.submit",
                "report.view.all",
                "project.members",
                "project.edit",
                "project.delete",
                "dingtalk.login",
                "admin.model",
                "admin.notify",
                "task.create.self",
                "task.assign",
                "solution.view",
                "solution.upload",
                "dsh.access",
                "assist.analyze",
                "stats.view",
            ],
        },
    )

    await seed_notification_templates(db)
    await db.commit()


async def _ensure_role_permissions(
    db: AsyncSession,
    perm_map: dict[str, Permission],
    required: dict[str, list[str]],
) -> None:
    for role_code, codes in required.items():
        result = await db.execute(select(Role).where(Role.code == role_code))
        role = result.scalar_one_or_none()
        if not role:
            continue
        existing = await db.execute(
            select(RolePermission.permission_id).where(RolePermission.role_id == role.id)
        )
        existing_ids = {row[0] for row in existing.all()}
        for code in codes:
            perm = perm_map.get(code)
            if not perm or perm.id in existing_ids:
                continue
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
            existing_ids.add(perm.id)

async def ensure_demo_users(db: AsyncSession, *, reset_passwords: bool = False) -> None:
    for username, name, password, role_codes in DEMO_USERS:
        result = await db.execute(
            select(User).options(selectinload(User.roles)).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        created = False
        if not user:
            user = User(
                username=username,
                name=name,
                hashed_password=hash_password(password),
                must_change_password=True,
            )
            db.add(user)
            await db.flush()
            created = True
        elif reset_passwords:
            user.hashed_password = hash_password(password)
            user.must_change_password = True
        if created or reset_passwords:
            roles_result = await db.execute(select(Role).where(Role.code.in_(role_codes)))
            roles = list(roles_result.scalars().all())
            await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
            for role in roles:
                db.add(UserRole(user_id=user.id, role_id=role.id))
    await db.commit()
