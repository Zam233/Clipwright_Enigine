"""M3 e2e：插件经 call_host 只读素材、查同任务 artifact、拒绝未授权 scope。"""
from __future__ import annotations

import json

import pytest

from clipwright.plugins.runtime import PluginHost
from clipwright.services.plugin_rpc import install_default_handlers
from clipwright.services.workspace import Workspace

SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class MatTool:",
    "    async def execute(self, **kwargs):",
    "        items = await call_host('materials.list', {'limit': 5})",
    "        first = items[0] if items else None",
    "        data = await call_host('materials.read', {'ref': first['ref']}) if first else None",
    "        arts = await call_host('artifact.list', {'scope': 'task'})",
    "        denied = ''",
    "        try:",
    "            await call_host('artifact.list', {'scope': 'project'})",
    "        except Exception as exc:",
    "            denied = getattr(exc, 'code', '') + ':' + str(exc)",
    "        return {'success': True, 'output': {'items': items, 'data': data,",
    "                                            'arts': arts, 'denied': denied}}",
    "",
    "",
    "class MatPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'probe_materials',",
    "                             MatTool(), meta={'name': 'probe_materials'})]",
    "",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    root = Workspace("proj-1").dirs().materials
    root.mkdir(parents=True, exist_ok=True)
    (root / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 16)

    reg = Workspace("proj-1").dirs().root / ".engine" / "artifacts"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "other.json").write_text(json.dumps({"artifacts": [{
        "artifact_id": "art_other", "plugin_id": "other", "task_id": "t1",
        "project_id": "proj-1", "path": "x.png", "mime": "image/png",
        "size": 1, "sha256": "x", "role": "image", "status": "ready",
        "created_at": 1.0,
    }]}), encoding="utf-8")

    plugin_dir = tmp_path / "plugins" / "mat_probe"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: mat_probe\nname: mat probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: tool\n    id: probe_materials\n",
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(SOURCE, encoding="utf-8")
    host = PluginHost("mat_probe", plugin_dir,
                      tmp_path / "PluginData" / "mat_probe")
    install_default_handlers(host)
    host.start()
    scope = {"project_id": "proj-1", "task_id": "t1"}
    yield host, scope
    host.stop()


@pytest.mark.asyncio
async def test_materials_and_task_artifacts(env):
    host, scope = env
    result = await host.invoke(
        "tool.call", {"id": "probe_materials", "input": {}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is True, result
    out = result["output"]
    assert out["items"][0]["ref"].startswith("materials/")
    assert out["data"]["data_b64"]
    assert [a["artifact_id"] for a in out["arts"]] == ["art_other"]
    assert "PERMISSION_DENIED" in out["denied"]
