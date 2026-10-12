#!/usr/bin/env python3
"""本机助手网页启动包装：记下开门令牌，并允许内网主机名访问。令牌文件仅本机运行目录，不进仓库。"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

TOKEN_FILE = Path(os.environ.get("DSH_LAUNCH_TOKEN_FILE", "/run/dsh-launch-token"))
NODE = os.environ.get("DSH_NODE", "/usr/bin/node")
DSH_BIN = os.environ.get(
    "DSH_BIN",
    "/opt/dsh-offline/node_modules/@deepseek-ai/dsh/lib/bin.js",
)
HOST = os.environ.get("DSH_WEB_HOST", "127.0.0.1")
PORT = os.environ.get("DSH_WEB_PORT", "3080")
TRUSTED_HOST = os.environ.get("DSH_TRUSTED_HOST", "30.251.255.142")
SHARED_ENV = Path(os.environ.get("DSH_SHARED_ENV", "/root/.dsh/.env"))
TOKEN_RE = re.compile(r"dsh web:\s+https?://\S+\?token=([A-Za-z0-9_-]+)")


def load_shared_env(path: Path) -> None:
    """模型钥匙只从共享文件读进进程环境，不写到用户目录。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        os.environ.setdefault(key, value.strip().strip("'").strip('"'))


def main() -> int:
    load_shared_env(SHARED_ENV)
    try:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.unlink(missing_ok=True)
    except OSError:
        pass
    cmd = [
        NODE,
        DSH_BIN,
        "web",
        "--no-open",
        "--host",
        HOST,
        "--port",
        PORT,
        "--trusted-host",
        TRUSTED_HOST,
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        match = TOKEN_RE.search(line)
        if match:
            TOKEN_FILE.write_text(match.group(1), encoding="ascii")
            TOKEN_FILE.chmod(0o600)
    return int(proc.wait() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
