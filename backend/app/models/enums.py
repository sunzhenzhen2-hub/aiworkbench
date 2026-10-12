import enum


class ProjectLevel(str, enum.Enum):
    """项目分级：普通 / 重点 / 日常。"""

    NORMAL = "normal"
    KEY = "key"
    DAILY = "daily"


class ProjectType(str, enum.Enum):
    SOFTWARE = "software"
    INTEGRATION = "integration"
    FUSION = "fusion"


class ProjectStatus(str, enum.Enum):
    NORMAL = "normal"
    AT_RISK = "at_risk"
    BLOCKED = "blocked"


class PhaseStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class BlockerStatus(str, enum.Enum):
    FOLLOWING = "following"
    PENDING_DECISION = "pending_decision"
    ESCALATED = "escalated"
    RESOLVED = "resolved"


class BlockerParty(str, enum.Enum):
    THIRD_PARTY = "third_party"
    CUSTOMER = "customer"
    INTERNAL = "internal"
    APPROVAL = "approval"


class TaskStatus(str, enum.Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TaskPriority(str, enum.Enum):
    """任务级别 P0（最高）～ P4（最低）；P0 创建/调升会推送责任人。"""

    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    P4 = "p4"

class MemberRole(str, enum.Enum):
    LEAD = "lead"
    ASSISTANT = "assistant"
    MEMBER = "member"


class ReportPeriod(str, enum.Enum):
    WEEK = "week"
    DAY = "day"


class StuckStatus(str, enum.Enum):
    NONE = "none"
    BLOCKER = "blocker"
    COORDINATION = "coordination"


class RiskLevel(str, enum.Enum):
    NORMAL = "normal"
    WATCH = "watch"
    ESCALATE = "escalate"
