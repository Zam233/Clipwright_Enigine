"""HyperframesRenderer.await_available() 冷启动轮询预热测试。

根因：``_CachedProbe.get_sync()`` 冷启动首次调用返回 default=False（async_util.py:82-101），
而首次 npx 解析可能耗时 ~90s。``await_available`` 在启动期/首次管线前轮询缓存探针，
直到其翻转为 True 或超时，从而避免冷启动竞态把逻辑动画误降级为 drawtext。

所有测试一律 monkeypatch 探针，绝不派生真实 npx 子进程。
"""

from __future__ import annotations

import asyncio

import clipwright.animation.hyperframes_renderer as hf
from clipwright.animation.hyperframes_renderer import HyperframesRenderer


class TestAwaitAvailable:
    """await_available() 轮询语义。"""

    def test_returns_true_when_probe_flips_false_to_true(self, monkeypatch) -> None:
        """脚本化探针：首次 False、二次 True → await_available(10) 返回 True，且轮询 ≥2 次。"""
        calls = {"n": 0}

        async def fake_probe():
            calls["n"] += 1
            return calls["n"] >= 2

        monkeypatch.setattr(hf, "_hf_available", fake_probe)
        monkeypatch.setattr(hf, "_AWAIT_POLL_INTERVAL", 0.01)

        result = asyncio.run(HyperframesRenderer.await_available(timeout=10))

        assert result is True
        assert calls["n"] >= 2

    def test_returns_false_on_timeout_without_raising(self, monkeypatch) -> None:
        """探针恒 False → await_available(1) 超时返回 False，不抛异常、不挂起。"""
        async def always_false():
            return False

        monkeypatch.setattr(hf, "_hf_available", always_false)
        monkeypatch.setattr(hf, "_AWAIT_POLL_INTERVAL", 0.01)

        result = asyncio.run(HyperframesRenderer.await_available(timeout=1))

        assert result is False


class TestInterfacePreserved:
    """既有 is_available() / ais_available() 接口保持。"""

    def test_is_available_still_exists_and_callable(self, monkeypatch) -> None:
        """is_available() 仍存在且可调用，返回值类型为 bool（不派生真实 npx）。"""
        class _FakeProbe:
            def get_sync(self) -> bool:
                return True

        monkeypatch.setattr(hf, "_hf_available", _FakeProbe())

        assert callable(HyperframesRenderer.is_available)
        available = HyperframesRenderer.is_available()
        assert isinstance(available, bool)
