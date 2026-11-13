"""M2 e2e：插件经 call_host 读写 scoped workspace 并登记 artifact。

验证 scope 由宿主随 tool.call 下发、插件默认沿用；跨插件伪造 scope 被拒。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.plugins.runtime import PluginHost
from clipwright.plugins.runtime.protocol import RpcError
from clipwright.services.plugin_rpc import install_default_handlers
from clipwright.services.plugin_workspace import (
    PluginScope,
    PluginWorkspace,
)

PLUGIN_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class WsTool:",
    "    async def execute(self, **kwargs):",
    "        write = await call_host('workspace.write',",
    "                                {'path': 'motion/a.txt', 'data': 'hi'})",
    "        read = await call_host('workspace.read', {'path': 'motion/a.txt'})",
    "        art = await call_host('artifact.register',",
    "                              {'path': 'motion/a.txt', 'role': 'report'})",
    "        return {'success': True,",
    "                'output': {'write': write, 'read': read, 'artifact': art}}",
    "",
    "",
    "class BadScopeTool:",
    "    async def execute(self, **kwargs):",
    "        try:",
    "            await call_host('workspace.write',",
    "                            {'path': 'motion/x.txt', 'data': 'x'},",
    "                            scope={'plugin_id': 'other', 'project_id': 'p1'})",
    "        except Exception as exc:",
    "            return {'success': False,",
    "                    'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "        return {'success': True}",
    "",
    "",
    "class WsPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [",
    "            Contribution(ContributionKind.TOOL, 'probe_ws', WsTool(),",
    "                         meta={'name': 'probe_ws'}),",
    "            Contribution(ContributionKind.TOOL, 'probe_bad_scope', BadScopeTool(),",
    "                         meta={'name': 'probe_bad_scope'}),",
    "        ]",
    "",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    root = tmp_path / "plugins" / "ws_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: ws_probe\nname: ws probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    host = PluginHost(
        "ws_probe", root,
        tmp_path / "PluginData" / "plugins" / "ws_probe",
        call_timeout=10.0,
    )
    install_default_handlers(host)
    host.start()
    scope = {"project_id": "proj-1", "task_id": "t1"}
    yield host, scope, tmp_path
    host.stop()


class TestWorkspaceOverRpc:
    @pytest.mark.asyncio
    async def test_write_read_and_register(self, env):
        host, scope, _tmp = env
        result = await host.invoke(
            "tool.call", {"id": "probe_ws", "input": {}, "scope": scope},
            scope=scope,
        )
        assert result["success"] is True, result
        out = result["output"]
        assert out["read"]["data"] == "hi"
        assert out["artifact"]["mime"] == "text/plain"
        assert out["artifact"]["size"] == 2

        ws = PluginWorkspace(PluginScope("ws_probe", "proj-1", "t1"))
        assert (ws.root / "motion" / "a.txt").read_text(encoding="utf-8") == "hi"

    @pytest.mark.asyncio
    async def test_forged_scope_is_denied(self, env):
        host, scope, _tmp = env
        result = await host.invoke(
            "tool.call", {"id": "probe_bad_scope", "input": {}, "scope": scope},
            scope=scope,
        )
        assert result["success"] is False
        assert "PERMISSION_DENIED" in str(result["error"])

    @pytest.mark.asyncio
    async def test_missing_project_scope_denied(self, env):
        host, _scope, _tmp = env
        # 插件未捕获 → worker 把 tool.call 本身回成错误帧 → 宿主侧抛 RpcError
        with pytest.raises(RpcError, match="project_id"):
            await host.invoke(
                "tool.call", {"id": "probe_ws", "input": {}, "scope": {"task_id": "t1"}},
                scope={"task_id": "t1"},
            )
