"""项目详情：关联周报条目判定（展示期，不写库）。"""

from __future__ import annotations

from typing import Any

from app.services.project_match import guess_project_id
from app.services.project_service import DAILY_PROJECT_NAME

# 关联原因（可多选）
REASON_EXPLICIT = "explicit"
REASON_INFERRED = "inferred"
REASON_TASK_LINKED = "task_linked"


def match_item_to_project(
    *,
    item_project_id: int | None,
    task_title: str,
    result_text: str,
    linked_task_project_id: int | None,
    target_project_id: int,
    daily_project_id: int | None,
    project_guess_list: list[dict[str, Any]],
) -> list[str]:
    """
    判断周报条目是否关联到目标项目，返回命中原因列表。

    - explicit：条目已挂该项目
    - inferred：空项目或「日常任务」时展示期推断命中（不写库）
    - task_linked：关联待办任务属于该项目

    已显式挂在其它真实立项上的条目：不因推断覆盖；任务关联仍可命中。
    """
    reasons: list[str] = []

    if item_project_id == target_project_id:
        reasons.append(REASON_EXPLICIT)

    if linked_task_project_id is not None and linked_task_project_id == target_project_id:
        reasons.append(REASON_TASK_LINKED)

    can_infer = item_project_id is None or (
        daily_project_id is not None and item_project_id == daily_project_id
    )
    if can_infer:
        guessed, _conf, _name = guess_project_id(
            task_title or "",
            result_text or "",
            project_guess_list,
            exclude_names={DAILY_PROJECT_NAME},
        )
        if guessed == target_project_id and REASON_INFERRED not in reasons:
            # 已挂本项目时不必再标推断
            if REASON_EXPLICIT not in reasons:
                reasons.append(REASON_INFERRED)

    return reasons
