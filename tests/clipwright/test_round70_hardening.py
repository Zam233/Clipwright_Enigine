"""轮70 加固：D10 proceed 幂等 + D15 后台任务卫生。"""

from __future__ import annotations

import asyncio
import logging

import pytest


class TestProceedIdempotency:
    """D10：同一会话/幂等键重复 proceed 命中在跑管线时返回既有 pipeline_id。"""

    def test_hit_when_running_or_queued(self) -> None:
        from clipwright.api import requirements as R

        R._PROCEED_IDEM.clear()
        try:
            R._PROCEED_IDEM["sess:s1"] = "pl_x"
            assert R._proceed_idem_hit("sess:s1", {"pl_x": object()}, {}) == "pl_x"
            assert R._proceed_idem_hit("sess:s1", {}, {"pl_x": "task_1"}) == "pl_x"
        finally:
            R._PROCEED_IDEM.clear()

    def test_miss_when_finished_or_unknown(self) -> None:
        from clipwright.api import requirements as R

        R._PROCEED_IDEM.clear()
        try:
            R._PROCEED_IDEM["sess:s1"] = "pl_x"
            # 管线已结束（不在 running/tasks 映射）→ 允许再次 proceed
            assert R._proceed_idem_hit("sess:s1", {}, {}) is None
            assert R._proceed_idem_hit("sess:other", {"pl_x": object()}, {}) is None
        finally:
            R._PROCEED_IDEM.clear()


class TestBackgroundHygiene:
    """D15：后台任务异常必须写日志；关闭时可批量取消。"""

    @pytest.mark.asyncio
    async def test_exception_is_logged(self, caplog) -> None:
        from clipwright.services.async_util import spawn_background

        async def boom() -> None:
            raise RuntimeError("bg boom")

        with caplog.at_level(logging.ERROR, logger="clipwright"):
            spawn_background(boom(), name="test-bg-boom")
            await asyncio.sleep(0.1)
        assert any("bg boom" in r.getMessage() for r in caplog.records), caplog.text

    @pytest.mark.asyncio
    async def test_cancel_all_background(self, monkeypatch) -> None:
        from clipwright.services import async_util as AU

        cancelled = asyncio.Event()

        async def waiter() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = AU.spawn_background(waiter(), name="test-bg-wait")
        await asyncio.sleep(0)
        # 隔离：只对本测试创建的任务执行批量取消，避免影响其他用例的 loop
        monkeypatch.setattr(AU, "_BACKGROUND_TASKS", {task})
        n = await AU.cancel_all_background(timeout=1.0)
        assert n == 1
        assert cancelled.is_set()
        assert task.cancelled()
