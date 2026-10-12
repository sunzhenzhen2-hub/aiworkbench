from datetime import date
from types import SimpleNamespace

from app.services.report_ai_review import rule_based_review


def _item(**kwargs):
    defaults = {
        "task_title": "测试任务",
        "no_action": False,
        "result_text": "完成勘察 3 处",
        "quant_value": "3",
        "stuck_status": "none",
        "stuck_detail": "",
        "risk_level": "normal",
        "risk_reason": "",
        "prior_done": True,
        "next_goal": "继续",
        "next_due_date": date(2026, 9, 20),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_rule_based_review_happy_path():
    report = SimpleNamespace(
        items=[_item()],
        period_type="week",
        period_start=date(2026, 9, 8),
        period_end=date(2026, 9, 14),
        theme="",
    )
    out = rule_based_review(report)
    assert out["source"] == "rule"
    assert "提交" in out["summary"] or "任务" in out["summary"]
    assert out["strengths"]
    assert "兑现" in out["completion_comment"]


def test_rule_based_review_escalation():
    report = SimpleNamespace(
        items=[
            _item(
                result_text="跟进中",
                quant_value="",
                risk_level="escalate",
                risk_reason="缺协调",
                stuck_status="blocker",
                stuck_detail="等客户",
                prior_done=False,
            )
        ]
    )
    out = rule_based_review(report)
    assert "升级" in out["summary"] or out["risks"]
    assert any("升级" in x or "卡点" in x for x in out["risks"])
