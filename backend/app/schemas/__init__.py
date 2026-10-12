from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.password_policy import validate_strong_password


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    must_change_password: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_strong_password(value)


class AdminResetPasswordRequest(BaseModel):
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        return validate_strong_password(value)


class UserCreateRequest(BaseModel):
    username: str
    name: str
    password: str
    email: str | None = None
    role_codes: list[str] = []

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_strong_password(value)


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    username: str
    avatar_url: str | None = None


class UserOut(UserBrief):
    email: str | None = None
    is_active: bool
    must_change_password: bool = False
    roles: list[str] = []
    permissions: list[str] = []
    dingtalk_user_id: str | None = None
    dingtalk_bound: bool = False
    # 工号即登录名，便于前端列展示
    job_number: str = ""
    # 钉钉岗位，空字符串表示还没同步到
    job_title: str = ""


class DingTalkLoginRequest(BaseModel):
    auth_code: str


class DingTalkScanRequest(BaseModel):
    auth_code: str
    state: str


class DingTalkBindRequest(BaseModel):
    bind_ticket: str
    username: str
    password: str


class DingTalkLoginResponse(BaseModel):
    status: str
    access_token: str | None = None
    must_change_password: bool = False
    bind_ticket: str | None = None
    message: str | None = None


class DingTalkConfigOut(BaseModel):
    client_id: str
    corp_id: str = ""
    redirect_uri: str
    state: str
    scope: str = "openid"
    response_type: str = "code"
    prompt: str = "consent"
    sdk_url: str



class AdminDingTalkBindRequest(BaseModel):
    dingtalk_user_id: str | None = None


class DingTalkCandidateOut(BaseModel):
    """绑定时下拉：钉钉通讯录中可选人员。"""

    userid: str
    name: str
    job_number: str = ""
    mobile_masked: str = ""
    # 若已被其他系统账号占用则带出登录名；绑定目标本人则不算占用
    bound_username: str | None = None


class TaskStatusUpdate(BaseModel):
    status: str
    result_text: str | None = None


class TaskCreate(BaseModel):
    project_id: int
    title: str = Field(min_length=1, max_length=256)
    # 做成什么样 / 做到什么程度
    goal: str = ""
    due_date: date | None = None
    # 可选：归属项目下某一进度阶段
    phase_id: int | None = None
    # self | delegated；自建时忽略 assignee_id
    assignment_type: str = "self"
    assignee_id: int | None = None
    assistant_ids: list[int] = []
    # critical | watch | normal → 现为 p0～p4，默认 p3
    priority: str = "p3"
    # 交办：请于何时前接收
    accept_by: datetime | None = None
    # 高度相似未完成任务时，前端二次确认后放行
    force_create: bool = False


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    goal: str | None = None
    due_date: date | None = None
    status: str | None = None
    result_text: str | None = None
    clear_due_date: bool = False
    phase_id: int | None = None
    clear_phase_id: bool = False
    priority: str | None = None
    checklist: list[dict] | None = None


class TaskPriorityUpdate(BaseModel):
    priority: str


class TaskChecklistUpdate(BaseModel):
    checklist: list[dict]


class TaskOwnerUpdate(BaseModel):
    assignee_id: int
    accept_by: datetime | None = None


class TaskDeleteIn(BaseModel):
    """删除任务时必须带上当前登录密码，防止误删。"""

    password: str = Field(min_length=1, max_length=128)


class TaskAssistantAdd(BaseModel):
    user_id: int


class TaskEventOut(BaseModel):
    id: int
    action: str
    actor: UserBrief
    payload: dict | None = None
    created_at: datetime


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    project_name: str | None = None
    phase_id: int | None = None
    phase_name: str | None = None
    title: str
    goal: str = ""
    due_date: date | None = None
    status: str
    priority: str = "p3"
    acceptance_status: str = "accepted"
    accept_by: datetime | None = None
    accepted_at: datetime | None = None
    checklist: list[dict] | None = None
    assignment_type: str = "self"
    result_text: str = ""
    my_role: str | None = None  # owner | assistant | none
    assignee: UserBrief
    created_by: UserBrief | None = None
    assistants: list[UserBrief] = []
    events: list[TaskEventOut] = []
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # 创建时回填：是否写入负责人本周周报草稿 / 钉钉工作通知是否实发
    synced_to_week_draft: bool = False
    dingtalk_notified: bool = False


class TaskListOut(BaseModel):
    items: list[TaskOut]
    total: int
    page: int
    page_size: int


class TaskReviewOut(BaseModel):
    id: int | None = None
    task_id: int | None = None
    summary: str = ""
    strengths: list[str] = []
    risks: list[str] = []
    suggestions: list[str] = []
    source: str = "rule"
    model_name: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    original_filename: str
    mime_type: str = ""
    file_size: int = 0
    uploader_id: int
    uploader_name: str = ""
    created_at: datetime | None = None


class TaskAiDraftIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class TaskGuessProjectIn(BaseModel):
    """写任务时根据标题猜测该归到哪个项目。"""

    title: str = Field(default="", max_length=256)
    goal: str = Field(default="", max_length=2000)


class TaskGuessProjectOut(BaseModel):
    project_id: int | None = None
    project_name: str | None = None
    confidence: float = 0
    reason: str = ""


class TaskCheckSimilarIn(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    goal: str = ""
    project_id: int | None = None
    assignee_id: int | None = None
    assignment_type: str = "self"


class TaskSimilarItem(BaseModel):
    id: int
    title: str
    score: float
    project_id: int
    status: str = ""


class TaskCheckSimilarOut(BaseModel):
    vague: bool = False
    similar: list[TaskSimilarItem] = []
    level: str = "none"  # none | soft | hard
    message: str = ""
    continuation_warning: str = ""


class TaskAiPolishIn(BaseModel):
    """润色目标或结果：field=goal|result_text；text 可省略则用任务当前值。"""

    field: str = Field(description="goal 或 result_text")
    text: str | None = Field(default=None, max_length=5000)


class AiTextIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    project_id: int | None = None


class AiGuidanceSaveIn(BaseModel):
    """管理员保存轻量指导（无打分）。"""

    target_user_id: int
    content: str = Field(min_length=1, max_length=4000)
    action_items: list[str] = []
    project_id: int | None = None
    period_start: date | None = None
    source: str = "admin"  # ai | admin
    notify: bool = False


class AssistGuidanceNoteOut(BaseModel):
    id: int
    target_user_id: int
    project_id: int | None = None
    period_start: date | None = None
    content: str = ""
    action_items: list[Any] = []
    source: str = "admin"
    created_by_id: int | None = None
    created_by: UserBrief | None = None
    created_at: datetime | None = None
    read_at: datetime | None = None
    improved_at: datetime | None = None
    improve_note: str = ""


class AiNudgeIn(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    user_id: int | None = None
    task_id: int | None = None
    project_id: int | None = None
    title: str = ""
    detail: str = ""


class GuidanceImproveIn(BaseModel):
    improve_note: str = Field(default="", max_length=500)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None
    permissions: list[str] = []


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None = None


class ProjectCreate(BaseModel):
    name: str
    project_type: str
    level: str = "normal"
    # 项目经理可暂空；创建人不等于项目经理
    lead_user_id: int | None = None
    support_user_id: int | None = None
    support_user_ids: list[int] = []
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    project_type: str | None = None
    status: str | None = None
    current_phase: str | None = None
    owner_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    next_update_date: date | None = None
    description: str | None = None


class ProjectDeleteIn(BaseModel):
    """删除项目时必须带上当前登录密码，防止误删。"""

    password: str = Field(min_length=1, max_length=128)


class ProjectLevelUpdate(BaseModel):
    level: str


class BlockerBrief(BaseModel):
    description: str
    party_type: str
    next_action: str
    next_follow_up: date
    status: str


class ProjectListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    project_type: str
    level: str
    status: str
    current_phase: str | None = None
    owner: UserBrief
    max_blocker: BlockerBrief | None = None
    next_update_date: date | None = None
    is_update_overdue: bool = False
    # 任务进度（交办任务，必挂项目）
    task_total: int = 0
    task_done: int = 0
    task_in_progress: int = 0
    task_overdue: int = 0
    # 本周周报（含展示期推断归类，不写库）
    week_report_items: int = 0
    week_report_inferred: int = 0
    week_escalate: int = 0
    week_quality: str = "good"  # good | ok | bad


class ProjectListOut(BaseModel):
    items: list[ProjectListItem]
    total: int
    page: int
    page_size: int


class ProjectDetail(ProjectListItem):
    start_date: date | None = None
    end_date: date | None = None
    description: str | None = None


class OverviewStats(BaseModel):
    total_projects: int
    # 重点 / 日常项目数量（原 red_count / yellow_count）
    key_count: int = 0
    daily_count: int = 0
    # 兼容旧前端字段名（逐步废弃）
    red_count: int = 0
    yellow_count: int = 0
    pending_blockers: int
    # 任务进度（可见项目范围内）
    task_total: int = 0
    task_done: int = 0
    task_completion_rate: float = 0.0
    # 本周周报提交/质量（需 report.view.all 才有真实人数；否则为 0）
    report_period_start: date | None = None
    report_period_end: date | None = None
    report_auto_switched: bool = False
    report_expected: int = 0
    report_submitted: int = 0
    report_submit_rate: float = 0.0
    report_quality_alerts: int = 0
    unassigned_report_items: int = 0
    # 未完成的重点任务（priority 为 p0/p1）
    key_task_count: int = 0


class KeyTaskBrief(BaseModel):
    id: int
    title: str
    project_id: int
    project_name: str | None = None
    assignee_name: str | None = None
    due_date: date | None = None
    priority: str = "p0"
    status: str


class PhaseCreate(BaseModel):
    name: str
    planned_end_date: date | None = None
    status: str = "not_started"
    note: str | None = None
    sort_order: int = 0


class PhaseUpdate(BaseModel):
    name: str | None = None
    planned_end_date: date | None = None
    status: str | None = None
    note: str | None = None
    sort_order: int | None = None


class PhaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    planned_end_date: date | None = None
    status: str
    note: str | None = None
    sort_order: int
    is_overdue: bool = False


class BlockerCreate(BaseModel):
    description: str
    responsible_party: str
    party_type: str = "internal"
    found_date: date | None = None
    actions_taken: str
    synced_to: str | None = None
    response: str | None = None
    impact: str | None = None
    status: str = "following"
    next_action: str
    next_follow_up: date
    assignee_id: int | None = None


class BlockerUpdate(BaseModel):
    description: str | None = None
    responsible_party: str | None = None
    party_type: str | None = None
    actions_taken: str | None = None
    synced_to: str | None = None
    response: str | None = None
    impact: str | None = None
    status: str | None = None
    next_action: str | None = None
    next_follow_up: date | None = None
    assignee_id: int | None = None


class BlockerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    description: str
    responsible_party: str
    party_type: str
    found_date: date
    actions_taken: str
    synced_to: str | None = None
    response: str | None = None
    impact: str | None = None
    status: str
    next_action: str
    next_follow_up: date
    assignee: UserBrief
    is_follow_up_overdue: bool = False
    lead_acked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class WeeklyReportCreate(BaseModel):
    project_id: int
    week_start: date
    completed: str = ""
    next_plan: str = ""
    issues: str = ""


class WeeklyReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    project_name: str | None = None
    week_start: date
    completed: str
    next_plan: str
    issues: str
    author: UserBrief
    submitted_at: datetime | None = None


class PersonalSummary(BaseModel):
    """个人关联模块概览：任务 / 周报 / 项目 / 待办。"""

    task_total: int = 0
    task_done: int = 0
    task_open: int = 0
    task_overdue: int = 0
    task_completion_rate: float = 0.0
    project_count: int = 0
    blocker_open: int = 0
    todo_open: int = 0  # 未完成任务 + 未解决卡点
    week_report_status: str = "none"  # none | draft | submitted（all 时：无人/部分/已全交）
    week_report_period_start: date | None = None
    week_report_period_end: date | None = None
    week_report_item_count: int = 0
    # summary_scope=all 时：团队本周周报提交人数
    report_submitted: int = 0
    report_expected: int = 0
    # summary_scope=all 时：重点项目 / 重点任务 / 质量告警
    key_project_count: int = 0
    key_task_count: int = 0
    report_quality_alerts: int = 0


class PersonalReportReviewItem(BaseModel):
    report_id: int
    period_type: str = "week"
    period_start: date
    period_end: date | None = None
    status: str = "submitted"
    review: Phase1ReviewOut | None = None


class PersonalTaskReviewItem(BaseModel):
    task_id: int
    task_title: str
    project_id: int | None = None
    project_name: str | None = None
    my_role: str | None = None
    review: TaskReviewOut


class PersonalReviews(BaseModel):
    report_reviews: list[PersonalReportReviewItem] = []
    task_reviews: list[PersonalTaskReviewItem] = []


class PersonalWorkspace(BaseModel):
    my_tasks: list[TaskOut]
    # 管理员 / 部门负责人可见：成员未完成待办汇总
    department_tasks: list[TaskOut] = []
    can_view_department_todos: bool = False
    my_blockers: list[BlockerOut]
    my_projects: list[ProjectListItem]
    # self=仅本人；all=部门负责人/超级管理员看全量口径
    summary_scope: str = "self"  # self | all
    summary: PersonalSummary = PersonalSummary()


class ProjectMemberOut(BaseModel):
    user: UserBrief
    member_role: str  # lead | support | assistant | member


class RoleCandidateOut(BaseModel):
    id: int
    name: str
    job_title: str = ""


class ProjectMembersUpdate(BaseModel):
    """项目经理可暂空；项目支撑可多人。"""

    lead_user_id: int | None = None
    # 兼容旧前端单选；优先用 support_user_ids
    support_user_id: int | None = None
    support_user_ids: list[int] = []
    assistant_user_ids: list[int] = []
    member_user_ids: list[int] = []


class ProgressItemIn(BaseModel):
    project_id: int | None = None
    chain_id: str | None = None
    linked_task_id: int | None = None
    task_title: str = ""
    no_action: bool = False
    result_text: str = ""
    quant_value: str = ""
    stuck_status: str = "none"
    stuck_detail: str = ""
    stuck_next_action: str = ""
    stuck_follow_up: date | None = None
    stuck_need_who: str = ""
    linked_blocker_id: int | None = None
    next_goal: str = ""
    next_due_date: date | None = None
    risk_level: str = "normal"
    risk_reason: str = ""
    risk_impact_date: date | None = None
    risk_coordinator: str = ""
    prior_done: bool | None = None
    tags: list[str] = []
    sort_order: int = 0


class ProgressReportSave(BaseModel):
    period_type: str = "week"
    period_start: date
    period_end: date | None = None
    theme: str = ""
    items: list[ProgressItemIn]
    submit: bool = False
    # 修改历史已交周期时需显式确认，防止误写进上一周
    force_edit_submitted: bool = False


class ProgressReportAttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    report_id: int
    item_id: int | None = None
    original_filename: str
    mime_type: str = ""
    file_size: int = 0
    uploader_id: int
    uploader_name: str = ""
    created_at: datetime | None = None


class ProgressItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int | None = None
    project_name: str | None = None
    # 展示期推断（project_id 为空时用名称相似度猜测，不写库）
    inferred_project_id: int | None = None
    inferred_project_name: str | None = None
    project_inferred: bool = False
    member_role: str | None = None
    lead_name: str | None = None
    chain_id: str | None = None
    linked_task_id: int | None = None
    task_title: str = ""
    no_action: bool
    result_text: str
    quant_value: str
    stuck_status: str
    stuck_detail: str
    stuck_next_action: str
    stuck_follow_up: date | None = None
    stuck_need_who: str
    linked_blocker_id: int | None = None
    next_goal: str
    next_due_date: date | None = None
    risk_level: str
    risk_reason: str
    risk_impact_date: date | None = None
    risk_coordinator: str
    prior_done: bool | None = None
    tags: list[str] = []
    sort_order: int
    attachments: list[ProgressReportAttachmentOut] = []


class ProgressReportOut(BaseModel):
    id: int
    period_type: str
    period_start: date
    period_end: date | None = None
    theme: str
    status: str
    author: UserBrief
    items: list[ProgressItemOut]
    soft_warnings: list[str] = []
    submitted_at: datetime | None = None
    # 提交周报时同步「下周计划」到本人待办的条数
    next_plan_synced: int = 0
    # 提交周报时同步「做成了什么」为已完成任务（挂阶段）的条数
    done_synced: int = 0
    # 已提交时可附带本人一阶段评价（员工可读）
    phase1_review: Phase1ReviewOut | None = None
    review_unavailable_reason: str | None = None
    attachments: list[ProgressReportAttachmentOut] = []


class ProgressValidateOut(BaseModel):
    ok: bool
    hard_errors: list[str]
    soft_warnings: list[str]


class ProgressPrefillOut(BaseModel):
    period_type: str
    period_start: date
    period_end: date | None = None
    items: list[ProgressItemIn]
    prior_checks: list[dict] = []


class AiCoachRequest(BaseModel):
    period_type: str = "week"
    period_start: date
    period_end: date | None = None
    theme: str = ""
    items: list[ProgressItemIn]


class AiCoachOut(BaseModel):
    enabled: bool
    clarity_questions: list[str] = []
    risk_alerts: list[str] = []
    rewrite_suggestions: list[dict] = []
    missing_quant: list[str] = []
    message: str = ""


class AiStructureRequest(BaseModel):
    period_type: str = "week"
    period_start: date
    period_end: date | None = None
    chain_id: str | None = None
    item: ProgressItemIn


class StructuredFields(BaseModel):
    task_title: str = ""
    result_text: str = ""
    quant_value: str = ""
    stuck_status: str = "none"
    stuck_detail: str = ""
    stuck_next_action: str = ""
    stuck_follow_up: date | None = None
    stuck_need_who: str = ""
    next_goal: str = ""
    next_due_date: date | None = None
    risk_level: str = "normal"
    risk_reason: str = ""
    risk_impact_date: date | None = None
    risk_coordinator: str = ""
    tags: list[str] = []


class ProjectSuggestion(BaseModel):
    project_id: int
    name: str
    confidence: float = 0.0


class ChainSuggestion(BaseModel):
    chain_id: str
    title: str
    last_period: str | None = None
    reason: str = ""
    project_id: int | None = None
    seed_item_id: int | None = None


class TaskSuggestion(BaseModel):
    task_id: int
    title: str
    project_id: int


class AiStructureOut(BaseModel):
    enabled: bool
    structured: StructuredFields
    project_suggestion: ProjectSuggestion | None = None
    chain_suggestion: ChainSuggestion | None = None
    task_suggestion: TaskSuggestion | None = None
    create_new_chain: bool = True
    message: str = ""
    continuation_warning: str = ""


class ChainTimelineNode(BaseModel):
    item_id: int
    report_id: int
    period_type: str
    period_start: date
    period_end: date | None = None
    task_title: str = ""
    result_text: str = ""
    quant_value: str = ""
    stuck_status: str = "none"
    stuck_detail: str = ""
    next_goal: str = ""
    next_due_date: date | None = None
    project_id: int | None = None
    project_name: str | None = None
    linked_task_id: int | None = None
    author_name: str = ""


class ChainTimelineOut(BaseModel):
    chain_id: str
    title: str = ""
    project_id: int | None = None
    project_name: str | None = None
    linked_task_id: int | None = None
    nodes: list[ChainTimelineNode] = []


class ReportBoardPerson(BaseModel):
    user: UserBrief
    submitted: bool
    escalate_count: int = 0
    blocker_count: int = 0
    report_id: int | None = None
    # mode=full 时带全文；summary 为 None
    report: ProgressReportOut | None = None
    # 近 N 周提交考核摘要
    submitted_weeks: int = 0
    expected_weeks: int = 0
    submit_rate: float = 0.0


class ReportBoardProject(BaseModel):
    project_id: int
    project_name: str
    lead: UserBrief | None = None
    assistants: list[UserBrief] = []
    reports: list[ProgressReportOut] = []
    open_blockers: int = 0
    unacked_blockers: int = 0
    escalate_count: int = 0
    lead_aligned: bool = True


class ReportBoardPeriod(BaseModel):
    period_start: date
    period_end: date | None = None
    submitted_count: int = 0


class MyReportPeriod(BaseModel):
    """写周报页：本人已有草稿/已交的周期。"""
    period_start: date
    period_end: date | None = None
    status: str
    item_count: int = 0


class ReportBoardOut(BaseModel):
    period_type: str
    period_start: date
    period_end: date | None = None
    people: list[ReportBoardPerson]
    projects: list[ReportBoardProject]
    unassigned_items: list[dict] = []
    available_periods: list[ReportBoardPeriod] = []
    # 若请求的日期无数据，后端自动切到最近有提交的周期
    auto_switched: bool = False
    # summary | full
    mode: str = "summary"


class ProjectRelatedItemOut(ProgressItemOut):
    """项目详情关联周报条目：附带命中原因。"""

    # explicit / inferred / task_linked（可多选）
    match_reasons: list[str] = []


class ProjectRelatedReportOut(BaseModel):
    id: int
    period_type: str
    period_start: date
    period_end: date | None = None
    theme: str
    status: str
    author: UserBrief
    items: list[ProjectRelatedItemOut]
    submitted_at: datetime | None = None


class ProjectRelatedPeriodOut(BaseModel):
    period_start: date
    period_end: date | None = None
    reports: list[ProjectRelatedReportOut]


class ProjectRelatedReportsOut(BaseModel):
    project_id: int
    periods: list[ProjectRelatedPeriodOut]


class Phase1ReviewOut(BaseModel):
    id: int | None = None
    report_id: int | None = None
    phase: int = 1
    summary: str = ""
    strengths: list[str] = []
    risks: list[str] = []
    completion_comment: str = ""
    source: str = "rule"
    model_name: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PersonHistoryPeriod(BaseModel):
    period_start: date
    period_end: date | None = None
    status: str
    report_id: int | None = None
    item_count: int = 0
    escalate_count: int = 0
    blocker_count: int = 0
    prior_done_yes: int = 0
    prior_done_no: int = 0
    prior_done_total: int = 0


class PersonCompletionStats(BaseModel):
    submitted_weeks: int = 0
    expected_weeks: int = 0
    submit_rate: float = 0.0
    prior_done_yes: int = 0
    prior_done_total: int = 0
    prior_done_rate: float = 0.0


class PersonWorkbenchOut(BaseModel):
    user: UserBrief
    period_type: str
    period_start: date
    period_end: date | None = None
    submitted: bool
    report: ProgressReportOut | None = None
    history: list[PersonHistoryPeriod] = []
    completion_stats: PersonCompletionStats
    phase1_review: Phase1ReviewOut | None = None
    review_unavailable_reason: str | None = None


class SubmissionMatrixCell(BaseModel):
    user_id: int
    submitted: bool
    report_id: int | None = None


class SubmissionMatrixPeriod(BaseModel):
    period_start: date
    period_end: date | None = None
    cells: list[SubmissionMatrixCell]


class SubmissionMatrixOut(BaseModel):
    period_type: str
    weeks: int
    people: list[UserBrief]
    periods: list[SubmissionMatrixPeriod]


class TaskEventBrief(BaseModel):
    id: int
    action: str
    payload: dict | None = None
    actor_name: str = ""
    created_at: datetime | None = None


class PersonTimelineChain(BaseModel):
    chain_id: str
    title: str = ""
    project_id: int | None = None
    project_name: str | None = None
    linked_task_id: int | None = None
    progress_nodes: list[ChainTimelineNode] = []
    task_events: list[TaskEventBrief] = []


class PersonTimelinesOut(BaseModel):
    user_id: int
    period_start: date | None = None
    chains: list[PersonTimelineChain] = []


class LlmModelCreate(BaseModel):
    name: str
    provider: str = "openai_compatible"
    api_base: str
    api_key: str = ""
    model_name: str
    purpose: str = "report_coach"
    is_enabled: bool = True
    is_default: bool = False
    sort_order: int = 0


class LlmModelUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    api_base: str | None = None
    api_key: str | None = None  # None/空串表示不修改
    model_name: str | None = None
    purpose: str | None = None
    is_enabled: bool | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class LlmModelOut(BaseModel):
    id: int
    name: str
    provider: str
    api_base: str
    api_key_masked: str
    model_name: str
    purpose: str
    is_enabled: bool
    is_default: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class LlmModelTestOut(BaseModel):
    ok: bool
    message: str


class LlmModelDiscoverIn(BaseModel):
    api_base: str
    api_key: str | None = None
    model_id: int | None = None  # 编辑时可用库中已存密钥


class LlmModelDiscoverOut(BaseModel):
    ok: bool
    models: list[str] = []
    message: str = ""


class DshUsageUserSummary(BaseModel):
    username: str
    name: str | None = None
    open_count: int = 0
    last_open_at: str | None = None
    prompt_count: int = 0
    turns: int = 0
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    session_count: int = 0


class DshUsageSummaryOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    items: list[DshUsageUserSummary] = []


class DshUsageVisit(BaseModel):
    username: str
    name: str | None = None
    started_at: str
    ended_at: str | None = None
    duration_sec: int | None = None
    source: str = ""
    ip: str = ""


class DshUsageVisitsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    items: list[DshUsageVisit] = []


class DshUsageSession(BaseModel):
    username: str
    name: str | None = None
    session_id: str
    model: str = ""
    provider: str = ""
    turns: int = 0
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    last_prompt_at: str | None = None


class DshUsageSessionsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    items: list[DshUsageSession] = []


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    content: str
    is_read: bool
    created_at: datetime


class NotificationTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_key: str
    name: str
    description: str
    title_template: str
    body_template: str
    channel_inbox: bool
    channel_dingtalk: bool
    is_enabled: bool
    schedule_cron: str
    max_per_day: int
    quiet_start: str | None = None
    quiet_end: str | None = None
    updated_at: datetime


class NotificationTemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    title_template: str | None = None
    body_template: str | None = None
    channel_inbox: bool | None = None
    channel_dingtalk: bool | None = None
    is_enabled: bool | None = None
    schedule_cron: str | None = None
    max_per_day: int | None = None
    quiet_start: str | None = None  # "HH:MM" or "" to clear
    quiet_end: str | None = None


class NotificationDeliveryLogOut(BaseModel):
    id: int
    event_key: str
    template_id: int | None = None
    user_id: int | None = None
    user_name: str = ""
    channel: str
    title: str
    content: str
    status: str
    error_message: str = ""
    ref_type: str = ""
    ref_id: str = ""
    created_at: datetime


class NotificationDeliveryLogListOut(BaseModel):
    total: int
    items: list[NotificationDeliveryLogOut]


class SolutionTagOut(BaseModel):
    id: int
    name: str
    source: str


class SolutionOut(BaseModel):
    id: int
    title: str
    summary: str
    original_filename: str
    mime_type: str
    file_size: int
    uploader_id: int
    uploader_name: str = ""
    status: str
    error_message: str = ""
    tags: list[SolutionTagOut] = []
    created_at: datetime
    updated_at: datetime


class SolutionListOut(BaseModel):
    items: list[SolutionOut]
    total: int
    page: int
    page_size: int


class SolutionUpdate(BaseModel):
    title: str | None = None
    tags: list[str] | None = None  # 全量替换为手动标签列表（保留同名 ai 标签来源标记为 manual）


class SolutionTagStat(BaseModel):
    name: str
    count: int
