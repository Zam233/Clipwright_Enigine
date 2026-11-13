"""M7：引擎能力受控调用（默认白名单 / 未授权拒绝 / 无实现返回 UNAVAILABLE）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.runtime import PluginHost
from clipwright.services.plugin_rpc import install_default_handlers

SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class CapTool:",
    "    async def execute(self, name='media_probe', **kwargs):",
    "        try:",
    "            data = await call_host('capability.call',",
    "                                   {'name': name, 'input': {'x': 1}})",
    "        except Exception as exc:",
    "            return {'success': False,",
    "                    'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "        return {'success': True, 'output': data}",
    "",
    "",
    "class CapPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'probe_cap', CapTool(),",
    "                             meta={'name': 'probe_cap'})]",
    "",
])


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "plugins" / "cap_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: cap_probe\nname: cap probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: tool\n    id: probe_cap\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    host = PluginHost("cap_probe", root, tmp_path / "PluginData" / "cap_probe")
    install_default_handlers(host)
    host.start()
    scope = {"project_id": "proj-1", "task_id": "t1"}
    yield host, scope
    host.stop()


@pytest.mark.asyncio
async def test_unlisted_capability_denied(env):
    host, scope = env
    result = await host.invoke(
        "tool.call", {"id": "probe_cap", "input": {"name": "llm"}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is False
    assert "PERMISSION_DENIED" in str(result["error"])


@pytest.mark.asyncio
async def test_missing_implementation_unavailable(env):
    host, scope = env
    result = await host.invoke(
        "tool.call", {"id": "probe_cap", "input": {"name": "media_probe"}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is False
    assert "UNAVAILABLE" in str(result["error"])


@pytest.mark.asyncio
async def test_allowed_capability_executes(env):
    host, scope = env
    from clipwright.tool.base import BaseTool, ToolExecResult
    from clipwright.tool.registry import ToolRegistry

    class _Probe(BaseTool):
        name = "fake_media_probe"
        description = "fake"
        capability = "media_probe"

        async def execute(self, **kwargs):
            return ToolExecResult(success=True, output={"got": kwargs})

    tool = _Probe()
    ToolRegistry.register(tool)
    try:
        result = await host.invoke(
            "tool.call", {"id": "probe_cap", "input": {"name": "media_probe"}, "scope": scope},
            scope=scope,
        )
    finally:
        ToolRegistry.unregister("fake_media_probe")
    assert result["success"] is True, result
    assert result["output"]["output"]["got"] == {"x": 1}
