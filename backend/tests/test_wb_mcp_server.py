"""工作台账本 MCP：stdio 列表与签名调用冒烟（需本机 API 时可跳过）。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "wb_mcp_server.py"
VENV_PY = ROOT / "backend" / ".venv" / "bin" / "python"


def _run_rpc(messages: list[dict], env: dict) -> list[dict]:
    payload = "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n"
    proc = subprocess.run(
        [str(VENV_PY), str(SCRIPT)],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = []
    for line in proc.stdout.splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_wb_mcp_tools_list_without_secret():
    env = {**os.environ, "PATH": os.environ.get("PATH", ""), "PYTHONPATH": f"{ROOT}:{ROOT}/backend"}
    replies = _run_rpc(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
        env,
    )
    names = {t["name"] for t in replies[-1]["result"]["tools"]}
    assert "draft_task" in names
    assert "draft_todo" in names
    assert "confirm_draft" in names
    assert "my_todos" in names
    assert "memory_get" in names
    assert "memory_search" in names
    assert "memory_upsert" in names
    assert "memory_ingest_session" in names


def test_write_wb_identity(tmp_path):
    from dsh_local_gate.homes import ensure_user_home, write_wb_identity

    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "web").mkdir()
    root = ensure_user_home(tmp_path / "users", "t_mcp_a", profiles_src=profiles)
    write_wb_identity(root, username="t_mcp_a", wb_user_id=42)
    data = json.loads((root / "home" / ".wb_identity.json").read_text(encoding="utf-8"))
    assert data["wb_user_id"] == 42
    assert data["username"] == "t_mcp_a"
