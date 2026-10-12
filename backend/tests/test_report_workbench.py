"""工作台 API：board summary、matrix、workbench、timelines、ai-review。"""

from datetime import date as date_cls

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.api.progress_reports import _dedupe_report_items
from app.models import ProgressReport, ProgressReportItem, ProgressReportReview, Role, User, UserRole
from app.schemas import ProgressItemIn
from app.services.init_service import init_rbac
from app.services.report_roster import REPORT_ROSTER_USERNAMES


async def _ensure_mgr_and_roster_user():
    async with async_session() as db:
        await init_rbac(db)
        r = await db.execute(select(User).where(User.username == "wb_mgr"))
        mgr = r.scalar_one_or_none()
        if not mgr:
            mgr = User(
                username="wb_mgr",
                name="工作台经理",
                hashed_password=hash_password("TestPass2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(mgr)
            await db.flush()
        else:
            mgr.hashed_password = hash_password("TestPass2026")
            mgr.must_change_password = False
        role = (await db.execute(select(Role).where(Role.code == "dept_head"))).scalar_one()
        ur = await db.execute(
            select(UserRole).where(UserRole.user_id == mgr.id, UserRole.role_id == role.id)
        )
        if not ur.scalar_one_or_none():
            db.add(UserRole(user_id=mgr.id, role_id=role.id))

        uname = REPORT_ROSTER_USERNAMES[0]
        r2 = await db.execute(select(User).where(User.username == uname))
        member = r2.scalar_one_or_none()
        if not member:
            member = User(
                username=uname,
                name="Roster甲",
                hashed_password=hash_password("TestPass2026"),
                must_change_password=False,
                is_active=True,
            )
            db.add(member)
            await db.flush()
        await db.commit()
        return mgr.id, member.id, uname


async def _login(client: AsyncClient, username: str) -> str:
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": "TestPass2026"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_board_summary_and_workbench_review():
    _mgr_id, member_id, _uname = await _ensure_mgr_and_roster_user()
    period_start = date_cls(2099, 1, 5)
    period_end = date_cls(2099, 1, 11)
    ps = period_start.isoformat()

    async with async_session() as db:
        old = await db.execute(
            select(ProgressReport).where(
                ProgressReport.user_id == member_id,
                ProgressReport.period_start == period_start,
            )
        )
        for r in old.scalars().all():
            await db.delete(r)
        await db.commit()

        report = ProgressReport(
            user_id=member_id,
            period_type="week",
            period_start=period_start,
            period_end=period_end,
            theme="联调",
            status="submitted",
        )
        db.add(report)
        await db.flush()
        db.add(
            ProgressReportItem(
                report_id=report.id,
                task_title="天网接入",
                result_text="新接入 10 路",
                quant_value="10",
                stuck_status="none",
                next_goal="继续接入",
                next_due_date=date_cls(2099, 1, 18),
                risk_level="normal",
                prior_done=True,
                chain_id="chain-wb-test-001",
            )
        )
        await db.commit()
        report_id = report.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "wb_mgr")
        headers = {"Authorization": f"Bearer {token}"}

        board = await client.get(
            "/api/progress-reports/board",
            headers=headers,
            params={"period_type": "week", "period_start": ps, "mode": "summary"},
        )
        assert board.status_code == 200, board.text
        data = board.json()
        assert data["mode"] == "summary"
        person = next(p for p in data["people"] if p["user"]["id"] == member_id)
        assert person["submitted"] is True
        assert person["report"] is None
        assert "submit_rate" in person

        matrix = await client.get(
            "/api/progress-reports/submission-matrix",
            headers=headers,
            params={"weeks": 4, "anchor": ps},
        )
        assert matrix.status_code == 200, matrix.text
        assert matrix.json()["people"]

        wb = await client.get(
            f"/api/progress-reports/users/{member_id}/workbench",
            headers=headers,
            params={"period_type": "week", "period_start": ps},
        )
        assert wb.status_code == 200, wb.text
        body = wb.json()
        assert body["submitted"] is True
        assert body["report"] is not None
        assert body["phase1_review"] is not None
        assert body["phase1_review"]["summary"]

        async with async_session() as db:
            cnt = (
                await db.execute(
                    select(ProgressReportReview).where(
                        ProgressReportReview.report_id == report_id,
                        ProgressReportReview.phase == 1,
                    )
                )
            ).scalars().all()
            assert len(cnt) == 1
            first_id = cnt[0].id

        wb2 = await client.get(
            f"/api/progress-reports/users/{member_id}/workbench",
            headers=headers,
            params={"period_type": "week", "period_start": ps},
        )
        assert wb2.json()["phase1_review"]["id"] == first_id

        tl = await client.get(
            f"/api/progress-reports/users/{member_id}/timelines",
            headers=headers,
            params={"period_type": "week", "period_start": ps},
        )
        assert tl.status_code == 200, tl.text
        chains = tl.json()["chains"]
        assert any(c["chain_id"] == "chain-wb-test-001" for c in chains)

        async with async_session() as db:
            other = (
                await db.execute(select(User).where(User.username == REPORT_ROSTER_USERNAMES[1]))
            ).scalar_one_or_none()
            if not other:
                other = User(
                    username=REPORT_ROSTER_USERNAMES[1],
                    name="Roster乙",
                    hashed_password=hash_password("TestPass2026"),
                    must_change_password=False,
                    is_active=True,
                )
                db.add(other)
                await db.commit()
                await db.refresh(other)
            oid = other.id

        wb_miss = await client.get(
            f"/api/progress-reports/users/{oid}/workbench",
            headers=headers,
            params={"period_type": "week", "period_start": ps},
        )
        assert wb_miss.status_code == 200
        miss = wb_miss.json()
        assert miss["submitted"] is False
        assert miss["phase1_review"] is None
        assert miss["review_unavailable_reason"]


@pytest.mark.asyncio
async def test_board_available_periods_include_empty_weeks():
    """切换周期从 9 月 1 日起（含无提交周）；显式点选空周不会被自动跳走。"""
    from datetime import timedelta

    from app.api.progress_reports import _period_floor
    from app.services.report_quality import monday_of

    await _ensure_mgr_and_roster_user()
    this_monday = monday_of(date_cls.today())
    floor = _period_floor(date_cls.today())
    # 很远的空周：验证显式 period_start 不被 auto_switch
    far_empty = this_monday - timedelta(days=7 * 40)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        token = await _login(client, "wb_mgr")
        headers = {"Authorization": f"Bearer {token}"}

        board = await client.get(
            "/api/progress-reports/board",
            headers=headers,
            params={"period_type": "week", "mode": "summary", "history_weeks": 12},
        )
        assert board.status_code == 200, board.text
        periods = board.json().get("available_periods") or []
        assert periods
        # 最早一周应覆盖 9/1 所在周（周一可能早于 9/1）
        oldest = min(p["period_start"] for p in periods)
        assert oldest >= monday_of(floor).isoformat()
        assert any(p["period_start"] >= floor.isoformat() or p.get("period_end", "") >= floor.isoformat() for p in periods)

        empty = next((p for p in periods if int(p.get("submitted_count") or 0) == 0), None)
        if empty:
            stuck = await client.get(
                "/api/progress-reports/board",
                headers=headers,
                params={
                    "period_type": "week",
                    "period_start": empty["period_start"],
                    "mode": "summary",
                    "history_weeks": 12,
                },
            )
            assert stuck.status_code == 200, stuck.text
            body = stuck.json()
            assert body.get("auto_switched") is False
            assert body["period_start"] == empty["period_start"]

        far = await client.get(
            "/api/progress-reports/board",
            headers=headers,
            params={
                "period_type": "week",
                "period_start": far_empty.isoformat(),
                "mode": "summary",
            },
        )
        assert far.status_code == 200, far.text
        assert far.json().get("auto_switched") is False
        assert far.json()["period_start"] == far_empty.isoformat()


def test_dedupe_report_items_keeps_one_copy():
    same = dict(task_title="培训", result_text="做了一次", next_goal="暂无", sort_order=3, project_id=1)
    other = dict(task_title="另一件事", result_text="别的结果", next_goal="明天", sort_order=4, project_id=2)
    out = _dedupe_report_items([
        ProgressItemIn(**same),
        ProgressItemIn(**same),
        ProgressItemIn(**other),
    ])
    assert [i.task_title for i in out] == ["培训", "另一件事"]
