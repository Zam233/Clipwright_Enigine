"""插件健康体检（一次性审查工具）。

逐个加载 plugins/*，输出三类问题的可复核证据：
1. **声明与注册不一致**：manifest 声明了但运行时没注册（declared_only）、
   注册了却没声明（会在加载时被门面拒绝，此处看错误通道）；
2. **零贡献但加载成功**：这类插件在管理页显示「已加载」，用户以为有功能；
3. **结构性问题**：类里既无 initialize 也无 contributions（注册发生在 import 期）、
   权限声明、shutdown 里的注册表直调、以及旧轨/新轨标记。

用法：python scripts/plugin_health_report.py [--json 输出路径]
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from clipwright.plugins.contribution import ContributionRegistry  # noqa: E402
from clipwright.plugins.loader import PluginLoader  # noqa: E402


def inspect(pid: str, plugin_dir: Path, data_dir: Path) -> dict:
    from clipwright.plugins.error_bus import get_error_bus

    loader = PluginLoader(plugin_dir=plugin_dir, data_dir=data_dir)
    entry: dict = {"id": pid}
    try:
        plugin = loader.load(pid)
    except Exception as e:  # noqa: BLE001
        entry["load_error"] = f"{type(e).__name__}: {e}"
        loader.stop_all_hosts()
        return entry

    contributions = ContributionRegistry.list_by_owner(pid)
    registered = sorted(f"{c.kind.value}:{c.id}" for c in contributions)
    manifest = loader.get_metadata(pid) if hasattr(loader, "get_metadata") else None
    if manifest is None:
        # 子进程插件没有宿主侧实例（load() 返回 None）——元数据从 _metadatas 取
        manifest = next(
            (m.manifest for m in loader.list_loaded() if m.manifest.id == pid), None
        )
    if manifest is None:
        entry["load_error"] = "已加载但元数据缺失"
        loader.stop_all_hosts()
        return entry
    declared = sorted(f"{d.kind}:{d.id}" for d in (manifest.contributions or []))
    entry["registered"] = registered
    entry["declared"] = declared
    entry["declared_only"] = sorted(set(declared) - set(registered))
    entry["undeclared"] = sorted(set(registered) - set(declared))
    entry["permissions"] = list(getattr(manifest, "permissions", []) or [])
    entry["runtime"] = str(getattr(manifest, "runtime", "inprocess"))
    entry["api_version"] = str(getattr(manifest, "api_version", "1"))

    klass = type(plugin) if plugin is not None else None
    entry["class_defines_initialize"] = bool(klass and "initialize" in klass.__dict__)
    entry["class_defines_contributions"] = bool(klass and "contributions" in klass.__dict__)
    entry["class_defines_setup"] = bool(klass and "setup" in klass.__dict__)
    if klass is None:
        entry["structure_note"] = "子进程档（宿主无实例）"
    elif not entry["class_defines_initialize"] and not entry["class_defines_contributions"]:
        entry["structure_note"] = "既无 initialize 也无 contributions（注册可能发生在 import 期）"
    elif not entry["class_defines_initialize"] and entry["api_version"] == "1":
        entry["structure_note"] = "未迁移：无 initialize 却仍是旧契约版本号"

    errors = [e for e in get_error_bus().list(limit=200) if e.get("plugin_id") == pid]
    entry["error_channel"] = [f"{e.get('phase')}: {e.get('message')}" for e in errors]

    loader.unload(pid)
    loader.stop_all_hosts()
    return entry


def main() -> int:
    root = REPO / "plugins"
    ids = sorted(p.name for p in root.iterdir()
                 if p.is_dir() and (p / "plugin.yaml").exists())
    report = []
    for pid in ids:
        ContributionRegistry.unregister_plugin(pid)
        report.append(inspect(pid, root, Path(tempfile.mkdtemp()) / "PluginData"))

    issues = 0
    for entry in report:
        flags = []
        if entry.get("load_error"):
            flags.append("LOAD_ERROR")
        if entry.get("declared_only"):
            flags.append(f"declared_only={len(entry['declared_only'])}")
        if entry.get("undeclared"):
            flags.append(f"undeclared={len(entry['undeclared'])}")
        if entry.get("structure_note"):
            flags.append("structure")
        if entry.get("error_channel"):
            flags.append(f"errors={len(entry['error_channel'])}")
        if not entry.get("registered") and not entry.get("load_error"):
            flags.append("ZERO_CONTRIB")
        if flags:
            issues += 1
            print(f"{entry['id']:26s} {' '.join(flags)}")
            for key in ("load_error", "declared_only", "undeclared", "structure_note"):
                if entry.get(key):
                    print(f"    {key}: {entry[key]}")
            for msg in entry.get("error_channel", [])[:3]:
                print(f"    error: {msg}")
    print(f"\n插件数 {len(report)}，有问题 {issues}")

    if "--json" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已写出:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
