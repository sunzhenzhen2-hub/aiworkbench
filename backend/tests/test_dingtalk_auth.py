"""钉钉免登、后台授权与任务状态。钉钉接口用假数据，不连外网。"""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.api.auth import _make_bind_ticket
from app.core.database import async_session
from app.core.security import hash_password
from app.main import app
from app.models import AuditLog, Permission, Project, Role, RolePermission, Task, User, UserRole
from app.services import dingtalk as dingtalk_mod
from app.services.init_service import init_rbac

PASSWORD = "DingBind2026"


def _jit_profile(userid: str, job: str, name: str = "测试同事") -> dict:
    return {"userid": userid, "name": name, "job_number": job, "title": ""}


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_user(
    *,
    role_codes: list[str],
    dingtalk_user_id: str | None = None,
    is_active: bool = True,
) -> User:
    username = f"dt_{uuid4().hex[:10]}"
    async with async_session() as db:
        user = User(
            username=username,
            name=username,
            hashed_password=hash_password(PASSWORD),
            must_change_password=False,
            dingtalk_user_id=dingtalk_user_id,
            is_active=is_active,
        )
        db.add(user)
        await db.flush()
        roles = (await db.execute(select(Role).where(Role.code.in_(role_codes)))).scalars().all()
        for role in roles:
            db.add(UserRole(user_id=user.id, role_id=role.id))
        await db.commit()
        await db.refresh(user)
        return user


def _stub_dingtalk(monkeypatch, userid: str, job: str, *, oauth: bool = False) -> None:
    if oauth:
        monkeypatch.setattr(
            "app.services.dingtalk.resolve_userid_by_oauth",
            AsyncMock(return_value=userid),
        )
    else:
        monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, job)),
    )


async def _delete_user_named(username: str) -> None:
    async with async_session() as db:
        uid = select(User.id).where(User.username == username)
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(uid)))
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(select(User.id).where(User.username == username))))
        await db.execute(delete(User).where(User.username == username))
        await db.commit()


async def _login(client: AsyncClient, username: str) -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_dingtalk_sso_succeeds_when_bound_and_authorized(monkeypatch):
    userid = f"dd_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"], dingtalk_user_id=userid)
    _stub_dingtalk(monkeypatch, userid, user.username)
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "fake-code"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["access_token"]
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["id"] == user.id


@pytest.mark.asyncio
async def test_dingtalk_sso_rejects_bound_user_without_permission(monkeypatch):
    userid = f"dd_{uuid4().hex[:8]}"
    # 无角色 = 无 dingtalk.login（admin/member 等默认角色已含该权限）
    user = await _make_user(role_codes=[], dingtalk_user_id=userid)
    _stub_dingtalk(monkeypatch, userid, user.username)
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "fake-code"})
        assert resp.status_code == 403
        assert "开通" in resp.json()["detail"] or "授权" in resp.json()["detail"]


@pytest.fixture(autouse=True)
def _noop_ensure_home(monkeypatch):
    monkeypatch.setattr("app.api.auth.try_ensure_dsh_home", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_dingtalk_unbound_creates_user_by_job_number(monkeypatch):
    userid = f"dd_new_{uuid4().hex[:8]}"
    job = f"t_jit_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, job)),
    )
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "fake-code"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["username"] == job
    async with async_session() as db:
        uid = select(User.id).where(User.username == job)
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(uid)))
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(select(User.id).where(User.username == job))))
        await db.execute(delete(User).where(User.username == job))
        await db.commit()


@pytest.mark.asyncio
async def test_dingtalk_jit_moves_off_system_account(monkeypatch):
    userid = f"dd_sys_{uuid4().hex[:8]}"
    sys_user = await _make_user(role_codes=["admin"], dingtalk_user_id=userid)
    job = f"t_fy_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.services.dingtalk_jit.SYSTEM_USERNAMES", {sys_user.username})
    _stub_dingtalk(monkeypatch, userid, job)
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "from-admin"})
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
        )
        assert me.json()["username"] == job
        assert me.json()["id"] != sys_user.id
    async with async_session() as db:
        loaded = (await db.execute(select(User).where(User.id == sys_user.id))).scalar_one()
        assert loaded.dingtalk_user_id is None
    await _delete_user_named(job)


@pytest.mark.asyncio
async def test_dingtalk_bind_requires_permission(monkeypatch):
    userid = f"dd_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=[])
    ticket = _make_bind_ticket(userid)
    async with _client() as client:
        resp = await client.post(
            "/api/auth/dingtalk/bind",
            json={"bind_ticket": ticket, "username": user.username, "password": PASSWORD},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dingtalk_bind_succeeds_with_permission(monkeypatch):
    userid = f"dd_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"])
    ticket = _make_bind_ticket(userid)
    async with _client() as client:
        resp = await client.post(
            "/api/auth/dingtalk/bind",
            json={"bind_ticket": ticket, "username": user.username, "password": PASSWORD},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"
        assert resp.json()["access_token"]

    async with async_session() as db:
        loaded = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
        assert loaded.dingtalk_user_id == userid


@pytest.mark.asyncio
async def test_admin_can_bind_and_unbind_dingtalk():
    admin = await _make_user(role_codes=["admin"])
    target = await _make_user(role_codes=["member"])
    userid = f"dd_admin_{uuid4().hex[:8]}"
    async with _client() as client:
        token = await _login(client, admin.username)
        headers = {"Authorization": f"Bearer {token}"}
        bind = await client.patch(
            f"/api/admin/users/{target.id}/dingtalk",
            json={"dingtalk_user_id": userid},
            headers=headers,
        )
        assert bind.status_code == 200, bind.text
        users = await client.get("/api/admin/users", headers=headers)
        row = next(u for u in users.json() if u["id"] == target.id)
        assert row["dingtalk_bound"] is True
        assert row["dingtalk_user_id"] == userid
        unbind = await client.patch(
            f"/api/admin/users/{target.id}/dingtalk",
            json={"dingtalk_user_id": None},
            headers=headers,
        )
        assert unbind.status_code == 200
        users = await client.get("/api/admin/users", headers=headers)
        row = next(u for u in users.json() if u["id"] == target.id)
        assert row["dingtalk_bound"] is False


@pytest.mark.asyncio
async def test_init_rbac_keeps_custom_role_permissions():
    async with async_session() as db:
        await init_rbac(db)
        role = (await db.execute(select(Role).where(Role.code == "member"))).scalar_one()
        perm = (await db.execute(select(Permission).where(Permission.code == "blocker.edit"))).scalar_one()
        existing = await db.execute(
            select(RolePermission).where(
                RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(RolePermission(role_id=role.id, permission_id=perm.id))
            await db.commit()
        await db.execute(
            delete(RolePermission).where(
                RolePermission.role_id == role.id, RolePermission.permission_id == perm.id
            )
        )
        await db.commit()
        role_id, perm_id = role.id, perm.id

    try:
        async with async_session() as db:
            await init_rbac(db)
            dt_perm = (
                await db.execute(select(Permission).where(Permission.code == "dingtalk.login"))
            ).scalar_one()
            leftover = await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role_id, RolePermission.permission_id == perm_id
                )
            )
            granted = await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role_id, RolePermission.permission_id == dt_perm.id
                )
            )
            assert leftover.scalar_one_or_none() is None
            assert granted.scalar_one_or_none() is not None
    finally:
        async with async_session() as db:
            still = await db.execute(
                select(RolePermission).where(
                    RolePermission.role_id == role_id, RolePermission.permission_id == perm_id
                )
            )
            if still.scalar_one_or_none() is None:
                db.add(RolePermission(role_id=role_id, permission_id=perm_id))
                await db.commit()



@pytest.mark.asyncio
async def test_assignee_can_update_own_task_status():
    user = await _make_user(role_codes=["member"])
    async with async_session() as db:
        project = Project(
            name=f"task-proj-{uuid4().hex[:6]}",
            project_type="other",
            owner_id=user.id,
        )
        db.add(project)
        await db.flush()
        task = Task(project_id=project.id, assignee_id=user.id, title="跟进材料", status="not_started")
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    async with _client() as client:
        token = await _login(client, user.username)
        resp = await client.patch(
            f"/api/personal/tasks/{task_id}",
            json={"status": "completed", "result_text": "材料已整理归档"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_cannot_update_others_task_status():
    owner = await _make_user(role_codes=["member"])
    other = await _make_user(role_codes=["member"])
    async with async_session() as db:
        project = Project(
            name=f"task-proj-{uuid4().hex[:6]}",
            project_type="other",
            owner_id=owner.id,
        )
        db.add(project)
        await db.flush()
        task = Task(project_id=project.id, assignee_id=owner.id, title="别人的任务")
        db.add(task)
        await db.commit()
        await db.refresh(task)
        task_id = task.id

    async with _client() as client:
        token = await _login(client, other.username)
        resp = await client.patch(
            f"/api/personal/tasks/{task_id}",
            json={"status": "completed"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403


# ---------- PC 钉钉扫码（OAuth / DTFrameLogin）----------


@pytest.fixture(autouse=True)
def _clear_scan_cache():
    from app.api import auth as auth_api
    from app.services.rate_limit import dingtalk_config_limiter, dingtalk_scan_limiter

    auth_api._scan_result_cache.clear()
    auth_api._consumed_bind_jtis.clear()
    dingtalk_config_limiter.clear()
    dingtalk_scan_limiter.clear()
    yield
    auth_api._scan_result_cache.clear()
    auth_api._consumed_bind_jtis.clear()
    dingtalk_config_limiter.clear()
    dingtalk_scan_limiter.clear()


@pytest.mark.asyncio
async def test_dingtalk_config_returns_state(monkeypatch):
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr("app.api.auth.settings.dingtalk_corp_id", "corp_test")
    async with _client() as client:
        resp = await client.get("/api/auth/dingtalk-config")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["client_id"] == "ding_test_app"
        assert body["state"]
        assert "dingtalk-callback?embed=1" in body["redirect_uri"]
        assert body["scope"] == "openid"


@pytest.mark.asyncio
async def test_dingtalk_scan_succeeds_when_bound(monkeypatch):
    userid = f"dd_scan_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"], dingtalk_user_id=userid)
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    _stub_dingtalk(monkeypatch, userid, user.username, oauth=True)
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-1", "state": cfg["state"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["access_token"]
        me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        assert me.json()["id"] == user.id


@pytest.mark.asyncio
async def test_dingtalk_scan_creates_user_by_job_number(monkeypatch):
    userid = f"dd_new_scan_{uuid4().hex[:8]}"
    job = f"t_jits_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(
        "app.services.dingtalk.resolve_userid_by_oauth",
        AsyncMock(return_value=userid),
    )
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, job)),
    )
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-bind", "state": cfg["state"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["username"] == job
    async with async_session() as db:
        uid = select(User.id).where(User.username == job)
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(uid)))
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(select(User.id).where(User.username == job))))
        await db.execute(delete(User).where(User.username == job))
        await db.commit()


@pytest.mark.asyncio
async def test_dingtalk_scan_rejects_without_permission(monkeypatch):
    userid = f"dd_noperm_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=[], dingtalk_user_id=userid)
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    _stub_dingtalk(monkeypatch, userid, user.username, oauth=True)
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-403", "state": cfg["state"]},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_dingtalk_scan_rejects_invalid_state(monkeypatch):
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(
        "app.services.dingtalk.resolve_userid_by_oauth",
        AsyncMock(return_value="unused"),
    )
    async with _client() as client:
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-bad", "state": "not-a-real-state"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_dingtalk_scan_timeout_returns_502(monkeypatch):
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(
        "app.services.dingtalk.resolve_userid_by_oauth",
        AsyncMock(side_effect=httpx.ConnectTimeout("dingtalk-oauth")),
    )
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-timeout", "state": cfg["state"]},
        )
    assert resp.status_code == 502
    assert "刷新二维码" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_scan_profile_timeout_returns_502(monkeypatch):
    userid = f"dd_to_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(
        "app.services.dingtalk.resolve_userid_by_oauth",
        AsyncMock(return_value=userid),
    )
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(side_effect=dingtalk_mod.DingTalkError(dingtalk_mod.NETWORK_MSG, 502)),
    )
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        resp = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-profile-timeout", "state": cfg["state"]},
        )
    assert resp.status_code == 502
    assert "连不上钉钉" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_scan_double_trigger_uses_cache(monkeypatch):
    userid = f"dd_cache_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"], dingtalk_user_id=userid)
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    resolve = AsyncMock(return_value=userid)
    monkeypatch.setattr("app.services.dingtalk.resolve_userid_by_oauth", resolve)
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, user.username)),
    )
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        payload = {"auth_code": "oauth-code-dup", "state": cfg["state"]}
        first = await client.post("/api/auth/dingtalk-scan", json=payload)
        assert first.status_code == 200, first.text
        second = await client.post("/api/auth/dingtalk-scan", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["access_token"] == first.json()["access_token"]
        assert resolve.await_count == 1


@pytest.mark.asyncio
async def test_dingtalk_scan_cache_rejects_attacker_without_matching_state(monkeypatch):
    """对抗：仅截获 auth_code、没有正确 state 时，不得从缓存偷走 JWT。"""
    userid = f"dd_steal_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"], dingtalk_user_id=userid)
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    _stub_dingtalk(monkeypatch, userid, user.username, oauth=True)
    async with _client() as client:
        cfg = (await client.get("/api/auth/dingtalk-config")).json()
        first = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-steal", "state": cfg["state"]},
        )
        assert first.status_code == 200
        assert first.json()["access_token"]
        # 攻击者只有 auth_code
        stolen = await client.post(
            "/api/auth/dingtalk-scan",
            json={"auth_code": "oauth-code-steal", "state": "forged-state"},
        )
        assert stolen.status_code == 400
        assert "state" in stolen.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_config_ignores_x_forwarded_host(monkeypatch):
    """对抗：客户端注入 X-Forwarded-Host 不得污染 redirect_uri。"""
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr("app.api.auth.settings.dingtalk_oauth_public_origin", "")
    async with _client() as client:
        resp = await client.get(
            "/api/auth/dingtalk-config",
            headers={
                "Host": "30.251.255.64",
                "X-Forwarded-Host": "evil.example",
                "X-Forwarded-Proto": "http",
            },
        )
        assert resp.status_code == 200, resp.text
        uri = resp.json()["redirect_uri"]
        assert "evil.example" not in uri
        assert uri.startswith("http://30.251.255.64/")


@pytest.mark.asyncio
async def test_dingtalk_config_prefers_origin_over_internal_host(monkeypatch):
    """VPN：浏览器 Origin 优先于内网 Host，避免 HTTPS 页跳到 HTTP 回调。"""
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr("app.api.auth.settings.dingtalk_oauth_public_origin", "")
    async with _client() as client:
        resp = await client.get(
            "/api/auth/dingtalk-config",
            headers={
                "Host": "30.251.255.64",
                "X-Forwarded-Proto": "http",
                "Origin": "https://app.fywasu.com:18090",
            },
        )
        assert resp.status_code == 200, resp.text
        uri = resp.json()["redirect_uri"]
        assert uri.startswith("https://app.fywasu.com:18090/")
        assert "dingtalk-callback?embed=1" in uri


@pytest.mark.asyncio
async def test_dingtalk_config_public_origin_overrides_host(monkeypatch):
    """配置 DINGTALK_OAUTH_PUBLIC_ORIGIN 时固定对外回调源。"""
    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(
        "app.api.auth.settings.dingtalk_oauth_public_origin",
        "https://app.fywasu.com:18090",
    )
    async with _client() as client:
        resp = await client.get(
            "/api/auth/dingtalk-config",
            headers={"Host": "30.251.255.64", "X-Forwarded-Proto": "http"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["redirect_uri"].startswith("https://app.fywasu.com:18090/")


@pytest.mark.asyncio
async def test_dingtalk_config_rate_limited(monkeypatch):
    from app.services.rate_limit import SlidingWindowLimiter
    import app.api.auth as auth_api

    monkeypatch.setattr("app.api.auth.settings.dingtalk_app_key", "ding_test_app")
    monkeypatch.setattr(auth_api, "dingtalk_config_limiter", SlidingWindowLimiter(3, 60))
    async with _client() as client:
        codes = []
        for _ in range(4):
            codes.append((await client.get("/api/auth/dingtalk-config")).status_code)
        assert codes[:3] == [200, 200, 200]
        assert codes[3] == 429


@pytest.mark.asyncio
async def test_dingtalk_bind_ticket_is_one_time(monkeypatch):
    userid = f"dd_once_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"])
    ticket = _make_bind_ticket(userid)
    async with _client() as client:
        first = await client.post(
            "/api/auth/dingtalk/bind",
            json={"bind_ticket": ticket, "username": user.username, "password": PASSWORD},
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            "/api/auth/dingtalk/bind",
            json={"bind_ticket": ticket, "username": user.username, "password": PASSWORD},
        )
        assert second.status_code == 400


@pytest.mark.asyncio
async def test_dingtalk_callback_embed_html():
    async with _client() as client:
        resp = await client.get(
            "/api/auth/dingtalk-callback",
            params={"embed": "1", "authCode": "abc", "state": "s1"},
        )
        assert resp.status_code == 200
        assert "dingtalk-oauth" in resp.text
        assert "abc" in resp.text or "authCode" in resp.text


@pytest.mark.asyncio
async def test_dingtalk_jit_creates_user_by_job_number(monkeypatch):
    userid = f"dd_jit_{uuid4().hex[:8]}"
    job = f"t_jit_{uuid4().hex[:8]}"
    homes: list[str] = []
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, job)),
    )
    monkeypatch.setattr("app.api.auth.try_ensure_dsh_home", lambda username: homes.append(username))
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "jit-code"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        payload = me.json()
        assert payload["username"] == job
        assert payload["job_number"] == job
        assert "dingtalk.login" in payload["permissions"]
        assert "dsh.access" in payload["permissions"]
    assert homes == [job]
    async with async_session() as db:
        uid = select(User.id).where(User.username == job)
        await db.execute(delete(AuditLog).where(AuditLog.user_id.in_(uid)))
        await db.execute(delete(UserRole).where(UserRole.user_id.in_(select(User.id).where(User.username == job))))
        await db.execute(delete(User).where(User.username == job))
        await db.commit()


@pytest.mark.asyncio
async def test_dingtalk_jit_binds_existing_unbound_job(monkeypatch):
    userid = f"dd_jitb_{uuid4().hex[:8]}"
    user = await _make_user(role_codes=["member"])
    homes: list[str] = []
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, user.username)),
    )
    monkeypatch.setattr("app.api.auth.try_ensure_dsh_home", lambda username: homes.append(username))
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "jit-bind"})
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {resp.json()['access_token']}"},
        )
        assert me.json()["id"] == user.id
        assert me.json()["dingtalk_bound"] is True
    assert homes == [user.username]


@pytest.mark.asyncio
async def test_dingtalk_jit_rejects_missing_job_number(monkeypatch):
    userid = f"dd_noj_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, "")),
    )
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "no-job"})
        assert resp.status_code == 403
        assert "工号" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_jit_rejects_job_owned_by_other_dingtalk(monkeypatch):
    userid = f"dd_clash_{uuid4().hex[:8]}"
    other = await _make_user(role_codes=["member"], dingtalk_user_id=f"dd_other_{uuid4().hex[:8]}")
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, other.username)),
    )
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "clash"})
        assert resp.status_code == 403
        assert "占用" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_dingtalk_jit_rejects_unsafe_job_number(monkeypatch):
    userid = f"dd_bad_{uuid4().hex[:8]}"
    monkeypatch.setattr("app.services.dingtalk.resolve_userid", AsyncMock(return_value=userid))
    monkeypatch.setattr(
        "app.services.dingtalk.get_user_profile",
        AsyncMock(return_value=_jit_profile(userid, "工号甲")),
    )
    async with _client() as client:
        resp = await client.post("/api/auth/dingtalk", json={"auth_code": "bad-job"})
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_user_profile_omits_mobile(monkeypatch):
    monkeypatch.setattr("app.services.dingtalk.get_access_token", AsyncMock(return_value="tok"))

    class _Resp:
        def json(self):
            return {
                "errcode": 0,
                "result": {
                    "userid": "dd_profile_1",
                    "name": "测试同事",
                    "job_number": "t_ab_1",
                    "title": "岗",
                    "mobile": "hidden",
                },
            }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return _Resp()

    monkeypatch.setattr("app.services.dingtalk.httpx.AsyncClient", lambda **_kw: _Client())
    data = await dingtalk_mod.get_user_profile("dd_profile_1")
    assert data == {
        "userid": "dd_profile_1",
        "name": "测试同事",
        "job_number": "t_ab_1",
        "title": "岗",
    }
