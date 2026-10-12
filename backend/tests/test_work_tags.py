from app.services.work_tags import WORK_TAG_OPTIONS, normalize_work_tags


def test_normalize_work_tags_basic():
    assert normalize_work_tags(["售前", "客户对接", "售前"]) == ["售前", "客户对接"]
    assert normalize_work_tags("售前支持,运维保障") == ["售前", "运维"]
    assert normalize_work_tags(["瞎写", "项目管理"]) == ["项目管理"]
    assert set(WORK_TAG_OPTIONS) >= {"售前", "售后", "方案汇报"}


def test_guess_and_resolve_tags():
    from app.services.work_tags import guess_work_tags, resolve_item_tags

    assert "运维" in guess_work_tags("政务云巡检", "完成日常巡检")
    assert "售前" in guess_work_tags("合同签约", "完成签约")
    assert resolve_item_tags(["售前"]) == ["售前"]
    assert resolve_item_tags([], title="跟进采购流程") == ["流程跟进"]
