"""真实链路验证：加载插件 → 初始化工程/写场景 → 渲染回调（Remotion）→ 具象化 clip。

前置：`runtime/` 下已执行过 `npm install`（node_modules 不入库），且本机有 node/Chrome/ffmpeg。
用法：python scripts/e2e_render.py <plugin_root>
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

SCENE = """import React from 'react';
import {useCurrentFrame, useVideoConfig} from 'remotion';

export default function Scene() {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = Math.min(1, frame / Math.max(1, fps));
  return (
    <div style={{width: '100%', height: '100%', display: 'flex',
                 alignItems: 'center', justifyContent: 'center'}}>
      <div style={{width: 120, height: 120, borderRadius: 24,
                   background: '#ff5533', opacity: t}} />
    </div>
  );
}
"""


async def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    node = shutil.which("node")
    if not node:
        print("缺少 node，跳过")
        return 0
    if not (root / "runtime" / "node_modules").is_dir():
        print("runtime/node_modules 缺失：先在 runtime/ 执行 npm install")
        return 2

    from clipwright import config

    staging = Path(tempfile.mkdtemp(prefix="motion-e2e-"))
    config.settings.workspace_dir = str(staging / "ws")
    plugin_dir = staging / "motion_scene"
    plugin_dir.mkdir(parents=True)
    # 用符号链接不行（worker 走 realpath 外），把插件文件拷过去；runtime 也一起
    shutil.copytree(root, plugin_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git", "__pycache__"))

    from clipwright.plugins.loader import PluginLoader
    from clipwright.schema.timeline import Clip, Timeline, Track
    from clipwright.services.clip_kind_registry import ClipKindRegistry
    from clipwright.services.plugin_jobs import RuntimeSpec
    from clipwright.services.plugin_render import RenderScope, materialize_plugin_clips
    from clipwright.services.plugin_rpc import register_runtime

    register_runtime(RuntimeSpec(
        runtime_id="node", executable=node, version="24", approved=True,
    ))
    loader = PluginLoader(plugin_dir=staging, data_dir=staging / "PluginData")
    try:
        loader.load("motion_scene")
        host = loader._hosts["motion_scene"]
        scope = {"project_id": "proj-1", "task_id": "t1"}

        init = await host.invoke(
            "tool.call",
            {"id": "motion_project_init", "input": {"topic": "e2e"}, "scope": scope},
            scope=scope,
        )
        assert init["success"], init
        written = await host.invoke(
            "tool.call",
            {"id": "motion_scene_write",
             "input": {"scene_id": "001", "source": SCENE}, "scope": scope},
            scope=scope,
        )
        assert written["success"], written
        print("project:", init["output"], "scene:", written["output"])

        kind = ClipKindRegistry.get("motion_scene_clip")
        assert kind and kind.renderer_tool == "motion_render"

        timeline = Timeline(width=320, height=240, fps=30.0, duration_sec=1.0, tracks=[
            Track(id="t1", kind="motion_scene_clip", index=1, clips=[Clip(
                id="c1", kind="motion_scene_clip", asset_id="", track_id="t1",
                start_sec=0.0, duration_sec=1.0,
                metadata={"scene_path": "motion/scenes/scene-001.tsx"},
            )]),
        ])
        result = await materialize_plugin_clips(
            timeline,
            host=lambda _pid: host,
            scope=RenderScope("motion_scene", "proj-1", "t1"),
            width=320, height=240, fps=30.0,
        )
        clip = result.tracks[0].clips[0]
        print("kind:", clip.kind)
        print("asset:", clip.asset_id)
        print("metadata:", clip.metadata)
        assert clip.kind == "video"
        assert Path(clip.asset_id).is_file()
        return 0
    finally:
        loader.unload("motion_scene")
        loader.stop_all_hosts()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
