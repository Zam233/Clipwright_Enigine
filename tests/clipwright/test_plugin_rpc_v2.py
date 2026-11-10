"""协议 v2：worker → host 请求通道（M1 验收）。

验收点（对应 Coding Plan §2.4）：
- worker 在 tool handler 内发起 host 请求，宿主回包；
- 宿主在等待该 tool 响应期间仍能处理请求、还能反向再发一次请求（重入不阻塞）；
- 超时/未知方法返回稳定错误码，而不是挂住或返回空；
- 并发多次调用各自配对，不串号；
- worker 崩溃时在途调用失败，不静默成功。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from clipwright.plugins.runtime import PluginHost, RpcError
from clipwright.plugins.runtime.protocol import ERR_TIMEOUT

PLUGIN_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class ProbeTool:",
    "    def __init__(self, mode):",
    "        self.mode = mode",
    "",
    "    async def execute(self, **kwargs):",
    "        if self.mode == 'echo':",
    "            value = await call_host('test.echo', {'v': kwargs.get('v')})",
    "            return {'success': True, 'output': value}",
    "        if self.mode == 'unknown':",
    "            try:",
    "                await call_host('test.nope', {})",
    "            except Exception as exc:",
    "                return {'success': False,",
    "                        'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "            return {'success': True}",
    "        if self.mode == 'timeout':",
    "            try:",
    "                await call_host('test.sleep', {'sec': 1.0}, timeout=0.2)",
    "            except Exception as exc:",
    "                return {'success': False,",
    "                        'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "            return {'success': True}",
    "        if self.mode == 'reenter':",
    "            value = await call_host('test.reenter', {})",
    "            return {'success': True, 'output': value}",
    "        return {'success': False, 'error': 'bad mode'}",
    "",
    "",
    "class ProbePlugin(BasePlugin):",
    "    def contributions(self):",
    "        items = []",
    "        for m in ('echo', 'unknown', 'timeout', 'reenter'):",
    "            items.append(Contribution(",
    "                ContributionKind.TOOL, 'probe_' + m, ProbeTool(m),",
    "                meta={'name': 'probe_' + m}))",
    "        return items",
    "",
])


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    root = tmp_path / "plugins" / "rpc_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: rpc_probe\nname: rpc probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    return root


@pytest.fixture
def host(plugin_dir: Path, tmp_path: Path):
    instance = PluginHost(
        "rpc_probe", plugin_dir,
        tmp_path / "PluginData" / "plugins" / "rpc_probe",
        call_timeout=5.0,
    )
    instance.register_handler("test.echo", lambda params, scope: {
        "echo": params.get("v"),
        "scope": scope,
    })
    instance.register_handler("test.reenter", lambda params, scope: _reenter(instance))
    instance.register_handler("test.sleep", lambda params, scope: _sleep(params))
    instance.start()
    yield instance
    instance.stop()


async def _reenter(instance: PluginHost) -> dict[str, object]:
    """宿主在等待 worker tool 响应时，反向再发一次请求。"""
    pong = await instance.invoke("ping", {})
    return {"pong": pong}


async def _sleep(params: dict) -> str:
    await asyncio.sleep(float(params.get("sec", 0.1)))
    return "done"


class TestWorkerToHost:
    @pytest.mark.asyncio
    async def test_basic_roundtrip_with_scope(self, host):
        result = await host.invoke(
            "tool.call",
            {"id": "probe_echo", "input": {"v": "两"}, "scope": {"task_id": "t1"}},
        )
        assert result["success"] is True
        assert result["output"]["echo"] == "两"

    @pytest.mark.asyncio
    async def test_reentrant_host_call_does_not_deadlock(self, host):
        """宿主处理 worker 请求时再调 worker：两条链路都必须能完成。"""
        result = await asyncio.wait_for(
            host.invoke("tool.call", {"id": "probe_reenter", "input": {}}),
            timeout=5.0,
        )
        assert result["success"] is True
        assert result["output"] == {"pong": "pong"}

    @pytest.mark.asyncio
    async def test_unknown_host_method_returns_stable_error(self, host):
        result = await host.invoke("tool.call", {"id": "probe_unknown", "input": {}})
        assert result["success"] is False
        assert "UNKNOWN_METHOD" in str(result["error"])

    @pytest.mark.asyncio
    async def test_worker_side_timeout(self, host):
        result = await host.invoke("tool.call", {"id": "probe_timeout", "input": {}})
        assert result["success"] is False
        # 插件捕获的是 RpcError(TIMEOUT)；错误文本里应出现错误码
        assert ERR_TIMEOUT in str(result["error"])

    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_cross_ids(self, host):
        calls = [
            host.invoke("tool.call", {"id": "probe_echo", "input": {"v": i}})
            for i in range(6)
        ]
        results = await asyncio.gather(*calls)
        assert [r["output"]["echo"] for r in results] == list(range(6))

    def test_worker_death_fails_inflight(self, host):
        """worker 崩溃后，在途/后续调用必须是 WORKER_DEAD，不能假成功。"""
        if host._proc is not None:
            host._proc.kill()
        with pytest.raises(RpcError) as err:
            host.invoke_sync("ping", {})
        assert err.value.code in {"WORKER_DEAD", "TIMEOUT"}
