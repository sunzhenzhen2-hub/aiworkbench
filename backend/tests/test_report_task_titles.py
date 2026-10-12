"""周报同步待办：清晰标题拼装。"""

from app.services.report_next_plan import (
    compose_clear_task_title,
    should_skip_next_plan_item,
    _rule_normalize,
)


def test_compose_prefixes_overview_when_goal_differs():
    title = compose_clear_task_title(
        task_title="统建监控续租",
        next_goal="完善采购，确定最终采购金额",
        project_name="",
    )
    assert "统建监控续租" in title
    assert "完善采购" in title


def test_compose_adds_project_when_title_equals_goal():
    title = compose_clear_task_title(
        task_title="完善采购，确定最终采购金额",
        next_goal="完善采购，确定最终采购金额",
        project_name="2021年度统建监控租赁服务续租项目（600路）",
    )
    assert "统建监控" in title or "续租" in title
    assert "完善采购" in title


def test_compose_vague_continuation_uses_overview():
    title = compose_clear_task_title(
        task_title="零信任终端试点",
        next_goal="继续推进",
        project_name="",
    )
    assert "零信任" in title
    assert "继续推进" in title


def test_compose_soft_boilerplate_keeps_overview():
    title = compose_clear_task_title(
        task_title="保障视联网视频会议2场",
        next_goal="继续政务外网相关平台设备运维及故障处理。",
        project_name="富阳区电子政务网驻场运维",
    )
    assert "视联网" in title
    assert "继续政务外网相关平台" not in title


def test_compose_template_continuation_uses_result():
    title = compose_clear_task_title(
        task_title="售前标准化",
        next_goal="继续推进「售前标准化」相关工作",
        project_name="售前标准化",
        result_text=(
            "1、5g新通话网络调试已单路通，营业厅协助处理并发问题，已提交杭州，还未完全处理好\n"
            "2、一体化平台应急预案收集\n"
            "3、电子秤对接总公司，考虑教育场景的融合"
        ),
    )
    assert "售前标准化" in title
    assert "继续推进「售前标准化」相关工作" not in title
    assert "5g" in title.lower() or "应急" in title or "电子秤" in title


def test_rule_normalize_template_improves_goal():
    n = _rule_normalize(
        task_title="售前标准化",
        next_goal="继续推进「售前标准化」相关工作",
        next_due_date=None,
        project_name="售前标准化",
        result_text="1、5g新通话并发未完全处理好\n2、应急预案收集",
    )
    assert "继续推进「售前标准化」相关工作" not in n.success_criteria
    assert "跟进" in n.success_criteria or "5g" in n.success_criteria.lower()


def test_skip_placeholder_next():
    assert should_skip_next_plan_item(next_goal="原文未写下周计划", result_text="做了勘察")
    assert should_skip_next_plan_item(next_goal="下周", result_text="做了勘察")
    assert not should_skip_next_plan_item(
        next_goal="继续跟进热成像到货",
        result_text="本周完成点位勘察",
        task_title="热成像跟进",
    )


def test_skip_next_that_restates_done_result():
    assert should_skip_next_plan_item(
        next_goal="完成设备到货报验工作",
        result_text="完成设备报验工作",
        task_title="设备到货报验",
    )
    assert should_skip_next_plan_item(
        next_goal="完成单兵设备入网测试",
        result_text="测试已完成",
        task_title="单兵设备测试",
    )
    assert not should_skip_next_plan_item(
        next_goal="完成方案终稿并提交客户",
        result_text="初稿已出",
        task_title="方案汇报",
    )
