"""迁移基线快照（P1）：逐个加载 plugins/*，记录每个插件在各注册表里的贡献 id。

用途：迁移 29 个内置插件「旧轨 initialize() → 新契约 contributions()」前后各跑一次，
逐项 diff 证明行为等价（迁移不改用户可见能力）。一次性工具，不属于运行时。

用法：python scripts/plugin_contribution_snapshot.py <输出 json>
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from clipwright.plugins.loader import PluginLoader  # noqa: E402


def _plugins_root() -> Path:
    return REPO / "plugins"


def collect(out_path: Path) -> dict:
    from clipwright.agents.registry import AgentRegistry
    from clipwright.animation.registry import AnimationRegistry
    from clipwright.category.registry import CategoryRegistry
    from clipwright.material.registry import MaterialRegistry
    from clipwright.plugins.hooks import HookPoint, HookRegistry
    from clipwright.skill.registry import SkillRegistry
    from clipwright.tool.registry import ToolRegistry

    root = _plugins_root()
    ids = sorted(p.name for p in root.iterdir()
                 if p.is_dir() and (p / "plugin.yaml").exists())
    snap: dict = {}
    for pid in ids:
        loader = PluginLoader(plugin_dir=root, data_dir=Path(tempfile.mkdtemp()) / "PluginData")
        entry: dict = {}
        try:
            loader.load(pid)
        except Exception as e:  # noqa: BLE001 — 记录而非中断，便于看到全部插件
            entry["error"] = f"{type(e).__name__}: {e}"
            snap[pid] = entry
            loader.stop_all_hosts()
            continue
        entry["tools"] = sorted(str(x) for x in ToolRegistry.list_by_plugin(pid))
        entry["skills"] = sorted(str(x) for x in SkillRegistry.list_by_plugin(pid))
        entry["agents"] = sorted(str(x) for x in AgentRegistry.list_by_plugin(pid))
        entry["categories"] = sorted(
            cid for cid, owner in CategoryRegistry._owner_plugin.items() if owner == pid
        )
        entry["material"] = sorted(
            sid for sid, src in MaterialRegistry._sources.items()
            if getattr(src, "_plugin_id", "") == pid
        )
        entry["animations"] = sorted(
            aid for aid, owner in (getattr(AnimationRegistry, "_plugin_map", {}) or {}).items()
            if owner == pid
        )
        entry["hooks"] = sorted(
            str(getattr(fn, "__plugin_id__", "")) for point in HookPoint
            for fn in HookRegistry._hooks.get(point, [])
            if getattr(fn, "__plugin_id__", "") == pid
        )
        loader.unload(pid)
        loader.stop_all_hosts()
        snap[pid] = entry
    out_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
    return snap


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "plugin_snapshot.json")
    data = collect(target)
    print("plugins:", len(data))
    for pid, entry in data.items():
        if entry.get("error"):
            print("  ERR", pid, entry["error"][:90])
        else:
            print(f"  {pid}: {sum(len(v) for v in entry.values())} 项")
