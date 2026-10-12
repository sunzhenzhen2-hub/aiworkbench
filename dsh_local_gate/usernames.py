"""登录名只能当文件夹名时才安全，像门牌号不能写斜杠。"""
from __future__ import annotations

import re

_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def safe_username(raw: str) -> str:
    text = (raw or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        raise ValueError("bad username")
    if not _SAFE.fullmatch(text):
        raise ValueError("bad username")
    return text
