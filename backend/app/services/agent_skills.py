"""个人助手技能包：按角色下发，不是四个独立机器人。"""

from __future__ import annotations

from pathlib import Path

from app.models import User
from app.rbac.deps import get_user_permissions, get_user_role_codes

# 技能文件名 -> 对外 id
_SKILL_FILES = (
    ("00-personal-assistant.md", "personal"),
    ("01-project.md", "project"),
    ("02-task.md", "task"),
    ("03-weekly-report.md", "weekly-report"),
    ("04-case-library.md", "case"),
    ("05-habit-notify.md", "habit"),
    ("06-manager.md", "manager"),
)


def _skills_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[3] / "docs" / "dsh-skills",
        here.parents[1] / "agent_skills",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[0]


def load_skill_text(skill_id: str) -> str:
    for filename, sid in _SKILL_FILES:
        if sid == skill_id:
            path = _skills_dir() / filename
            if path.is_file():
                return path.read_text(encoding="utf-8")
    return ""


def skill_ids_for(user: User) -> list[str]:
    perms = get_user_permissions(user)
    ids = ["personal", "project", "task", "weekly-report", "case", "habit"]
    if "assist.analyze" in perms or get_user_role_codes(user) & {"admin", "dept_head"}:
        ids.append("manager")
    return ids


def skill_pack_for(user: User) -> dict:
    ids = skill_ids_for(user)
    return {
        "skill_ids": ids,
        "skills": [{"id": sid, "text": load_skill_text(sid)} for sid in ids],
    }
