from app.services.report_ai_coach import (
    _clean_list,
    _clean_rewrites,
    _items_for_coach,
    _strip_tech_jargon,
    rule_based_coach,
)


def test_strip_tech_jargon():
    s = _strip_tech_jargon("result_text 为空，project_id 也没有")
    assert "result_text" not in s
    assert "project_id" not in s
    assert "做成了什么" in s or "所属项目" in s


def test_items_for_coach_uses_chinese_keys():
    rows = _items_for_coach(
        [
            {
                "task_title": "天网接入",
                "result_text": "新接入 10 路",
                "stuck_status": "none",
                "next_goal": "继续接入",
                "next_due_date": "2026-09-18",
                "tags": ["售中"],
            }
        ]
    )
    assert rows[0]["任务概述"] == "天网接入"
    assert "做成了什么" in rows[0]
    assert "project_id" not in rows[0]


def test_clean_list_drops_dev_jargon():
    out = _clean_list(["缺人天统计", "请补完成日期", "进入 UAT"])
    assert out == ["请补完成日期"]


def test_clean_rewrites_adds_label():
    out = _clean_rewrites(
        [{"item_index": 1, "field": "result_text", "suggestion": "完成勘察 3 处"}]
    )
    assert out[0]["field_label"] == "做成了什么"


def test_clean_rewrites_tags_vocab():
    out = _clean_rewrites(
        [{"item_index": 1, "field": "tags", "suggestion": "['设备到货']"}]
    )
    assert "售前" in out[0]["suggestion"]


def test_rule_based_empty_title():
    data = rule_based_coach("", [{"task_title": "", "result_text": "跟进中", "stuck_status": "none"}])
    assert any("任务概述" in x or "做成了什么" in x for x in data["clarity_questions"])


def test_extract_truncated_json_in_think_fence():
    from app.services.report_ai_coach import _extract_json_obj

    # 模拟 MiniMax：JSON 在 think + ```json 里且被截断
    text = (
        "<think>blah\n```json\n"
        "{\n"
        '  "clarity_questions": ["下周目标缺日期"],\n'
        '  "risk_alerts": ["流量包停机，建议勾有卡点"],\n'
        '  "missing_quant": ["受影响用户多少"],\n'
        '  "rewrite_suggestions": [\n'
        '    {"item_index": 1, "field": "result_text", '
        '"suggestion": "已排查停机并报障'
        "\n</think>\n"
    )
    obj = _extract_json_obj(text)
    assert "clarity_questions" in obj
    assert obj["risk_alerts"]
