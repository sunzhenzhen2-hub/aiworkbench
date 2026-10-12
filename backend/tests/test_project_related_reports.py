"""项目关联周报：三种命中与「其它立项不误推」。"""

from app.services.project_related_reports import (
    REASON_EXPLICIT,
    REASON_INFERRED,
    REASON_TASK_LINKED,
    match_item_to_project,
)

TARGET = 10
OTHER = 20
DAILY = 99

GUESS_LIST = [
    {"project_id": TARGET, "name": "智安校园电子学生证项目-实验中学"},
    {"project_id": OTHER, "name": "政务外网运维项目"},
    {"project_id": DAILY, "name": "日常任务"},
]


def test_explicit_match():
    reasons = match_item_to_project(
        item_project_id=TARGET,
        task_title="随便写点",
        result_text="",
        linked_task_project_id=None,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert REASON_EXPLICIT in reasons
    assert REASON_INFERRED not in reasons


def test_inferred_from_null_project():
    reasons = match_item_to_project(
        item_project_id=None,
        task_title="智安校园电子学生证项目推进",
        result_text="完成实验中学现场调研",
        linked_task_project_id=None,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert reasons == [REASON_INFERRED]


def test_inferred_from_daily_project():
    reasons = match_item_to_project(
        item_project_id=DAILY,
        task_title="智安校园电子学生证联调",
        result_text="实验中学发卡测试",
        linked_task_project_id=None,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert REASON_INFERRED in reasons
    assert REASON_EXPLICIT not in reasons


def test_task_linked_match():
    reasons = match_item_to_project(
        item_project_id=OTHER,
        task_title="无关标题",
        result_text="",
        linked_task_project_id=TARGET,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert reasons == [REASON_TASK_LINKED]


def test_other_real_project_not_inferred():
    """已挂其它真实立项时，不因文案相似误推到目标项目。"""
    reasons = match_item_to_project(
        item_project_id=OTHER,
        task_title="智安校园电子学生证项目-实验中学推进",
        result_text="实验中学发卡",
        linked_task_project_id=None,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert reasons == []


def test_explicit_and_task_linked_both():
    reasons = match_item_to_project(
        item_project_id=TARGET,
        task_title="推进",
        result_text="",
        linked_task_project_id=TARGET,
        target_project_id=TARGET,
        daily_project_id=DAILY,
        project_guess_list=GUESS_LIST,
    )
    assert REASON_EXPLICIT in reasons
    assert REASON_TASK_LINKED in reasons
