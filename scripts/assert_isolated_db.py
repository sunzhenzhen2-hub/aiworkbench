"""拒绝把 142 试验库指到现网 64 正式库。"""

from __future__ import annotations

import os
import sys

FORBIDDEN_HOSTS = ("30.251.255.64",)


def database_url_ok(url: str) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return True
    return not any(host in text for host in FORBIDDEN_HOSTS)


def main() -> int:
    url = os.environ.get("DATABASE_URL") or ""
    if len(sys.argv) > 1:
        url = sys.argv[1]
    if not database_url_ok(url):
        print("拒绝：DATABASE_URL 指向现网正式库，142 必须用本机独立库。", file=sys.stderr)
        return 2
    print("ok: database url is isolated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
