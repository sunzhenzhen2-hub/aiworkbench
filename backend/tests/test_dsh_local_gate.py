"""本机门禁：按登录名建屋子、换票、两人互不串房。"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

import pytest
from jose import jwt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.core.security import ALGORITHM
from app.services.service_auth import SERVICE_WORKBENCH, build_signed_headers, reset_for_tests
from dsh_local_gate.homes import ensure_user_home
from dsh_local_gate.runtime import UserProc
from dsh_local_gate.server import COOKIE_NAME, Gate, GateHandler, GateHTTPServer
from dsh_local_gate.usernames import safe_username

SSO = "sso-local-gate-test-secret"
WB = "wb-to-dsh-local-gate-test-secret"


class FakeRuntime:
    def __init__(self, tokens: dict[str, str], ports: dict[str, int]) -> None:
        self.tokens = tokens
        self.ports = ports
        self.started: list[str] = []

    def ensure(self, username: str) -> UserProc:
        self.started.append(username)
        return UserProc(username, self.ports[username], self.tokens[username], 1, time.time())

    def touch(self, username: str) -> None:
        return None

    def stop(self, username: str) -> None:
        self.started.append(f"stop:{username}")

    def reap_idle(self, now=None):
        return []


class EchoHandler(BaseHTTPRequestHandler):
    username = "unset"

    def do_GET(self) -> None:
        body = self.username.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return None


def _echo_server(username: str) -> tuple[ThreadingHTTPServer, int]:
    class Bound(EchoHandler):
        pass

    Bound.username = username
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, int(httpd.server_address[1])


def _gate_server(tmp_path: Path, runtime: FakeRuntime) -> tuple[GateHTTPServer, int]:
    reset_for_tests()
    users_root = tmp_path / "users"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    gate = Gate(
        runtime=runtime,
        sso_secret=SSO,
        wb_to_dsh_secret=WB,
        allowlist=["127.0.0.1", "::1"],
        users_root=users_root,
        profiles_src=profiles,
    )
    httpd = GateHTTPServer(("127.0.0.1", 0), GateHandler, gate)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, int(httpd.server_address[1])


def _ticket(username: str) -> str:
    return jwt.encode(
        {
            "typ": "dsh_sso",
            "username": username,
            "name": username,
            "role": "member",
            "wb_user_id": 1,
            "jti": "abc",
        },
        SSO,
        algorithm=ALGORITHM,
    )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _open(req: Request):
    opener = build_opener(NoRedirect)
    try:
        return opener.open(req, timeout=5)
    except HTTPError as exc:
        return exc


def _signed_exchange(port: int, ticket: str) -> dict:
    body = json.dumps({"ticket": ticket}, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(WB, SERVICE_WORKBENCH, "POST", "/internal/sso/exchange", body)
    req = Request(
        f"http://127.0.0.1:{port}/internal/sso/exchange",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_safe_username_rejects_path():
    with pytest.raises(ValueError):
        safe_username("../root")
    with pytest.raises(ValueError):
        safe_username("a/b")
    assert safe_username("t_ws_a") == "t_ws_a"


def test_ensure_user_home_reuses_directory(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    first = ensure_user_home(tmp_path / "users", "t_ws_a", profiles_src=profiles)
    second = ensure_user_home(tmp_path / "users", "t_ws_a", profiles_src=profiles)
    other = ensure_user_home(tmp_path / "users", "t_ws_b", profiles_src=profiles)
    rules = first / "home" / "Documents" / "deepseek-harness" / "t_ws_a" / "工作台账本规则.md"
    assert rules.is_file()
    rules_text = rules.read_text(encoding="utf-8")
    assert "我的待办" in rules_text
    assert "任务" in rules_text
    assert "先问再写" in rules_text
    mem = first / "home" / "Documents" / "deepseek-harness" / "t_ws_a" / "长期记忆说明.md"
    assert mem.is_file()
    assert "memory_get" in mem.read_text(encoding="utf-8")
    assert first == second
    assert first != other
    assert (first / "home" / "profiles").is_symlink()
    assert (other / "workspace").is_dir()
    default_a = first / "home" / "Documents" / "deepseek-harness" / "t_ws_a"
    default_b = other / "home" / "Documents" / "deepseek-harness" / "t_ws_b"
    assert default_a.is_dir()
    assert default_b.is_dir()
    assert default_a != default_b
    data = json.loads((first / "home" / "storages" / "workspace.json").read_text(encoding="utf-8"))
    rows = list((data.get("tables") or {}).get("workspaces", {}).values())
    titles = [row.get("title") for row in rows]
    assert "t_ws_a" in titles
    seeded = next(row for row in rows if row.get("title") == "t_ws_a")
    assert seeded.get("createdAt")
    assert seeded.get("updatedAt")
    assert isinstance(seeded.get("sessionIds"), list)
    patch = (first / "home" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert "documentsDirectory" in patch
    assert "Documents" in patch


def test_ensure_resets_home_registered_as_workspace(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    root = ensure_user_home(tmp_path / "users", "t_ws_a", profiles_src=profiles)
    store = root / "home" / "storages"
    store.mkdir(parents=True, exist_ok=True)
    (store / "workspace.json").write_text(
        json.dumps(
            {
                "unit": {"name": "workspace", "version": 2},
                "global": {
                    "initialized": True,
                    "workspaceIds": ["wid-1"],
                    "archivedSessionIds": [],
                    "pinnedSessionIds": [],
                },
                "tables": {
                    "workspaces": {
                        "wid-1": {
                            "path": str(root / "home"),
                            "title": "home",
                            "sessionIds": [],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    ensure_user_home(tmp_path / "users", "t_ws_a", profiles_src=profiles)
    data = json.loads((store / "workspace.json").read_text(encoding="utf-8"))
    assert data["global"]["initialized"] is True
    ids = data["global"]["workspaceIds"]
    assert len(ids) == 1
    row = data["tables"]["workspaces"][ids[0]]
    assert row["title"] == "t_ws_a"
    assert row["path"].endswith("t_ws_a")
    assert row.get("createdAt")
    assert row.get("updatedAt")


def _signed_ensure(port: int, username: str, *, secret: str = WB) -> tuple[int, dict | str]:
    body = json.dumps({"username": username}, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(secret, SERVICE_WORKBENCH, "POST", "/internal/homes/ensure", body)
    req = Request(
        f"http://127.0.0.1:{port}/internal/homes/ensure",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_ensure_home_creates_directory_without_starting_runtime(tmp_path):
    runtime = FakeRuntime({}, {})
    httpd, port = _gate_server(tmp_path, runtime)
    try:
        status, payload = _signed_ensure(port, "t_ws_a")
        assert status == 200
        assert payload["ok"] is True
        assert (tmp_path / "users" / "t_ws_a" / "home" / "Documents" / "deepseek-harness" / "t_ws_a").is_dir()
        assert runtime.started == []
    finally:
        httpd.shutdown()


def test_ensure_home_rejects_bad_username(tmp_path):
    runtime = FakeRuntime({}, {})
    httpd, port = _gate_server(tmp_path, runtime)
    try:
        status, _body = _signed_ensure(port, "../etc")
        assert status == 400
    finally:
        httpd.shutdown()


def test_ensure_home_rejects_unsigned(tmp_path):
    runtime = FakeRuntime({}, {})
    httpd, port = _gate_server(tmp_path, runtime)
    try:
        req = Request(
            f"http://127.0.0.1:{port}/internal/homes/ensure",
            data=b'{"username":"t_ws_a"}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as exc:
            urlopen(req, timeout=5)
        assert exc.value.code == 401
    finally:
        httpd.shutdown()


def test_exchange_rejects_bad_username(tmp_path):
    runtime = FakeRuntime({}, {})
    httpd, port = _gate_server(tmp_path, runtime)
    try:
        with pytest.raises(HTTPError) as exc:
            _signed_exchange(port, _ticket("../etc"))
        assert exc.value.code == 400
    finally:
        httpd.shutdown()


def test_two_users_do_not_share_proxy(tmp_path):
    echo_a, port_a = _echo_server("room-a")
    echo_b, port_b = _echo_server("room-b")
    runtime = FakeRuntime(
        tokens={"t_ws_a": "tok-a", "t_ws_b": "tok-b"},
        ports={"t_ws_a": port_a, "t_ws_b": port_b},
    )
    httpd, gate_port = _gate_server(tmp_path, runtime)
    try:
        code_a = _signed_exchange(gate_port, _ticket("t_ws_a"))["code"]
        code_b = _signed_exchange(gate_port, _ticket("t_ws_b"))["code"]
        assert code_a != code_b
        assert (tmp_path / "users" / "t_ws_a").is_dir()
        assert (tmp_path / "users" / "t_ws_b").is_dir()

        sso_a = _open(Request(f"http://127.0.0.1:{gate_port}/sso?code={code_a}"))
        assert sso_a.code == 302
        loc = sso_a.headers.get("Location") or ""
        assert loc.startswith("/?token=")
        cookie_a = sso_a.headers.get("Set-Cookie") or ""
        assert COOKIE_NAME in cookie_a

        sso_b = _open(Request(f"http://127.0.0.1:{gate_port}/sso?code={code_b}"))
        assert sso_b.code == 302
        cookie_b = sso_b.headers.get("Set-Cookie") or ""

        cookie_a_val = cookie_a.split(";")[0]
        cookie_b_val = cookie_b.split(";")[0]
        with urlopen(
            Request(
                f"http://127.0.0.1:{gate_port}/",
                headers={"Cookie": cookie_a_val},
            ),
            timeout=5,
        ) as resp:
            assert resp.read().decode("utf-8") == "room-a"
        with urlopen(
            Request(
                f"http://127.0.0.1:{gate_port}/",
                headers={"Cookie": cookie_b_val},
            ),
            timeout=5,
        ) as resp:
            assert resp.read().decode("utf-8") == "room-b"
    finally:
        httpd.shutdown()
        echo_a.shutdown()
        echo_b.shutdown()


def test_get_logout_clears_cookie_without_proxy(tmp_path):
    echo, echo_port = _echo_server("should-not-see")
    runtime = FakeRuntime(tokens={"t_out": "tok"}, ports={"t_out": echo_port})
    httpd, gate_port = _gate_server(tmp_path, runtime)
    try:
        code = _signed_exchange(gate_port, _ticket("t_out"))["code"]
        sso = _open(Request(f"http://127.0.0.1:{gate_port}/sso?code={code}"))
        cookie = (sso.headers.get("Set-Cookie") or "").split(";")[0]
        resp = _open(
            Request(
                f"http://127.0.0.1:{gate_port}/logout?stay=1",
                headers={"Cookie": cookie},
            )
        )
        assert resp.code == 204
        assert "Max-Age=0" in (resp.headers.get("Set-Cookie") or "")
        assert "stop:t_out" in runtime.started
    finally:
        httpd.shutdown()
        echo.shutdown()
