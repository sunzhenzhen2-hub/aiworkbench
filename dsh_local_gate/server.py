"""本机助手门禁 HTTP：换票、种饼干、按人转发。"""
from __future__ import annotations

import json
import os
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import hmac
import hashlib
import secrets
import re
import sys

from jose import JWTError, jwt

from app.services.service_auth import (
    SERVICE_WORKBENCH,
    parse_allowlist,
    verify_signed_request,
)
from dsh_local_gate.homes import ensure_user_home, write_wb_identity
from dsh_local_gate.runtime import UserRuntime
from dsh_local_gate.usernames import safe_username

COOKIE_NAME = "dsh_ws"
CODE_TTL_SEC = 90
SESSION_TTL_SEC = 12 * 3600


class Gate:
    def __init__(
        self,
        *,
        runtime: UserRuntime,
        sso_secret: str,
        wb_to_dsh_secret: str,
        allowlist: list[str],
        users_root: Path,
        profiles_src: Path,
    ) -> None:
        self.runtime = runtime
        self.sso_secret = sso_secret
        self.wb_to_dsh_secret = wb_to_dsh_secret
        self.allowlist = allowlist
        self.users_root = users_root
        self.profiles_src = profiles_src
        self._codes: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def exchange(self, ticket: str) -> str:
        try:
            payload = jwt.decode(ticket, self.sso_secret, algorithms=["HS256"])
        except JWTError as exc:
            raise ValueError("bad ticket") from exc
        if payload.get("typ") != "dsh_sso":
            raise ValueError("bad ticket")
        username = safe_username(str(payload.get("username") or ""))
        root = ensure_user_home(self.users_root, username, profiles_src=self.profiles_src)
        try:
            wb_user_id = int(payload.get("wb_user_id"))
        except (TypeError, ValueError):
            wb_user_id = 0
        if wb_user_id > 0:
            write_wb_identity(root, username=username, wb_user_id=wb_user_id)
        code = secrets.token_urlsafe(24)
        with self._lock:
            self._codes[code] = (username, time.time() + CODE_TTL_SEC)
        return code

    def ensure_home(self, username: str) -> str:
        name = safe_username(username)
        ensure_user_home(self.users_root, name, profiles_src=self.profiles_src)
        return name

    def redeem(self, code: str) -> str:
        with self._lock:
            row = self._codes.pop(code, None)
        if not row:
            raise ValueError("bad code")
        username, exp = row
        if time.time() > exp:
            raise ValueError("bad code")
        return username

    def session_cookie(self, username: str) -> str:
        exp = int(time.time()) + SESSION_TTL_SEC
        nonce = secrets.token_hex(8)
        body = f"{username}.{exp}.{nonce}"
        sig = hmac.new(self.wb_to_dsh_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{body}.{sig}"

    def username_from_cookie(self, header: str) -> str | None:
        value = cookie_value(header, COOKIE_NAME)
        if not value:
            return None
        parts = value.split(".")
        if len(parts) != 4:
            return None
        username, exp_s, nonce, sig = parts
        body = f"{username}.{exp_s}.{nonce}"
        expected = hmac.new(
            self.wb_to_dsh_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        try:
            if int(exp_s) < int(time.time()):
                return None
            return safe_username(username)
        except (ValueError, TypeError):
            return None


def cookie_value(header: str, name: str) -> str:
    for segment in (header or "").split(";"):
        at = segment.find("=")
        if at == -1:
            continue
        if segment[:at].strip() == name:
            return segment[at + 1 :].strip()
    return ""


class GateHandler(BaseHTTPRequestHandler):
    server: "GateHTTPServer"

    def log_message(self, fmt: str, *args) -> None:
        text = re.sub(r"(token|code)=[^\s&\"]+", r"\1=<redacted>", fmt % args)
        sys.stderr.write("%s - %s\n" % (self.address_string(), text))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/internal/sso/exchange":
            self._exchange()
            return
        if parsed.path == "/internal/homes/ensure":
            self._ensure_home()
            return
        if parsed.path == "/internal/logout":
            self._logout()
            return
        self._proxy()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/sso":
            self._sso(parsed)
            return
        if parsed.path == "/logout":
            self._browser_logout(parsed)
            return
        self._proxy()

    def do_HEAD(self) -> None:
        self._proxy()

    def do_PUT(self) -> None:
        self._proxy()

    def do_PATCH(self) -> None:
        self._proxy()

    def do_DELETE(self) -> None:
        self._proxy()

    def do_OPTIONS(self) -> None:
        self._proxy()

    def _client_ip(self) -> str:
        return (self.headers.get("x-real-ip") or self.client_address[0] or "").split(",")[0].strip()

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _require_workbench_sig(self, body: str) -> bool:
        gate = self.server.gate
        ok, _reason = verify_signed_request(
            secret=gate.wb_to_dsh_secret,
            expected_service_id=SERVICE_WORKBENCH,
            method=self.command,
            path=urlparse(self.path).path,
            body=body,
            headers=self.headers,
            ip=self._client_ip(),
            allowlist=gate.allowlist,
        )
        return ok

    def _exchange(self) -> None:
        raw = self._read_body()
        body = raw.decode("utf-8")
        if not self._require_workbench_sig(body):
            self._text(401, "机器签名没通过")
            return
        try:
            payload = json.loads(body or "{}")
            ticket = str(payload.get("ticket") or "")
            code = self.server.gate.exchange(ticket)
        except (ValueError, json.JSONDecodeError):
            self._text(400, "换票失败")
            return
        except RuntimeError:
            self._text(503, "本机助手还在启动，请稍后再打开对话。")
            return
        data = json.dumps({"code": code}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _ensure_home(self) -> None:
        raw = self._read_body()
        body = raw.decode("utf-8")
        if not self._require_workbench_sig(body):
            self._text(401, "机器签名没通过")
            return
        try:
            payload = json.loads(body or "{}")
            username = self.server.gate.ensure_home(str(payload.get("username") or ""))
        except (ValueError, json.JSONDecodeError):
            self._text(400, "账号不对")
            return
        self._json(200, {"ok": True, "username": username})

    def _logout(self) -> None:
        raw = self._read_body()
        body = raw.decode("utf-8")
        if not self._require_workbench_sig(body):
            self._text(401, "机器签名没通过")
            return
        self._json(200, {"ok": True})

    def _browser_logout(self, parsed) -> None:
        """助手页 GET /logout：清饼干并停本人工序，不要转进网页进程（那边没有这个地址）。"""
        username = self.server.gate.username_from_cookie(self.headers.get("Cookie") or "")
        if username:
            self.server.gate.runtime.stop(username)
        stay = (parse_qs(parsed.query).get("stay") or [""])[0]
        self.send_response(204 if stay == "1" else 302)
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax",
        )
        if stay != "1":
            self.send_header("Location", "/aiworkbench/")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _sso(self, parsed) -> None:
        qs = parse_qs(parsed.query)
        code = (qs.get("code") or [""])[0]
        try:
            username = self.server.gate.redeem(code)
            proc = self.server.gate.runtime.ensure(username)
        except ValueError:
            self._text(401, "登录已过期，请从工作台重新打开助手。")
            return
        except RuntimeError:
            self._text(503, "本机助手还在启动，请稍后再打开对话。")
            return
        cookie = self.server.gate.session_cookie(username)
        self.send_response(302)
        self.send_header("Location", f"/?token={proc.token}")
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}={cookie}; Path=/; Max-Age={SESSION_TTL_SEC}; HttpOnly; SameSite=Lax",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _proxy(self) -> None:
        gate = self.server.gate
        username = gate.username_from_cookie(self.headers.get("Cookie") or "")
        if not username:
            self._text(401, "dsh web authentication required; reopen from the workbench assistant.")
            return
        try:
            proc = gate.runtime.ensure(username)
            gate.runtime.touch(username)
        except RuntimeError:
            self._text(503, "本机助手还在启动，请稍后再打开对话。")
            return
        self._forward(proc.port)

    def _forward(self, port: int) -> None:
        upgrade = (self.headers.get("Upgrade") or "").lower() == "websocket"
        length = int(self.headers.get("Content-Length") or 0)
        body = b""
        if length > 0:
            body = self.rfile.read(length)
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        except OSError:
            self._text(502, "助手网页暂时连不上")
            return
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for key, value in self.headers.items():
            if key.lower() == "connection" and not upgrade:
                continue
            lines.append(f"{key}: {value}")
        if not upgrade:
            lines.append("Connection: close")
        payload = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body
        try:
            sock.sendall(payload)
            if upgrade:
                self._splice(sock)
                return
            sock.settimeout(3600)
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _splice(self, backend: socket.socket) -> None:
        client = self.connection
        backend.setblocking(False)
        client.setblocking(False)
        try:
            while True:
                readable, _, _ = select.select([client, backend], [], [], 3600)
                if not readable:
                    break
                if client in readable:
                    data = client.recv(65536)
                    if not data:
                        break
                    backend.sendall(data)
                if backend in readable:
                    data = backend.recv(65536)
                    if not data:
                        break
                    client.sendall(data)
        except OSError:
            pass
        finally:
            try:
                backend.close()
            except OSError:
                pass

    def _text(self, status: int, message: str) -> None:
        data = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class GateHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, addr, handler, gate: Gate) -> None:
        super().__init__(addr, handler)
        self.gate = gate


def _reaper(runtime: UserRuntime, stop: threading.Event) -> None:
    while not stop.wait(30):
        runtime.reap_idle()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 3081,
    users_root: Path | None = None,
    profiles_src: Path | None = None,
    runtime: UserRuntime | None = None,
) -> GateHTTPServer:
    root = Path(__file__).resolve().parents[1]
    users_root = users_root or Path(os.environ.get("DSH_USERS_ROOT", "/var/lib/dsh/users"))
    profiles_src = profiles_src or Path(os.environ.get("DSH_PROFILES_SRC", "/root/.dsh/profiles"))
    sso_secret = (os.environ.get("DSH_SSO_SECRET") or "").strip()
    wb_secret = (os.environ.get("WB_TO_DSH_SECRET") or "").strip()
    if not sso_secret or not wb_secret:
        raise SystemExit("DSH_SSO_SECRET / WB_TO_DSH_SECRET missing")
    runtime = runtime or UserRuntime(
        users_root=users_root,
        profiles_src=profiles_src,
        runner=root / "scripts" / "run_dsh_web.py",
        python_bin=os.environ.get("DSH_GATE_PYTHON", "/usr/bin/python3"),
        shared_env=Path(os.environ.get("DSH_SHARED_ENV", "/root/.dsh/.env")),
        trusted_host=os.environ.get("DSH_TRUSTED_HOST", "30.251.255.142"),
    )
    users_root.mkdir(parents=True, exist_ok=True)
    os.chmod(users_root, 0o700)
    gate = Gate(
        runtime=runtime,
        sso_secret=sso_secret,
        wb_to_dsh_secret=wb_secret,
        allowlist=parse_allowlist(os.environ.get("DSH_SERVICE_ALLOWLIST")),
        users_root=users_root,
        profiles_src=profiles_src,
    )
    httpd = GateHTTPServer((host, port), GateHandler, gate)
    stop = threading.Event()
    threading.Thread(target=_reaper, args=(runtime, stop), daemon=True).start()
    httpd.stop_event = stop  # type: ignore[attr-defined]
    return httpd


def main() -> None:
    host = os.environ.get("DSH_GATE_HOST", "127.0.0.1")
    port = int(os.environ.get("DSH_GATE_PORT", "3081"))
    httpd = serve(host=host, port=port)
    print(f"dsh-gate listening on {host}:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop = getattr(httpd, "stop_event", None)
        if stop:
            stop.set()
        httpd.server_close()


if __name__ == "__main__":
    main()
