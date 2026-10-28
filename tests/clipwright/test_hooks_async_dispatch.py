"""P9 测试：钩子异步调度（execute_async）——同步钩子语义不变 + 异步钩子被 await。"""
from __future__ import annotations

import asyncio

import pytest

from clipwright.plugins.hooks import HookPoint, HookRegistry
from clipwright.plugins.hooks_async import execute_async


@pytest.fixture(autouse=True)
def _clean_hooks():
    HookRegistry.clear()
    yield
    HookRegistry.clear()


class TestSyncHooksThroughAsyncPath:
    def test_sync_hook_keeps_semantics(self):
        def sync_hook(ctx: dict) -> dict:
            return {"patched": True}

        HookRegistry.register(HookPoint.PRE_RENDER, sync_hook, plugin_id="p1")
        ctx = asyncio.run(execute_async(HookPoint.PRE_RENDER, {"task_id": "t"}))
        assert ctx["patched"] is True
        assert ctx["task_id"] == "t"

    def test_hook_exception_does_not_block_others(self):
        def boom(ctx):
            raise RuntimeError("x")

        def ok(ctx):
            return {"second": 1}

        HookRegistry.register(HookPoint.PRE_RENDER, boom, plugin_id="p1")
        HookRegistry.register(HookPoint.PRE_RENDER, ok, plugin_id="p2")
        ctx = asyncio.run(execute_async(HookPoint.PRE_RENDER, {}))
        assert ctx == {"second": 1}

    def test_falsy_result_ignored(self):
        HookRegistry.register(HookPoint.PRE_RENDER, lambda ctx: None, plugin_id="p1")
        HookRegistry.register(HookPoint.PRE_RENDER, lambda ctx: {}, plugin_id="p2")
        assert asyncio.run(execute_async(HookPoint.PRE_RENDER, {"a": 1})) == {"a": 1}


class TestAsyncHooksPreferred:
    def test_acall_is_used_and_awaited(self):
        """有 acall 的钩子（子进程代理）走异步路径，同步 __call__ 不被调用。"""
        calls: list[str] = []

        class AsyncHook:
            def __call__(self, ctx):  # pragma: no cover - 不应被调用
                calls.append("sync")
                return {"from": "sync"}

            async def acall(self, ctx):
                calls.append("async")
                await asyncio.sleep(0)
                return {"from": "async"}

        HookRegistry.register(HookPoint.PRE_RENDER, AsyncHook(), plugin_id="p1")
        ctx = asyncio.run(execute_async(HookPoint.PRE_RENDER, {}))
        assert ctx["from"] == "async"
        assert calls == ["async"]

    def test_async_hook_exception_isolated(self):
        class Boom:
            async def acall(self, ctx):
                raise RuntimeError("async boom")

        HookRegistry.register(HookPoint.PRE_RENDER, Boom(), plugin_id="p1")
        assert asyncio.run(execute_async(HookPoint.PRE_RENDER, {"k": 1})) == {"k": 1}

    def test_hook_list_is_a_copy(self):
        def h(ctx):
            return None

        HookRegistry.register(HookPoint.PRE_RENDER, h, plugin_id="p1")
        listed = HookRegistry.hook_list(HookPoint.PRE_RENDER)
        assert len(listed) == 1
        listed.clear()
        assert len(HookRegistry.hook_list(HookPoint.PRE_RENDER)) == 1  # 内部表未被改


class TestPipelineCallSitesUseAsyncPath:
    """回归：异步调用点必须走 execute_async（否则子进程 hook 会阻塞事件循环）。"""

    def test_async_call_sites_updated(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        # M3：老管线（services/pipeline_v2.py）已删除，调用点只剩渲染与管线 API
        for rel in ("clipwright/api/render.py", "clipwright/api/pipeline.py"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "execute_async" in text, rel
            assert "HookRegistry.execute(" not in text, f"{rel} 仍有同步调用点"

    def test_sync_call_sites_kept_for_sync_paths(self):
        """同步路径（动画目录/图解钩子）保持同步调用，语义未变。"""
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for rel in ("clipwright/animation/catalog.py", "clipwright/animation/diagram_svg.py"):
            text = (root / rel).read_text(encoding="utf-8")
            assert "HookRegistry.execute(" in text, rel
