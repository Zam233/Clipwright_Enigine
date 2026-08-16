"""P5-B2: 滑动窗口速率限制（内存实现，单实例部署语义）。

多实例部署需换用共享存储（Redis/Mongo），本实现按计划默认关闭，
通过 CLIPWRIGHT_RATE_LIMIT_ENABLED=true 开启。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, window_sec: float = 60.0, max_requests: int = 120):
        self.window = window_sec
        self.max = max_requests
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        """滑动窗口判定；返回 True 表示放行。"""
        now = time.time()
        q = self._hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max:
            return False
        q.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()
