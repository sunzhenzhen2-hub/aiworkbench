"""按登录名拉起/停掉个人助手网页。磁盘屋子停进程后仍保留。"""
from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from dsh_local_gate.homes import default_workspace_dir, ensure_user_home

DEFAULT_PORT_START = 3200
DEFAULT_PORT_END = 3299
DEFAULT_IDLE_SEC = 30 * 60
DEFAULT_MAX_LIVE = 8
TOKEN_WAIT_SEC = 45


@dataclass
class UserProc:
    username: str
    port: int
    token: str
    pid: int
    last_used: float


class UserRuntime:
    def __init__(
        self,
        *,
        users_root: Path,
        profiles_src: Path,
        runner: Path,
        python_bin: str,
        shared_env: Path,
        trusted_host: str = "30.251.255.142",
        port_start: int = DEFAULT_PORT_START,
        port_end: int = DEFAULT_PORT_END,
        idle_sec: float = DEFAULT_IDLE_SEC,
        max_live: int = DEFAULT_MAX_LIVE,
    ) -> None:
        self.users_root = users_root
        self.profiles_src = profiles_src
        self.runner = runner
        self.python_bin = python_bin
        self.shared_env = shared_env
        self.trusted_host = trusted_host
        self.port_start = port_start
        self.port_end = port_end
        self.idle_sec = idle_sec
        self.max_live = max_live
        self._lock = threading.Lock()
        self._live: dict[str, subprocess.Popen] = {}
        self._meta: dict[str, UserProc] = {}

    def touch(self, username: str) -> None:
        with self._lock:
            meta = self._meta.get(username)
            if meta:
                meta.last_used = time.time()

    def get(self, username: str) -> UserProc | None:
        with self._lock:
            return self._meta.get(username)

    def ensure(self, username: str) -> UserProc:
        # 等人拿到开门令牌前，别的请求不能再开一份，否则会抢端口启动失败。
        deadline = time.time() + TOKEN_WAIT_SEC + 5
        started_proc: subprocess.Popen | None = None
        started_port = 0
        started_token_file: Path | None = None
        while time.time() < deadline:
            with self._lock:
                existing = self._alive_locked(username)
                if existing:
                    existing.last_used = time.time()
                    return existing
                live = self._live.get(username)
                if live is not None and live.poll() is None:
                    pass
                else:
                    self._evict_if_needed_locked()
                    root = ensure_user_home(self.users_root, username, profiles_src=self.profiles_src)
                    port = self._pick_port_locked()
                    token_file = root / "run" / "token"
                    try:
                        token_file.unlink(missing_ok=True)
                    except OSError:
                        pass
                    env = os.environ.copy()
                    env["DSH_HOME"] = str(root / "home")
                    env["DSH_WEB_HOST"] = "127.0.0.1"
                    env["DSH_WEB_PORT"] = str(port)
                    env["DSH_LAUNCH_TOKEN_FILE"] = str(token_file)
                    env["DSH_SHARED_ENV"] = str(self.shared_env)
                    env["DSH_TRUSTED_HOST"] = self.trusted_host
                    env["HOME"] = str(root / "home")
                    env["XDG_DOCUMENTS_DIR"] = str(root / "home" / "Documents")
                    env["PYTHONUNBUFFERED"] = "1"
                    workdir = default_workspace_dir(root, username)
                    proc = subprocess.Popen(
                        [self.python_bin, str(self.runner)],
                        cwd=str(workdir),
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    self._live[username] = proc
                    (root / "run" / "port").write_text(str(port), encoding="ascii")
                    (root / "run" / "pid").write_text(str(proc.pid), encoding="ascii")
                    started_proc = proc
                    started_port = port
                    started_token_file = token_file
            if started_proc is None or started_token_file is None:
                time.sleep(0.2)
                continue
            token = self._wait_token(started_token_file, started_proc)
            with self._lock:
                meta = UserProc(
                    username=username,
                    port=started_port,
                    token=token,
                    pid=started_proc.pid,
                    last_used=time.time(),
                )
                self._meta[username] = meta
                return meta
        raise RuntimeError("dsh-web start busy")

    def stop(self, username: str) -> None:
        with self._lock:
            self._stop_locked(username)

    def reap_idle(self, now: float | None = None) -> list[str]:
        now = now if now is not None else time.time()
        stopped: list[str] = []
        with self._lock:
            for name, meta in list(self._meta.items()):
                if now - meta.last_used >= self.idle_sec:
                    self._stop_locked(name)
                    stopped.append(name)
        return stopped

    def _alive_locked(self, username: str) -> UserProc | None:
        proc = self._live.get(username)
        meta = self._meta.get(username)
        if proc is None or meta is None:
            return None
        if proc.poll() is not None:
            self._live.pop(username, None)
            self._meta.pop(username, None)
            return None
        return meta

    def _stop_locked(self, username: str) -> None:
        proc = self._live.pop(username, None)
        self._meta.pop(username, None)
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _evict_if_needed_locked(self) -> None:
        live_ok = [name for name in list(self._live) if self._alive_locked(name)]
        if len(live_ok) < self.max_live:
            return
        oldest = min(live_ok, key=lambda name: self._meta[name].last_used)
        self._stop_locked(oldest)

    def _pick_port_locked(self) -> int:
        used = {meta.port for meta in self._meta.values()}
        for port in range(self.port_start, self.port_end + 1):
            if port in used:
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise RuntimeError("no free dsh port")

    def _wait_token(self, token_file: Path, proc: subprocess.Popen) -> str:
        deadline = time.time() + TOKEN_WAIT_SEC
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("dsh-web exited")
            try:
                token = token_file.read_text(encoding="ascii").strip()
            except OSError:
                token = ""
            if token:
                return token
            time.sleep(0.2)
        raise RuntimeError("dsh-web token timeout")
