"""P5-B2: 速率限制测试（中间件运行时开关 + 限流器单元测试）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.main import app
from clipwright.services.rate_limit import RateLimiter


class TestRateLimiterUnit:
    def test_window_allows_up_to_max(self):
        rl = RateLimiter(window_sec=60, max_requests=3)
        assert rl.allow("k") and rl.allow("k") and rl.allow("k")
        assert not rl.allow("k")

    def test_keys_isolated(self):
        rl = RateLimiter(window_sec=60, max_requests=1)
        assert rl.allow("a")
        assert rl.allow("b")  # 不同键互不影响
        assert not rl.allow("a")


class TestRateLimitMiddleware:
    @pytest.fixture()
    def client(self):
        prev_enabled = settings.rate_limit_enabled
        prev_max = settings.rate_limit_max_requests
        settings.rate_limit_enabled = True
        settings.rate_limit_max_requests = 3
        # 重置限流器状态（复用模块级实例）
        from clipwright.main import _rate_limiter
        _rate_limiter.reset()
        try:
            yield TestClient(app)
        finally:
            settings.rate_limit_enabled = prev_enabled
            settings.rate_limit_max_requests = prev_max
            _rate_limiter.reset()

    def test_429_after_burst(self, client: TestClient):
        # off 模式无鉴权，persona/list 可匿名访问
        for _ in range(3):
            assert client.get("/api/persona/list").status_code == 200
        resp = client.get("/api/persona/list")
        assert resp.status_code == 429
        assert "频繁" in resp.json()["detail"]
