"""演示数据种子脚本：创建示例项目、阶段、卡点和交办任务。"""

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.core.database import async_session, engine, Base
from app.core.migrations import run_migrations
from app.models import Blocker, Phase, Project, ProjectMember, Task, TaskAssistant, TaskEvent, User
from app.services.init_service import ensure_demo_users, init_rbac


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await run_migrations()

    async with async_session() as db:
        await init_rbac(db)
        await ensure_demo_users(db)

        owner = (await db.execute(select(User).where(User.username == "owner1"))).scalar_one()
        member1 = (await db.execute(select(User).where(User.username == "member1"))).scalar_one()
        member2 = (await db.execute(select(User).where(User.username == "member2"))).scalar_one()
        manager = (await db.execute(select(User).where(User.username == "manager"))).scalar_one()

        demo_projects = [
            {
                "name": "家庭医生签约系统",
                "project_type": "software",
                "level": "red",
                "status": "at_risk",
                "current_phase": "联调阶段",
                "owner_id": owner.id,
                "phases": [
                    ("需求分析", "completed", -30),
                    ("开发阶段", "completed", -10),
                    ("联调阶段", "in_progress", 5),
                    ("上线验收", "not_started", 20),
                ],
                "blocker": {
                    "description": "第三方接口文档未提供，联调无法推进",
                    "responsible_party": "XX公司-王工",
                    "party_type": "third_party",
                    "actions_taken": "已邮件催促3次，电话沟通1次",
                    "next_action": "王工周五前提供接口文档",
                    "next_follow_up": date.today() + timedelta(days=2),
                },
            },
            {
                "name": "民政展厅装修",
                "project_type": "engineering",
                "level": "yellow",
                "status": "normal",
                "current_phase": "施工阶段",
                "owner_id": owner.id,
                "phases": [
                    ("方案设计", "completed", -45),
                    ("施工阶段", "in_progress", 15),
                    ("验收交付", "not_started", 30),
                ],
                "blocker": {
                    "description": "消防审批材料缺少盖章件",
                    "responsible_party": "行政审批中心",
                    "party_type": "approval",
                    "actions_taken": "已补齐材料并提交",
                    "next_action": "跟进审批进度",
                    "next_follow_up": date.today() + timedelta(days=5),
                },
            },
            {
                "name": "办公设备采购",
                "project_type": "procurement",
                "level": "green",
                "status": "normal",
                "current_phase": "到货验收",
                "owner_id": owner.id,
                "phases": [
                    ("询价比价", "completed", -20),
                    ("下单采购", "completed", -5),
                    ("到货验收", "in_progress", 7),
                ],
                "blocker": None,
            },
        ]

        first_project_id: int | None = None
        for item in demo_projects:
            existing = await db.execute(select(Project).where(Project.name == item["name"]))
            project = existing.scalar_one_or_none()
            if project:
                if first_project_id is None:
                    first_project_id = project.id
                continue
            project = Project(
                name=item["name"],
                project_type=item["project_type"],
                level=item["level"],
                status=item["status"],
                current_phase=item["current_phase"],
                owner_id=item["owner_id"],
                next_update_date=date.today() + timedelta(days=3),
            )
            db.add(project)
            await db.flush()
            if first_project_id is None:
                first_project_id = project.id
            db.add(ProjectMember(project_id=project.id, user_id=owner.id, member_role="lead"))
            db.add(ProjectMember(project_id=project.id, user_id=member1.id, member_role="member"))
            db.add(ProjectMember(project_id=project.id, user_id=member2.id, member_role="member"))
            db.add(ProjectMember(project_id=project.id, user_id=manager.id, member_role="assistant"))

            for idx, (phase_name, status, day_offset) in enumerate(item["phases"]):
                db.add(
                    Phase(
                        project_id=project.id,
                        name=phase_name,
                        status=status,
                        planned_end_date=date.today() + timedelta(days=day_offset),
                        sort_order=idx,
                    )
                )

            if item["blocker"]:
                b = item["blocker"]
                db.add(
                    Blocker(
                        project_id=project.id,
                        assignee_id=owner.id,
                        description=b["description"],
                        responsible_party=b["responsible_party"],
                        party_type=b["party_type"],
                        found_date=date.today() - timedelta(days=3),
                        actions_taken=b["actions_taken"],
                        status="following",
                        next_action=b["next_action"],
                        next_follow_up=b["next_follow_up"],
                    )
                )

        await db.flush()

        if first_project_id:
            demo_tasks = [
                {
                    "title": "【交办】催促第三方接口文档",
                    "assignment_type": "delegated",
                    "assignee_id": member1.id,
                    "created_by_id": owner.id,
                    "assistant_ids": [member2.id],
                    "due_date": date.today() + timedelta(days=3),
                },
                {
                    "title": "【自建】整理本周联调问题清单",
                    "assignment_type": "self",
                    "assignee_id": member1.id,
                    "created_by_id": member1.id,
                    "assistant_ids": [],
                    "due_date": date.today() + timedelta(days=1),
                },
            ]
            for t in demo_tasks:
                exists = await db.execute(
                    select(Task).where(
                        Task.project_id == first_project_id,
                        Task.title == t["title"],
                    )
                )
                if exists.scalar_one_or_none():
                    continue
                task = Task(
                    project_id=first_project_id,
                    assignee_id=t["assignee_id"],
                    created_by_id=t["created_by_id"],
                    assignment_type=t["assignment_type"],
                    title=t["title"],
                    due_date=t["due_date"],
                    status="in_progress",
                    result_text="",
                    source="manual",
                )
                db.add(task)
                await db.flush()
                for uid in t["assistant_ids"]:
                    db.add(TaskAssistant(task_id=task.id, user_id=uid))
                db.add(
                    TaskEvent(
                        task_id=task.id,
                        actor_id=t["created_by_id"],
                        action="created",
                        payload={
                            "assignment_type": t["assignment_type"],
                            "assignee_id": t["assignee_id"],
                            "assistant_ids": t["assistant_ids"],
                            "title": t["title"],
                        },
                    )
                )

        await db.commit()
        print("演示数据初始化完成")


if __name__ == "__main__":
    asyncio.run(seed())
