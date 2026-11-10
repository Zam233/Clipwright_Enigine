"""加载冒烟：用 ClipWright engine 的 PluginLoader 真加载本插件。

用法（在装有 clipwright 的 Python 里）：
    python scripts/smoke.py <plugin_root>

脚本会把插件文件复制到临时 staging 目录（结构 `<parent>/motion_scene/`），
避免污染工作区；成功时打印 category / clip_kind 注册结果。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    if not (root / "plugin.yaml").exists():
        print(f"不是插件目录（缺 plugin.yaml）: {root}")
        return 2

    from clipwright.category.registry import CategoryRegistry
    from clipwright.plugins.loader import PluginLoader
    from clipwright.services.clip_kind_registry import ClipKindRegistry

    staging = Path(tempfile.mkdtemp(prefix="motion-smoke-"))
    plugin_dir = staging / "motion_scene"
    plugin_dir.mkdir(parents=True)
    for name in ("plugin.yaml", "main.py"):
        shutil.copy2(root / name, plugin_dir / name)

    loader = PluginLoader(plugin_dir=staging, data_dir=staging / "PluginData")
    try:
        loader.load("motion_scene")
        category = CategoryRegistry.get("motion_scene")
        kind = ClipKindRegistry.get("motion_scene_clip")
        assert category is not None, "category 未注册"
        assert kind is not None, "clip_kind 未注册"
        bundle = category.get_prompt_bundle()
        print("category:", category.display_name)
        print("prompt slots:", sorted(bundle.slots))
        print("clip_kind:", kind.kind_id, "renderer:", kind.renderer_tool)
        return 0
    finally:
        loader.unload("motion_scene")
        loader.stop_all_hosts()


if __name__ == "__main__":
    raise SystemExit(main())
