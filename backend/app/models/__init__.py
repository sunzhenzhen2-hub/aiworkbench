from datetime import date, datetime, time
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(String(256), nullable=True)
    dingtalk_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    # 钉钉通讯录岗位（title），用来判断谁能当项目支撑
    job_title: Mapped[str] = mapped_column(String(64), default="", server_default="")
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", back_populates="users")
    led_projects: Mapped[list["Project"]] = relationship(back_populates="owner")
    memberships: Mapped[list["ProjectMember"]] = relationship(back_populates="user")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)

    users: Mapped[list[User]] = relationship(secondary="user_roles", back_populates="roles")
    permissions: Mapped[list["Permission"]] = relationship(
        secondary="role_permissions", back_populates="roles"
    )


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(256), nullable=True)

    roles: Mapped[list[Role]] = relationship(
        secondary="role_permissions", back_populates="permissions"
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), primary_key=True)
    permission_id: Mapped[int] = mapped_column(ForeignKey("permissions.id"), primary_key=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    project_type: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16), default="normal", index=True)
    status: Mapped[str] = mapped_column(String(16), default="normal")
    current_phase: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_update_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="led_projects")
    members: Mapped[list["ProjectMember"]] = relationship(back_populates="project")
    phases: Mapped[list["Phase"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    blockers: Mapped[list["Blocker"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list["Task"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    reports: Mapped[list["WeeklyReport"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    level_changes: Mapped[list["ProjectLevelChange"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # lead=项目经理（每项目唯一） support=项目支撑 assistant=辅助人 member=普通成员（旧数据）
    member_role: Mapped[str] = mapped_column(String(16), default="member")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class ProjectLevelChange(Base):
    __tablename__ = "project_level_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    old_level: Mapped[str] = mapped_column(String(16))
    new_level: Mapped[str] = mapped_column(String(16))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="level_changes")


class Phase(Base):
    __tablename__ = "phases"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(128))
    planned_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="not_started")
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)

    project: Mapped[Project] = relationship(back_populates="phases")


class Blocker(Base):
    __tablename__ = "blockers"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    description: Mapped[str] = mapped_column(String(512))
    responsible_party: Mapped[str] = mapped_column(String(128))
    party_type: Mapped[str] = mapped_column(String(32), default="internal")
    found_date: Mapped[date] = mapped_column(Date)
    actions_taken: Mapped[str] = mapped_column(Text)
    synced_to: Mapped[str | None] = mapped_column(String(256), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    impact: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="following")
    next_action: Mapped[str] = mapped_column(String(512))
    next_follow_up: Mapped[date] = mapped_column(Date)
    lead_acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lead_acked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="blockers")
    assignee: Mapped[User] = relationship(foreign_keys=[assignee_id])
    histories: Mapped[list["BlockerHistory"]] = relationship(
        back_populates="blocker", cascade="all, delete-orphan"
    )


class BlockerHistory(Base):
    __tablename__ = "blocker_histories"

    id: Mapped[int] = mapped_column(primary_key=True)
    blocker_id: Mapped[int] = mapped_column(ForeignKey("blockers.id"))
    changed_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    old_status: Mapped[str] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    blocker: Mapped[Blocker] = relationship(back_populates="histories")


class Task(Base):
    """任务交办：assignee_id = 负责人（即执行人）。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    # 可选：任务归属的进度阶段（同一项目下）
    phase_id: Mapped[int | None] = mapped_column(
        ForeignKey("phases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # self=员工自建 delegated=管理者交办
    assignment_type: Mapped[str] = mapped_column(String(16), default="self")
    title: Mapped[str] = mapped_column(String(256))
    # 做成什么样 / 做到什么程度（验收目标）
    goal: Mapped[str] = mapped_column(Text, default="")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="not_started")
    # critical=重点 watch=关注 normal=普通
    priority: Mapped[str] = mapped_column(String(16), default="p3", index=True)
    # pending=待接收 accepted=已接收（自建创建即 accepted）
    acceptance_status: Mapped[str] = mapped_column(String(16), default="accepted", index=True)
    accept_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 子任务拆解 JSON: [{title, done, sort_order}]
    checklist: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    result_text: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    # 周报下周计划同步：溯源报告 + 稳定键（chain_id 或 idx:N）
    source_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("progress_reports.id"), nullable=True, index=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    project: Mapped[Project] = relationship(back_populates="tasks")
    phase: Mapped["Phase | None"] = relationship(foreign_keys=[phase_id])
    assignee: Mapped[User] = relationship(foreign_keys=[assignee_id])
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_id])
    assistants: Mapped[list["TaskAssistant"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    events: Mapped[list["TaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TaskEvent.created_at"
    )
    reviews: Mapped[list["TaskReview"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["TaskAttachment"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class TaskAssistant(Base):
    __tablename__ = "task_assistants"
    __table_args__ = (UniqueConstraint("task_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="assistants")
    user: Mapped[User] = relationship(foreign_keys=[user_id])


class TaskEvent(Base):
    """任务操作流水：何时、谁、做了什么。"""

    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    task: Mapped[Task] = relationship(back_populates="events")
    actor: Mapped[User] = relationship(foreign_keys=[actor_id])


class TaskReview(Base):
    """任务评价：AI/规则建议，执行人本人可读。"""

    __tablename__ = "task_reviews"
    __table_args__ = (UniqueConstraint("task_id", name="uq_task_reviews_task"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    summary: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list[Any]] = mapped_column(JSON, default=list)
    risks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    suggestions: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # llm | rule
    source: Mapped[str] = mapped_column(String(16), default="rule")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="reviews")
    author: Mapped[User] = relationship(foreign_keys=[user_id])


class TaskAttachment(Base):
    """任务佐证附件：本机落盘，库表只存元数据。"""

    __tablename__ = "task_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    original_filename: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="attachments")
    uploader: Mapped[User] = relationship(foreign_keys=[uploader_id])


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "week_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    week_start: Mapped[date] = mapped_column(Date)
    completed: Mapped[str] = mapped_column(Text, default="")
    next_plan: Mapped[str] = mapped_column(Text, default="")
    issues: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="reports")
    author: Mapped[User] = relationship(foreign_keys=[user_id])


class ProgressReport(Base):
    """人本周报/日报：一人一周期一份，多条任务卡片。"""

    __tablename__ = "progress_reports"
    __table_args__ = (UniqueConstraint("user_id", "period_type", "period_start"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    period_type: Mapped[str] = mapped_column(String(8), default="week")  # week | day
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    theme: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | submitted
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    author: Mapped[User] = relationship(foreign_keys=[user_id])
    items: Mapped[list["ProgressReportItem"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    attachments: Mapped[list["ProgressReportAttachment"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ProgressReportItem(Base):
    __tablename__ = "progress_report_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("progress_reports.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    # 跨周任务链：同 chain_id 串成时间线；可选挂交办任务
    chain_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    linked_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    task_title: Mapped[str] = mapped_column(String(120), default="")
    no_action: Mapped[bool] = mapped_column(Boolean, default=False)
    result_text: Mapped[str] = mapped_column(Text, default="")
    quant_value: Mapped[str] = mapped_column(String(64), default="")
    stuck_status: Mapped[str] = mapped_column(String(16), default="none")
    stuck_detail: Mapped[str] = mapped_column(Text, default="")
    stuck_next_action: Mapped[str] = mapped_column(String(512), default="")
    stuck_follow_up: Mapped[date | None] = mapped_column(Date, nullable=True)
    stuck_need_who: Mapped[str] = mapped_column(String(256), default="")
    linked_blocker_id: Mapped[int | None] = mapped_column(ForeignKey("blockers.id"), nullable=True)
    next_goal: Mapped[str] = mapped_column(Text, default="")
    next_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(16), default="normal")
    risk_reason: Mapped[str] = mapped_column(Text, default="")
    risk_impact_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    risk_coordinator: Mapped[str] = mapped_column(String(256), default="")
    prior_done: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # 工作标签：售前/售中/售后/运维/项目管理/流程跟进/客户对接/方案汇报 等
    tags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    sort_order: Mapped[int] = mapped_column(default=0)

    report: Mapped[ProgressReport] = relationship(back_populates="items")
    project: Mapped[Project] = relationship(foreign_keys=[project_id])
    linked_task: Mapped["Task | None"] = relationship(foreign_keys=[linked_task_id])


class ProgressReportAttachment(Base):
    """周报/日报证明材料：优先挂在任务条目上；item_id 为空表示整份报告级（旧数据）。"""

    __tablename__ = "progress_report_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("progress_reports.id", ondelete="CASCADE"), index=True
    )
    # 所属任务卡片；条目被重建删除时置空，保存流程再按序号挂回
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("progress_report_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    original_filename: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    report: Mapped[ProgressReport] = relationship(back_populates="attachments")
    uploader: Mapped[User] = relationship(foreign_keys=[uploader_id])


class ProgressReportReview(Base):
    """周报一阶段（及预留多阶段）评价：LLM/规则生成后落库。"""

    __tablename__ = "progress_report_reviews"
    __table_args__ = (
        UniqueConstraint("report_id", "phase", name="uq_progress_report_review_phase"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("progress_reports.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    period_type: Mapped[str] = mapped_column(String(8), default="week")
    period_start: Mapped[date] = mapped_column(Date)
    # 1 = 一阶段自动评价；后续可扩展人工二阶段
    phase: Mapped[int] = mapped_column(default=1)
    summary: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list[Any]] = mapped_column(JSON, default=list)
    risks: Mapped[list[Any]] = mapped_column(JSON, default=list)
    completion_comment: Mapped[str] = mapped_column(Text, default="")
    # llm | rule
    source: Mapped[str] = mapped_column(String(16), default="rule")
    model_name: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    report: Mapped[ProgressReport] = relationship(foreign_keys=[report_id])
    author: Mapped[User] = relationship(foreign_keys=[user_id])


class AssistGuidanceNote(Base):
    """管理员轻量指导备注（无打分）；本人可读。"""

    __tablename__ = "assist_guidance_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    content: Mapped[str] = mapped_column(Text, default="")
    action_items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    # ai | admin
    source: Mapped[str] = mapped_column(String(16), default="admin")
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    improved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    improve_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_user: Mapped[User] = relationship(foreign_keys=[target_user_id])
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    project: Mapped["Project | None"] = relationship(foreign_keys=[project_id])


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationTemplate(Base):
    """系统管理可编辑的通知模板（格式 / 渠道 / 频率）。"""

    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(256), default="")
    title_template: Mapped[str] = mapped_column(String(256))
    body_template: Mapped[str] = mapped_column(Text)
    channel_inbox: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_dingtalk: Mapped[bool] = mapped_column(Boolean, default=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 定时类：五段 cron「分 时 日 月 周」(0=周日)；即时类为空
    schedule_cron: Mapped[str] = mapped_column(String(64), default="")
    # 同一用户+同一 ref 每天最多成功发送次数；0=不限（即时类常用）
    max_per_day: Mapped[int] = mapped_column(Integer, default=0)
    quiet_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NotificationDeliveryLog(Base):
    """通知发送日志（按渠道一条）。"""

    __tablename__ = "notification_delivery_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), index=True)
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_templates.id"), nullable=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(16))  # inbox | dingtalk
    title: Mapped[str] = mapped_column(String(256), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="success")  # success|skipped|failed
    error_message: Mapped[str] = mapped_column(Text, default="")
    ref_type: Mapped[str] = mapped_column(String(32), default="")
    ref_id: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class LlmModel(Base):
    """系统管理中的 LLM / 兼容模型配置。"""

    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32), default="openai_compatible")
    api_base: Mapped[str] = mapped_column(String(512))
    api_key: Mapped[str] = mapped_column(String(512), default="")
    model_name: Mapped[str] = mapped_column(String(128))
    purpose: Mapped[str] = mapped_column(String(32), default="report_coach")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Solution(Base):
    """方案库：历史方案文件与 AI 整理结果。"""

    __tablename__ = "solutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    original_filename: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(128), default="")
    file_size: Mapped[int] = mapped_column(default=0)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # pending | processing | ready | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    uploader: Mapped[User] = relationship(foreign_keys=[uploader_id])
    tags: Mapped[list["SolutionTag"]] = relationship(
        back_populates="solution", cascade="all, delete-orphan"
    )


class SolutionTag(Base):
    __tablename__ = "solution_tags"
    __table_args__ = (UniqueConstraint("solution_id", "name", name="uq_solution_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    solution_id: Mapped[int] = mapped_column(ForeignKey("solutions.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(64))
    # ai | manual
    source: Mapped[str] = mapped_column(String(16), default="ai")

    solution: Mapped[Solution] = relationship(back_populates="tags")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    resource: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[int | None] = mapped_column(nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthState(Base):
    """PC 钉钉扫码 OAuth state，防 CSRF；校验与撤销分离。"""

    __tablename__ = "oauth_state"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    redirect_uri: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentUserPref(Base):
    """个人助手习惯：免打扰与通知渠道。不展示在全员看板。"""

    __tablename__ = "agent_user_prefs"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    quiet_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    digest_mode: Mapped[str] = mapped_column(String(16), default="instant")
    channel_inbox: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_dingtalk: Mapped[bool] = mapped_column(Boolean, default=True)
    channel_dsh: Mapped[bool] = mapped_column(Boolean, default=True)
    prefer_p0: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentDraft(Base):
    """助手起草、等人确认后再写账本。"""

    __tablename__ = "agent_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="reassign")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentNudgeLog(Base):
    """同一事件只催一次。"""

    __tablename__ = "agent_nudge_logs"
    __table_args__ = (UniqueConstraint("event_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SystemSetting(Base):
    """系统级键值配置（如向量模型）。库值优先于 .env。"""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMemoryNote(Base):
    """个人长期记忆笔记：偏好 / 工作摘要 / 事实。不是账本。"""

    __tablename__ = "agent_memory_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="fact", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMemoryChunk(Base):
    """可检索记忆片段（会话摘录等）。embedding 可空：V1 只关键词，V2 再填向量。"""

    __tablename__ = "agent_memory_chunks"
    __table_args__ = (UniqueConstraint("user_id", "source_type", "source_ref", name="uq_memory_chunk_src"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="session", index=True)
    source_ref: Mapped[str] = mapped_column(String(512), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

