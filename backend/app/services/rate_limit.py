"""简单滑动窗口限流（进程内）。单实例部署够用。"""

from __future__ import annotations

import time
from collections import defaultdict


class SlidingWindowLimiter:
    def __init__(self, max_calls: int, window_sec: float) -> None:
        self.max_calls = max_calls
        self.window_sec = window_sec
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        window_start = now - self.window_sec
        recent = [t for t in self._hits[key] if t > window_start]
        if len(recent) >= self.max_calls:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        return True

    def clear(self) -> None:
        self._hits.clear()


# PC 钉钉扫码端点限流（可在测试中 clear / 调低）
dingtalk_config_limiter = SlidingWindowLimiter(max_calls=30, window_sec=60)
dingtalk_scan_limiter = SlidingWindowLimiter(max_calls=20, window_sec=60)
