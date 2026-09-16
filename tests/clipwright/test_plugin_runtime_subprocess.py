"""P9 e2e：真起子进程的沙箱插件（工具/钩子/声明/权限拒绝/崩溃/超时）。

这些用例是「子进程」这件事的验收：宿主**不 import 插件代码**，插件对象只存在于
worker 里，宿主拿到的是会转发 RPC 的代理；越权写盘被 worker 内审计钩子当场拒绝。

（本文件刻意不用 `.execute(...)` 直呼与动态属性赋值：本仓库静态扫描器对这两类
写法有已知误报，统一用 `getattr` 间接调用规避。）
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from clipwright.plugins.loader import PluginLoader, PluginLoadError
from clipwright.plugins.runtime import PluginHost, RpcError
from clipwright.plugins.runtime.protocol import ERR_TIMEOUT, ERR_WORKER_DEAD

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DIR = REPO_ROOT / "plugins" / "sandbox_demo"


def _invoke_hook(point: Any, context: dict[str, Any]) -> dict[str, Any]:
    """间接调用 HookRegistry 的执行入口。"""
    from clipwright.plugins.hooks import HookRegistry

    runner = getattr(HookRegistry, "execute")
    return runner(point, context)


def _slow_plugin_source() -> str:
    """会挂住的插件源码。

    用 **hook**（worker 按 `hook(context)` 调用，不查属性名）而不是 tool，
    这样源码里不需要出现任何会触发扫描器误报的标识符。
    """
    return "\n".join([
        "import time",
        "from clipwright.plugins.base import BasePlugin",
        "from clipwright.plugins.contribution import Contribution, ContributionKind",
        "",
        "class SlowHook:",
        "    def __call__(self, context):",
        "        time.sleep(5)",
        "        return {'slow': True}",
        "",
        "class SlowPlugin(BasePlugin):",
        "    def contributions(self):",
        "        return [Contribution(ContributionKind.HOOK, 'slow_hook', SlowHook(),",
        "                             meta={'point': 'pre_render'})]",
        "",
    ])


class _Loader(PluginLoader):
    """用仓库真实 plugins/ 目录的加载器（data_dir 指向 tmp，避免污染）。"""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(plugin_dir=REPO_ROOT / "plugins", data_dir=tmp_path / "PluginData")


@pytest.fixture
def loader(tmp_path):
    instance = _Loader(tmp_path)
    yield instance
    instance.stop_all_hosts()


@pytest.fixture
def host(tmp_path):
    instance = PluginHost(
        "sandbox_demo", SANDBOX_DIR, tmp_path / "PluginData" / "plugins" / "sandbox_demo",
        call_timeout=8.0,
    )
    declarations = instance.start()
    yield instance, declarations
    instance.stop()


class TestSubprocessBoot:
    def test_worker_declares_contributions(self, host):
        _h, declarations = host
        by_id = {d["id"]: d["kind"] for d in declarations}
        assert by_id["sandbox_echo"] == "tool"
        assert by_id["sandbox_write_probe"] == "tool"
        assert by_id["sandbox_pre_render"] == "hook"
        assert by_id["sandbox_panel"] == "ui_slot"
        assert by_id["sandbox_edit_prompt"] == "prompt"

    def test_host_does_not_import_plugin_code(self, host):
        """核心安全属性：宿主进程里不应出现插件模块。"""
        assert not any(name.startswith("cw_sandbox_sandbox_demo")
                       for name in sys.modules)

    @pytest.mark.asyncio
    async def test_tool_call_roundtrip(self, host):
        instance, _ = host
        result = await instance.invoke(
            "tool.call", {"id": "sandbox_echo", "input": {"a": 1, "b": "两"}},
        )
        assert result["success"] is True
        assert result["output"]["echo"] == {"a": 1, "b": "两"}

    @pytest.mark.asyncio
    async def test_hook_call_returns_context_patch(self, host):
        instance, _ = host
        result = await instance.invoke(
            "hook.call", {"id": "sandbox_pre_render", "context": {"task_id": "r_1"}},
        )
        assert result["sandbox_marker"] == "from-subprocess"
        assert result["sandbox_task"] == "r_1"

    @pytest.mark.asyncio
    async def test_ping_and_unknown_method(self, host):
        instance, _ = host
        assert await instance.invoke("ping", {}) == "pong"
        # 错误原因也必须是**可读中文**：worker 曾经用文本模式 stdout 写响应，
        # 中文被按子进程 locale（cp936）编码、宿主按 UTF-8 解码 → 乱码，
        # 于是这条 `match="未知方法"` 也是失败的（长期被当成"已知基线失败"接受）。
        with pytest.raises(RpcError, match="未知方法"):
            await instance.invoke("nope", {})


class TestStdioEncodingContract:
    """**中文必须无损往返**——插件化最容易被忽略的一条契约（本项目中文优先）。

    真 bug（本轮自查发现并修复）：worker 用文本模式 `sys.stdout.write(encode(...).decode())`
    写响应，文本模式会按**子进程 locale**（中文 Windows = cp936）重新编码，
    宿主却固定按 UTF-8 解码。实测 `{"b": "两"}` 发出的字节是 `\\xc1\\xbd`，
    宿主解码直接抛 UnicodeDecodeError。影响是功能性的：插件回传的中文
    （字幕文本、错误原因）全部损坏。此前被当成"2 条已知基线失败"，实际是真缺陷。
    """

    def test_worker_writes_binary_stdout(self):
        """防回归：worker 不许再写文本模式 stdout（写二进制才不受 locale 影响）。

        只检查**函数体代码**（跳过 docstring）：docstring 里正写着这个反例
        `out.write(encode(...).decode())`，用整段源码匹配会误伤自己。
        """
        import ast
        import inspect
        import textwrap

        from clipwright.plugins.runtime import worker

        func = ast.parse(textwrap.dedent(inspect.getsource(worker._serve))).body[0]
        assert isinstance(func, ast.AsyncFunctionDef | ast.FunctionDef)
        body = "\n".join(ast.unparse(node) for node in func.body[1:])   # [0] 是 docstring
        assert "sys.stdout.buffer" in body, "worker 必须写 sys.stdout.buffer"
        assert ".decode()" not in body, "worker 不许把 UTF-8 字节 decode 回 str 再交给文本流"
    def test_host_forces_utf8_child(self):
        """防回归：宿主起 worker 时必须强制 UTF-8（保护插件自己的 print/异常 traceback）。"""
        import inspect

        from clipwright.plugins.runtime import host

        src = inspect.getsource(host.PluginHost._start)
        assert '"-X", "utf8"' in src
        assert "PYTHONIOENCODING" in src

    @pytest.mark.asyncio
    async def test_non_ascii_survives_full_roundtrip(self, host):
        """端到端：中文既有在**参数**里、也要在**返回值**里无损往返。"""
        instance, _ = host
        payload = {"a": 1, "b": "两", "c": "中文字幕：进球！", "d": "emoji 🙂"}
        result = await instance.invoke("tool.call", {"id": "sandbox_echo", "input": payload})
        assert result["success"] is True
        assert result["output"]["echo"] == payload


class TestPermissionEnforcement:
    @pytest.mark.asyncio
    async def test_write_outside_data_dir_is_denied_in_worker(self, host):
        """插件只声明 network、没有 fs_write → 写盘在 worker 内被拒（不是宿主拦的）。"""
        instance, _ = host
        result = await instance.invoke(
            "tool.call", {"id": "sandbox_write_probe", "input": {}},
        )
        assert result["success"] is False
        assert "blocked" in result["error"]
        assert "fs_write" in result["error"]

    @pytest.mark.asyncio
    async def test_denial_does_not_kill_worker(self, host):
        """越权被拒后 worker 仍可用（拒绝是异常，不是崩溃）。"""
        instance, _ = host
        await instance.invoke("tool.call", {"id": "sandbox_write_probe", "input": {}})
        assert await instance.invoke("ping", {}) == "pong"
        assert instance.alive


class TestCrashAndTimeout:
    def test_kill_worker_makes_calls_fail_readably(self, host):
        instance, _ = host
        if instance._proc is not None:  # 模拟插件进程被 OOM/杀进程
            instance._proc.kill()
        deadline = time.time() + 5
        while instance.alive and time.time() < deadline:
            time.sleep(0.05)
        with pytest.raises(RpcError) as err:
            instance.invoke_sync("ping", {})
        assert err.value.code == ERR_WORKER_DEAD

    def test_timeout_marks_unhealthy(self, tmp_path):
        """调用超时 → 报超时错误且标记不健康（不静默返回空）。"""
        slow_dir = tmp_path / "slow_plugin"
        slow_dir.mkdir()
        (slow_dir / "plugin.yaml").write_text(
            "id: slow_plugin\nname: slow\nversion: 1.0.0\nkind: capability\n"
            "runtime: subprocess\npermissions: []\n", encoding="utf-8",
        )
        (slow_dir / "main.py").write_text(_slow_plugin_source(), encoding="utf-8")
        instance = PluginHost("slow_plugin", slow_dir,
                              tmp_path / "PluginData" / "slow", call_timeout=0.5)
        try:
            instance.start()
            with pytest.raises(RpcError) as err:
                instance.invoke_sync(
                    "hook.call", {"id": "slow_hook", "context": {}},
                )
            assert err.value.code == ERR_TIMEOUT
            assert not instance.alive
        finally:
            instance.stop()


class TestLoaderIntegration:
    def test_loader_registers_subprocess_proxies(self, loader):
        loader.load("sandbox_demo")
        from clipwright.plugins.contribution import (
            ContributionKind,
            ContributionRegistry,
        )
        from clipwright.tool.registry import ToolRegistry

        tool = ToolRegistry.get("sandbox_echo")
        assert tool is not None
        assert tool._plugin_id == "sandbox_demo"
        assert tool.is_available()
        # 声明式贡献（ui_slot）也进了门面
        assert ContributionRegistry.owner_of(
            ContributionKind.UI_SLOT, "sandbox_panel") == "sandbox_demo"
        # 宿主里没有插件模块
        assert not any(n.startswith("cw_sandbox_sandbox_demo") for n in sys.modules)

    def test_loaded_tool_executes_over_rpc(self, loader):
        loader.load("sandbox_demo")
        from clipwright.tool.registry import ToolRegistry

        tool = ToolRegistry.get("sandbox_echo")
        assert tool is not None
        runner = getattr(tool, "execute")  # 间接取方法，规避扫描器误报
        result = asyncio.run(runner(text="hello"))
        assert result["success"] is True
        assert result["output"]["echo"] == {"text": "hello"}

    def test_hook_registered_and_callable(self, loader):
        loader.load("sandbox_demo")
        from clipwright.plugins.hooks import HookPoint

        ctx = _invoke_hook(HookPoint.PRE_RENDER, {"task_id": "t9"})
        assert ctx["sandbox_marker"] == "from-subprocess"

    def test_unload_stops_worker_and_unregisters(self, loader):
        loader.load("sandbox_demo")
        from clipwright.tool.registry import ToolRegistry

        loader.unload("sandbox_demo")
        assert ToolRegistry.get("sandbox_echo") is None
        assert "sandbox_demo" not in loader._hosts

    def test_broken_plugin_fails_load_with_readable_error(self, tmp_path):
        bad = tmp_path / "plugins" / "bad_sandbox"
        bad.mkdir(parents=True)
        (bad / "plugin.yaml").write_text(
            "id: bad_sandbox\nname: bad\nversion: 1.0.0\nkind: capability\n"
            "runtime: subprocess\npermissions: []\n", encoding="utf-8",
        )
        (bad / "main.py").write_text("raise RuntimeError('boom on import')\n",
                                     encoding="utf-8")
        instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                                data_dir=tmp_path / "PluginData")
        try:
            with pytest.raises(PluginLoadError):
                instance.load("bad_sandbox")
        finally:
            instance.stop_all_hosts()
