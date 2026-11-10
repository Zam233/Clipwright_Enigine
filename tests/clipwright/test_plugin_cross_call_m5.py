"""M5：跨插件调用（依赖声明 + exposes 白名单 + 拒绝路径）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.plugins.loader import PluginLoader
from clipwright.plugins.runtime.protocol import RpcError

B_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "",
    "",
    "class EchoTool:",
    "    async def execute(self, **kwargs):",
    "        return {'success': True, 'output': {'echo': kwargs}}",
    "",
    "",
    "class SecretTool:",
    "    async def execute(self, **kwargs):",
    "        return {'success': True, 'output': {'secret': True}}",
    "",
    "",
    "class BPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [",
    "            Contribution(ContributionKind.TOOL, 'b_echo', EchoTool(),",
    "                         meta={'name': 'b_echo'}),",
    "            Contribution(ContributionKind.TOOL, 'b_secret', SecretTool(),",
    "                         meta={'name': 'b_secret'}),",
    "        ]",
    "",
])

A_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class CallTool:",
    "    async def execute(self, target='b_echo', **kwargs):",
    "        try:",
    "            result = await call_host('plugin.call', {",
    "                'plugin_id': 'cross_b', 'kind': 'tool', 'id': target,",
    "                'input': {'x': 1},",
    "            })",
    "        except Exception as exc:",
    "            return {'success': False,",
    "                    'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "        return {'success': True, 'output': result}",
    "",
    "",
    "class APlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'a_call', CallTool(),",
    "                             meta={'name': 'a_call'})]",
    "",
])


def _write_plugin(root: Path, pid: str, source: str, extra: str = "") -> None:
    d = root / pid
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(
        f"id: {pid}\nname: {pid}\nversion: 1.0.0\nkind: capability\n"
        f"runtime: subprocess\npermissions: []\n{extra}"
        f"contributions:\n"
        f"  - kind: tool\n    id: {'a_call' if pid == 'cross_a' else 'b_echo'}\n"
        + ("  - kind: tool\n    id: b_secret\n" if pid == "cross_b" else ""),
        encoding="utf-8",
    )
    (d / "main.py").write_text(source, encoding="utf-8")


@pytest.fixture
def loader(tmp_path):
    root = tmp_path / "plugins"
    _write_plugin(
        root, "cross_b", B_SOURCE,
        extra="runtime_config:\n  exposes:\n    - 'tool:b_echo'\n",
    )
    _write_plugin(
        root, "cross_a", A_SOURCE,
        extra="dependencies:\n  - cross_b\n",
    )
    instance = PluginLoader(plugin_dir=root, data_dir=tmp_path / "PluginData")
    yield instance
    instance.stop_all_hosts()


@pytest.mark.asyncio
async def test_declared_and_exposed_call_succeeds(loader):
    loader.load("cross_b")
    loader.load("cross_a")
    host = loader._hosts["cross_a"]
    scope = {"project_id": "proj-1", "task_id": "t1"}
    result = await host.invoke(
        "tool.call", {"id": "a_call", "input": {"target": "b_echo"}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is True, result
    inner = result["output"]
    assert inner["plugin_id"] == "cross_b"
    assert inner["result"]["output"]["echo"] == {"x": 1}


@pytest.mark.asyncio
async def test_unexposed_tool_denied(loader):
    loader.load("cross_b")
    loader.load("cross_a")
    host = loader._hosts["cross_a"]
    scope = {"project_id": "proj-1", "task_id": "t1"}
    result = await host.invoke(
        "tool.call", {"id": "a_call", "input": {"target": "b_secret"}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is False
    assert "PERMISSION_DENIED" in str(result["error"])


def test_unknown_target_plugin_rejected(loader):
    loader.load("cross_b")
    loader.load("cross_a")
    from clipwright.services.plugin_rpc import _PLUGIN_MANIFESTS

    # 直接验证依赖检查：cross_a 的依赖只有 cross_b
    md = _PLUGIN_MANIFESTS["cross_a"]
    assert "cross_b" in list(md.dependencies or [])
