"""周报三问硬拦 / 软提示规则测试。"""

from datetime import date

from app.services.report_quality import validate_report_items


def _item(**kwargs):
    base = {
        "project_id": 1,
        "task_title": "点位勘察",
        "result_text": "完成点位勘察三处",
        "quant_value": "3",
        "stuck_status": "none",
        "stuck_detail": "",
        "stuck_next_action": "",
        "stuck_follow_up": None,
        "stuck_need_who": "",
        "next_goal": "到货后安装调试",
        "next_due_date": date(2026, 9, 12),
        "risk_level": "normal",
        "risk_reason": "",
        "risk_impact_date": None,
        "risk_coordinator": "",
        "no_action": False,
    }
    base.update(kwargs)
    return base


def test_empty_result_is_hard_error():
    ok, hard, soft = validate_report_items([_item(result_text="")])
    assert not ok
    assert any("做成了什么" in e for e in hard)


def test_empty_task_title_is_hard_error():
    ok, hard, soft = validate_report_items([_item(task_title="")])
    assert not ok
    assert any("任务概述" in e for e in hard)


def test_vague_result_is_hard_error():
    ok, hard, soft = validate_report_items([_item(result_text="跟进项目进度")])
    assert not ok
    assert any("空话" in e or "做成了什么" in e for e in hard)


def test_blocker_missing_follow_up_hard_error():
    ok, hard, soft = validate_report_items(
        [
            _item(
                stuck_status="blocker",
                stuck_detail="设备未到货",
                stuck_next_action="催厂商",
                stuck_follow_up=None,
            )
        ]
    )
    assert not ok
    assert any("跟进" in e for e in hard)


def test_escalate_missing_coordinator_hard_error():
    ok, hard, soft = validate_report_items(
        [
            _item(
                risk_level="escalate",
                risk_reason="可能超期",
                risk_impact_date=date(2026, 9, 30),
                risk_coordinator="",
            )
        ]
    )
    assert not ok
    assert any("协调" in e for e in hard)


def test_valid_minimal_item_passes():
    ok, hard, soft = validate_report_items([_item()])
    assert ok
    assert hard == []


def test_missing_quant_is_soft_only():
    ok, hard, soft = validate_report_items([_item(quant_value="")])
    assert ok
    assert hard == []
    assert any("量化" in e or "数字" in e for e in soft)


def test_no_action_skips_three_questions():
    ok, hard, soft = validate_report_items(
        [_item(no_action=True, result_text="", next_goal="", next_due_date=None)]
    )
    assert ok
    assert hard == []


def test_missing_project_id_is_hard_error():
    ok, hard, soft = validate_report_items([_item(project_id=None)])
    assert not ok
    assert any("所属项目" in e or "日常任务" in e for e in hard)


def test_no_action_still_needs_project():
    ok, hard, soft = validate_report_items(
        [_item(no_action=True, project_id=None, result_text="", next_goal="", next_due_date=None)]
    )
    assert not ok
    assert any("所属项目" in e or "日常任务" in e for e in hard)


def test_no_action_still_needs_task_title():
    ok, hard, soft = validate_report_items(
        [_item(no_action=True, task_title="", result_text="", next_goal="", next_due_date=None)]
    )
    assert not ok
    assert any("任务概述" in e for e in hard)


def test_suspected_risk_leak_soft_warning():
    ok, hard, soft = validate_report_items(
        [_item(result_text="本周平台整体进度滞后，已多次沟通", risk_level="normal")]
    )
    assert ok
    assert any("风险" in e or "漏报" in e for e in soft)
