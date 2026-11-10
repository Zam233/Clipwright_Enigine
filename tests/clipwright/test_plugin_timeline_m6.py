"""M6：插件读取任务时间线只读摘要。"""
from __future__ import annotations

import pytest

from clipwright.plugins.runtime import PluginHost
from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.plugin_rpc import (
    install_default_handlers,
    register_timeline,
    unregister_timeline,
)

SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class SummaryTool:",
    "    async def execute(self, **kwargs):",
    "        try:",
    "            data = await call_host('timeline.summary', {})",
    "        except Exception as exc:",
    "            return {'success': False,",
    "                    'error': getattr(exc, 'code', '') + ': ' + str(exc)}",
    "        return {'success': True, 'output': data}",
    "",
    "",
    "class SummaryPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'probe_summary',",
    "                             SummaryTool(), meta={'name': 'probe_summary'})]",
    "",
])


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "plugins" / "sum_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: sum_probe\nname: sum probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: tool\n    id: probe_summary\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    host = PluginHost("sum_probe", root, tmp_path / "PluginData" / "sum_probe")
    install_default_handlers(host)
    host.start()
    scope = {"project_id": "proj-1", "task_id": "t1"}
    yield host, scope
    host.stop()
    unregister_timeline("t1")


def _timeline() -> Timeline:
    return Timeline(width=640, height=360, fps=25.0, duration_sec=6.0, tracks=[
        Track(id="t0", kind="video", index=0, clips=[
            Clip(id="c1", kind="video", asset_id="a", track_id="t0",
                 start_sec=0.0, duration_sec=3.0),
            Clip(id="c2", kind="video", asset_id="b", track_id="t0",
                 start_sec=3.0, duration_sec=3.0),
        ]),
    ])


@pytest.mark.asyncio
async def test_summary_returns_metrics(env):
    host, scope = env
    register_timeline("t1", _timeline())
    result = await host.invoke(
        "tool.call", {"id": "probe_summary", "input": {}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is True, result
    out = result["output"]
    assert out == {
        "duration_sec": 6.0, "width": 640, "height": 360, "fps": 25.0,
        "track_count": 1, "clip_count": 2,
    }


@pytest.mark.asyncio
async def test_summary_missing_timeline_errors(env):
    host, scope = env
    unregister_timeline("t1")
    result = await host.invoke(
        "tool.call", {"id": "probe_summary", "input": {}, "scope": scope},
        scope=scope,
    )
    assert result["success"] is False
    assert "NOT_FOUND" in str(result["error"])
