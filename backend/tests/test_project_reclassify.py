"""智能归类：明显立项工作不进「日常任务」。"""

from app.services.project_match import (
    looks_like_daily_work,
    looks_like_named_project,
    suggest_project_name_from_title,
    guess_project_id,
    canonical_project_name,
)


def test_named_project_not_daily():
    assert looks_like_named_project("杭州市富阳区应急管理局一张图二期项目", "初验演示")
    assert not looks_like_daily_work("杭州市富阳区应急管理局一张图二期项目", "初验演示")


def test_invoice_is_daily():
    assert looks_like_daily_work("司机开票流程和电费发票报销流程申请", "")
    assert not looks_like_named_project("司机开票流程和电费发票报销流程申请", "")


def test_boilerplate_result_not_daily():
    # 「原周报导入」套话含「周报」字样，不能把专名误判成日常
    assert not looks_like_daily_work("水务大楼", "本周无实质动作（由原周报导入）")
    assert looks_like_named_project("水务大楼", "本周无实质动作（由原周报导入）")


def test_short_alias_not_daily():
    assert not looks_like_daily_work("家医", "")
    assert looks_like_named_project("家医", "")
    assert suggest_project_name_from_title("家医") == "家庭医生"


def test_suggest_strips_action_suffix():
    assert suggest_project_name_from_title("智安校园-实验中学请假系统学生信息录入") == "智安校园"


def test_split_zhian_campus():
    from app.services.project_match import split_project_and_task, guess_project_type_from_title

    p, t = split_project_and_task(
        "智安校园-实验中学请假系统学生信息录入及家长账号配置"
    )
    assert p == "智安校园"
    assert t and "实验中学" in t and "家长账号" in t
    assert guess_project_type_from_title(
        "智安校园-实验中学请假系统学生信息录入及家长账号配置"
    ) == "fusion"


def test_ops_type_poison():
    from app.services.project_match import guess_project_type_from_title, suggest_project_name_from_title

    assert guess_project_type_from_title("学校城域网终端中毒处理") == "integration"
    assert "城域网" in suggest_project_name_from_title("学校城域网终端中毒处理")


def test_software_type_hint():
    from app.services.project_match import guess_project_type_from_title

    assert guess_project_type_from_title("某某小程序软件开发") == "software"
    assert guess_project_type_from_title("普通业务项目") == "software"


def test_guess_skips_daily_name():
    projects = [
        {"project_id": 1, "name": "日常任务"},
        {"project_id": 2, "name": "天网接入专项-b48d5595"},
    ]
    pid, conf, name = guess_project_id(
        "天网工程社会面接入",
        "",
        projects,
        exclude_names={"日常任务"},
    )
    assert pid == 2
    assert conf >= 0.45
    assert canonical_project_name("天网接入专项-b48d5595") == "天网接入专项"
