#!/usr/bin/env python3
"""工作台账本 MCP（stdio）：把 DSH 接到 POST /api/dsh/tools/*。

环境变量：
  DSH_TO_WB_SECRET  机器签名密钥（必填）
  WB_API_BASE       默认 http://127.0.0.1:8100
  DSH_HOME          用户家目录；读取 $DSH_HOME/.wb_identity.json 里的 wb_user_id
  WB_USER_ID        可选，覆盖 identity 文件
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# 复用工作台签名（仓库内模块）；独立运行时退化为内联实现
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(_ROOT / "backend"))

try:
    from app.services.service_auth import SERVICE_DSH, build_signed_headers
except Exception:  # pragma: no cover - 离线兜底
    import hashlib
    import hmac
    import secrets
    import time

    SERVICE_DSH = "dsh-gate"

    def build_signed_headers(secret: str, service_id: str, method: str, path: str, body: str = "") -> dict[str, str]:
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical = "\n".join([method.upper(), path, timestamp, nonce, digest])
        signature = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {
            "content-type": "application/json; charset=utf-8",
            "x-service-id": service_id,
            "x-timestamp": timestamp,
            "x-nonce": nonce,
            "x-signature": signature,
        }


TOOLS: list[dict[str, Any]] = [
    {
        "name": "my_todos",
        "description": "读取当前用户在工作台账本中的待办（不是会话里的 todo_write）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "my_projects",
        "description": "列出当前用户可见的工作台项目。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "my_week_report",
        "description": "读取本人本周周报草稿（账本）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "skill_pack",
        "description": "拉取个人助手技能包（先问再写库等规则）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "draft_task",
        "description": "起草任务写入 agent_drafts，不立刻进待办。需用户确认后再 confirm_draft。必填：project_id, title, due_date(YYYY-MM-DD)。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "title": {"type": "string"},
                "due_date": {"type": "string", "description": "YYYY-MM-DD"},
                "goal": {"type": "string"},
            },
            "required": ["project_id", "title", "due_date"],
            "additionalProperties": True,
        },
    },
    {
        "name": "draft_todo",
        "description": (
            "起草本人待办（写入 agent_drafts，确认后进 my_todos）。"
            "默认挂「日常任务」项目，不必先选立项。"
            "可传单条 title/due_date，或 items 数组一次多条。"
            "确认前不进账本；用户确认后调用 confirm_draft。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "单条待办标题"},
                "due_date": {
                    "type": "string",
                    "description": "YYYY-MM-DD，省略则用今天",
                },
                "goal": {"type": "string"},
                "priority": {"type": "string", "description": "p0|p1|p2|p3"},
                "project_id": {
                    "type": "integer",
                    "description": "可选；省略则挂日常任务",
                },
                "items": {
                    "type": "array",
                    "description": "多条待办；与单条字段二选一",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "due_date": {"type": "string"},
                            "goal": {"type": "string"},
                            "priority": {"type": "string"},
                            "project_id": {"type": "integer"},
                        },
                        "required": ["title"],
                    },
                },
            },
            "additionalProperties": True,
        },
    },
    {
        "name": "draft_week_report",
        "description": "起草周报事项。确认前不入库。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task_title": {"type": "string"},
                            "result_text": {"type": "string"},
                            "next_goal": {"type": "string"},
                        },
                    },
                }
            },
            "additionalProperties": True,
        },
    },
    {
        "name": "draft_project",
        "description": "起草立项。确认前不建项目。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "type": {"type": "string"},
                "end_date": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": True,
        },
    },
    {
        "name": "confirm_draft",
        "description": "用户明确说「确认」后，把草稿真正写入待办/任务/周报/项目账本。必须有 draft_id。",
        "inputSchema": {
            "type": "object",
            "properties": {"draft_id": {"type": "integer"}},
            "required": ["draft_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_get",
        "description": "读取本人长期记忆：习惯偏好、工作摘要、近期事实笔记。记忆≠账本。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_search",
        "description": "检索本人记忆与会话摘录。默认关键词；配置了向量模型后可混合语义检索。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_upsert",
        "description": (
            "写入/更新本人记忆笔记（pref / work_summary / fact）。"
            "不是待办/任务/周报；正式写库仍须 draft→confirm。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "kind": {"type": "string", "description": "pref | work_summary | fact"},
                "title": {"type": "string"},
                "body": {"type": "string"},
                "source": {"type": "string", "description": "user | agent | ledger"},
            },
            "required": ["body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "memory_ingest_session",
        "description": "把一段会话摘录写入本人可检索索引（session_id + text）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["session_id", "text"],
            "additionalProperties": False,
        },
    },
]

_PATH = {
    "my_todos": "/api/dsh/tools/my-todos",
    "my_projects": "/api/dsh/tools/my-projects",
    "my_week_report": "/api/dsh/tools/my-week-report",
    "skill_pack": "/api/dsh/tools/skill-pack",
    "draft_task": "/api/dsh/tools/draft-task",
    "draft_todo": "/api/dsh/tools/draft-todo",
    "draft_week_report": "/api/dsh/tools/draft-week-report",
    "draft_project": "/api/dsh/tools/draft-project",
    "confirm_draft": "/api/dsh/tools/confirm-draft",
    "memory_get": "/api/dsh/tools/memory-get",
    "memory_search": "/api/dsh/tools/memory-search",
    "memory_upsert": "/api/dsh/tools/memory-upsert",
    "memory_ingest_session": "/api/dsh/tools/memory-ingest-session",
}


def _wb_user_id() -> int:
    raw = (os.environ.get("WB_USER_ID") or "").strip()
    if raw.isdigit():
        return int(raw)
    home = Path(os.environ.get("DSH_HOME") or Path.home())
    identity = home / ".wb_identity.json"
    if identity.is_file():
        data = json.loads(identity.read_text(encoding="utf-8"))
        return int(data["wb_user_id"])
    raise RuntimeError("缺少 wb_user_id：请重新从工作台打开助手，或写入 $DSH_HOME/.wb_identity.json")


def _call(tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    secret = (os.environ.get("DSH_TO_WB_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("缺少 DSH_TO_WB_SECRET")
    base = (os.environ.get("WB_API_BASE") or "http://127.0.0.1:8100").rstrip("/")
    path = _PATH[tool]
    body_obj = dict(arguments or {})
    body_obj["wb_user_id"] = _wb_user_id()
    body = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
    headers = build_signed_headers(secret, SERVICE_DSH, "POST", path, body)
    req = urllib.request.Request(
        f"{base}{path}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _result_text(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error_text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "workbench-ledger", "version": "1.0.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if name not in _PATH:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": _error_text(f"未知工具: {name}"),
            }
        try:
            data = _call(name, arguments if isinstance(arguments, dict) else {})
            return {"jsonrpc": "2.0", "id": mid, "result": _result_text(data)}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": _error_text(f"调用失败: {exc}"),
            }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if mid is None:
        return None
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        reply = _handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
