"""M4–M6 跨段 e2e：subprocess 插件声明 clip_kind 与 renderer → 渲染时回调 →
workspace 写文件 → 登记 artifact → 宿主解析并具象化为视频 overlay。"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.plugins.loader import PluginLoader
from clipwright.schema.timeline import Clip, Timeline, Track
from clipwright.services.clip_kind_registry import ClipKindRegistry
from clipwright.services.plugin_render import RenderScope, materialize_plugin_clips
from clipwright.services.workspace import Workspace

PLUGIN_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "from clipwright.plugins.runtime.bridge import call_host",
    "",
    "",
    "class RenderTool:",
    "    async def execute(self, **kwargs):",
    "        request = kwargs.get('request') or {}",
    "        await call_host('workspace.write',",
    "                        {'path': 'renders/scene.mov', 'data': 'FAKE'})",
    "        art = await call_host('artifact.register',",
    "                              {'path': 'renders/scene.mov', 'role': 'render'})",
    "        return {'success': True, 'output': {",
    "            'artifact_id': art['artifact_id'],",
    "            'mime': 'video/quicktime',",
    "            'width': request.get('width'),",
    "            'height': request.get('height'),",
    "            'fps': request.get('fps'),",
    "            'duration': request.get('duration_sec'),",
    "            'alpha': True,",
    "        }}",
    "",
    "",
    "class MotionPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [",
    "            Contribution(ContributionKind.CLIP_KIND, 'probe_scene', None, meta={",
    "                'display_name': '探针场景',",
    "                'render_contract': 'media_overlay.v1',",
    "                'preview_contract': 'artifact.v1',",
    "                'renderer_tool': 'probe_render',",
    "            }),",
    "            Contribution(ContributionKind.TOOL, 'probe_render', RenderTool(),",
    "                         meta={'name': 'probe_render'}),",
    "        ]",
    "",
])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from clipwright import config

    monkeypatch.setattr(config.settings, "workspace_dir", str(tmp_path / "ws"), raising=False)
    root = tmp_path / "plugins" / "motion_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: motion_probe\nname: motion probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n"
        "  - kind: clip_kind\n    id: probe_scene\n"
        "  - kind: tool\n    id: probe_render\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    loader = PluginLoader(plugin_dir=tmp_path / "plugins",
                          data_dir=tmp_path / "PluginData")
    loader.load("motion_probe")
    yield loader, tmp_path
    loader.unload("motion_probe")
    loader.stop_all_hosts()
    ClipKindRegistry.clear()


def _probe(path) -> dict:
    assert Path(path).is_file(), "宿主解析出的 artifact 路径必须存在"
    return {"width": 1920, "height": 1080, "fps": 30.0, "duration": 2.0,
            "pix_fmt": "yuva420p"}


@pytest.mark.asyncio
async def test_plugin_kind_render_chain(env):
    loader, _tmp = env
    assert ClipKindRegistry.get("probe_scene").owner_plugin == "motion_probe"

    clip = Clip(id="c1", kind="probe_scene", asset_id="", track_id="t1",
                start_sec=0.0, duration_sec=2.0)
    timeline = Timeline(width=1920, height=1080, fps=30.0, tracks=[
        Track(id="t1", kind="probe_scene", index=1, clips=[clip]),
    ])

    host = loader._hosts["motion_probe"]
    result = await materialize_plugin_clips(
        timeline,
        host=lambda _pid: host,
        scope=RenderScope("motion_probe", "proj-1", "t1"),
        width=1920, height=1080, fps=30.0, probe=_probe,
    )

    rewritten = result.tracks[0]
    assert rewritten.kind == "video"
    assert rewritten.clips[0].kind == "video"
    assert rewritten.clips[0].metadata["alpha"] is True
    assert rewritten.clips[0].metadata["plugin_kind"] == "probe_scene"

    # 文件确实落在插件命名空间，且 artifact 已登记
    root = Workspace("proj-1").dirs().root
    media = root / "plugins" / "motion_probe" / "renders" / "scene.mov"
    assert media.is_file()
    assert str(rewritten.clips[0].asset_id) == str(media.resolve())
