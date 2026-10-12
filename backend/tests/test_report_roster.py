"""周报应提交固定名单。"""

from app.services.report_roster import REPORT_ROSTER_USERNAMES, is_report_roster_username


def test_roster_has_eleven():
    assert len(REPORT_ROSTER_USERNAMES) == 11


def test_roster_includes_qian_and_luo():
    assert is_report_roster_username("FY0395")
    assert is_report_roster_username("fy0395")
    assert is_report_roster_username("FY0383")
    assert not is_report_roster_username("admin")
    assert not is_report_roster_username("t_chain_a")
