"""项目名称相似度归类单元测试。"""

from app.services.project_match import guess_project_id, score_project_match


def test_guess_by_substring_title():
    projects = [
        {"project_id": 1, "name": "天网项目"},
        {"project_id": 2, "name": "家庭医生"},
    ]
    pid, conf, name = guess_project_id("天网接入联调", "", projects)
    assert pid == 1
    assert name == "天网项目"
    assert conf >= 0.45


def test_guess_below_threshold_is_none():
    projects = [{"project_id": 1, "name": "天网项目"}]
    pid, conf, name = guess_project_id("完全无关的事项", "写了几份材料", projects)
    assert pid is None
    assert conf == 0.0
    assert name is None


def test_score_shared_bigram():
    assert score_project_match("天网接入", "", "天网项目") >= 0.45


def test_context_classifies_by_related_task_keywords():
    from app.services.project_match import guess_project_with_context

    projects = [
        {"project_id": 1, "name": "智安校园"},
        {"project_id": 2, "name": "家庭医生"},
    ]
    context = [
        {"project_id": 1, "text": "智安校园请假系统学生信息录入"},
        {"project_id": 2, "text": "家庭医生随访签约"},
    ]
    pid, conf, name, reason = guess_project_with_context(
        "请假系统家长账号配置",
        "",
        projects,
        context,
        exclude_names={"日常任务"},
    )
    assert pid == 1
    assert name == "智安校园"
    assert conf >= 0.55
    assert "上下文" in reason


def test_context_does_not_classify_unrelated_daily():
    from app.services.project_match import guess_project_with_context

    projects = [{"project_id": 1, "name": "智安校园"}]
    context = [{"project_id": 1, "text": "智安校园请假系统学生信息录入"}]
    pid, conf, _name, _reason = guess_project_with_context(
        "司机开票报销",
        "",
        projects,
        context,
        exclude_names={"日常任务"},
    )
    assert pid is None
    assert conf == 0.0
