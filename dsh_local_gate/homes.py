"""每人一间磁盘屋子：家目录、文档里的默认干活文件夹、运行状态。"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dsh_local_gate.usernames import safe_username


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def user_root(base: Path, username: str) -> Path:
    root = (base.resolve() / safe_username(username)).resolve()
    if root.parent != base.resolve():
        raise ValueError("bad username")
    return root


def documents_dir(root: Path) -> Path:
    return root / "home" / "Documents"


def default_workspace_dir(root: Path, username: str) -> Path:
    return documents_dir(root) / "deepseek-harness" / safe_username(username)


def ensure_user_home(
    base: Path,
    username: str,
    *,
    profiles_src: Path | None = None,
) -> Path:
    root = user_root(base, username)
    (root / "home").mkdir(parents=True, exist_ok=True)
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    (root / "run").mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    dest = root / "home" / "profiles"
    if profiles_src is not None and profiles_src.is_dir() and not dest.exists():
        dest.symlink_to(profiles_src.resolve())
    _ensure_documents_workspace(root, username)
    _reset_if_home_was_workspace(root)
    _seed_named_workspace(root, username)
    _seed_ledger_rules(root, username)
    _seed_memory_guide(root, username)
    return root


_MEMORY_GUIDE = """# 长期记忆说明（助手必读）

每人有跨会话的长期记忆，但**记忆不是账本**。

## 何时读

- 新开对话、用户提到「上次 / 我习惯 / 最近在忙」时：先 `memory_get`，必要时 `memory_search`。
- 可用 DSH 自带会话搜索/引用；工作台 `memory_*` 是跨会话稳定入口。

## 何时写

- 用户明确说「记住…」「以后按…做」→ `memory_upsert`（kind=`pref` 或 `fact`）。
- 滚动工作摘要 → `memory_upsert`（kind=`work_summary`，每人一条会覆盖更新）。
- 重要会话要点 → `memory_ingest_session`（session_id + 摘录文本）。

## 禁止

- 禁止用记忆代替待办/任务/周报写库。
- 禁止声称「已创建任务」却只写了 memory。
- 禁止查别人的记忆。
"""


_LEDGER_RULES = """# 工作台账本规则（助手必读）

整理工作日记或任务清单后，**先问再写**，不要自动进账本。

必须用弹出选择框（工具 `ask_user_question`）让用户点选，不要让用户打字「待办是/否」：
1. 是否加入【我的待办】？
2. 是否写成【任务】并挂到项目？（选「是」后再弹项目列表选择）
3. （可选）是否写入【本周周报】？

用户点选同意之前：只总结、只生成文档，不调用写库。
同意后：调用工作台 MCP（`mcp__workbench__draft_task` 等）起草，
再用选择框确认后调用 `mcp__workbench__confirm_draft` 才真正写入。

禁止事项：
- 禁止用纯文字「回复：待办是 / 待办否」代替弹出选择框
- 会话里的 `todo_write` ≠ 工作台待办
- 只在 Documents 里写 md ≠ 已创建任务/项目/周报
- 工具没返回 `draft_id` / `ok: true` 时，不许声称「已创建」
"""


def _seed_ledger_rules(root: Path, username: str) -> None:
    """每人干活夹放一份短规则，提醒助手整理后先问再写库。"""
    work = default_workspace_dir(root, username)
    work.mkdir(parents=True, exist_ok=True)
    path = work / "工作台账本规则.md"
    path.write_text(_LEDGER_RULES, encoding="utf-8")


def _seed_memory_guide(root: Path, username: str) -> None:
    """每人干活夹放长期记忆读写纪律。"""
    work = default_workspace_dir(root, username)
    work.mkdir(parents=True, exist_ok=True)
    path = work / "长期记忆说明.md"
    path.write_text(_MEMORY_GUIDE, encoding="utf-8")


def write_wb_identity(root: Path, *, username: str, wb_user_id: int) -> None:
    """SSO 换票后记下工号对应的工作台用户 id，供账本 MCP 签名调用。"""
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    path = home / ".wb_identity.json"
    payload = {
        "username": safe_username(username),
        "wb_user_id": int(wb_user_id),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _ensure_documents_workspace(root: Path, username: str) -> None:
    """助手网页要在「文档/deepseek-harness/工号」建默认干活夹。"""
    docs = documents_dir(root)
    work = default_workspace_dir(root, username)
    work.mkdir(parents=True, exist_ok=True)
    legacy = docs / "deepseek-harness" / "default-workspace"
    if not legacy.exists() and not legacy.is_symlink():
        try:
            legacy.symlink_to(work.name)
        except OSError:
            legacy.mkdir(parents=True, exist_ok=True)
    cfg = root / "home" / ".config"
    cfg.mkdir(parents=True, exist_ok=True)
    dirs_file = cfg / "user-dirs.dirs"
    if not dirs_file.exists():
        dirs_file.write_text('XDG_DOCUMENTS_DIR="$HOME/Documents"\n', encoding="utf-8")
    patch = root / "home" / "cordis.patch.yml"
    docs_abs = str(docs.resolve())
    patch.write_text(
        "# 本机按工号生成，指定文档目录，让助手能自动建默认工作区。\n"
        "- id: workspace-controller\n"
        "  config:\n"
        f"    documentsDirectory: {json.dumps(docs_abs, ensure_ascii=False)}\n",
        encoding="utf-8",
    )


def _reset_if_home_was_workspace(root: Path) -> None:
    """若误把家目录登记成工作区，清掉标记，让助手下次自动建默认夹。"""
    store = root / "home" / "storages" / "workspace.json"
    if not store.is_file():
        return
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    tables = (data.get("tables") or {}).get("workspaces") or {}
    if not tables:
        return
    home = (root / "home").resolve()
    for row in tables.values():
        raw = str(row.get("path") or "")
        try:
            path = Path(raw).resolve()
        except OSError:
            return
        if path != home:
            return
    global_state = data.setdefault("global", {})
    global_state["initialized"] = False
    global_state["workspaceIds"] = []
    data.setdefault("tables", {})["workspaces"] = {}
    store.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _seed_named_workspace(root: Path, username: str) -> None:
    """把干活夹登记成工号名字，避免助手把家目录显示成 home。"""
    name = safe_username(username)
    work = default_workspace_dir(root, name)
    work.mkdir(parents=True, exist_ok=True)
    store = root / "home" / "storages" / "workspace.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if store.is_file():
        try:
            parsed = json.loads(store.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = {}
    work_abs = str(work.resolve())
    now = _iso_now()
    tables = (data.get("tables") or {}).get("workspaces") or {}
    for row in tables.values():
        if not isinstance(row, dict):
            continue
        raw = str(row.get("path") or "")
        try:
            if Path(raw).resolve() != work.resolve():
                continue
        except OSError:
            continue
        dirty = False
        if str(row.get("title") or "") != name:
            row["title"] = name
            dirty = True
        if not row.get("createdAt"):
            row["createdAt"] = now
            dirty = True
        if not row.get("updatedAt"):
            row["updatedAt"] = now
            dirty = True
        if not isinstance(row.get("sessionIds"), list):
            row["sessionIds"] = []
            dirty = True
        global_state = data.setdefault("global", {})
        global_state.setdefault("archivedSessionIds", [])
        global_state.setdefault("pinnedSessionIds", [])
        if dirty:
            store.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    wid = str(uuid.uuid4())
    data.setdefault("unit", {"name": "workspace", "version": 2})
    global_state = data.setdefault("global", {})
    global_state["initialized"] = True
    global_state["workspaceIds"] = [wid]
    global_state.setdefault("archivedSessionIds", [])
    global_state.setdefault("pinnedSessionIds", [])
    data.setdefault("tables", {})["workspaces"] = {
        wid: {
            "path": work_abs,
            "title": name,
            "sessionIds": [],
            "createdAt": now,
            "updatedAt": now,
        }
    }
    store.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
