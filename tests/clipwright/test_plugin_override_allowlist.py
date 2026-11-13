"""B7：override 允许表必须覆盖全部随应用发布的覆盖插件。

2026-11-13 实测踩到：tts_feature 除 `skill:dub_script` 外还声明
`ui_slot:create_dub_entry`，白名单漏列导致整个插件加载失败。此测试把
「允许表 ⊇ 所有 bundled override 插件的声明」变成构建期约束。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from clipwright.plugins.override_policy import ALLOWED_BUILTIN_OVERRIDES

REPO = Path(__file__).resolve().parents[2]


def _override_manifests() -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for plugin_dir in sorted((REPO / "plugins").iterdir()):
        manifest = plugin_dir / "plugin.yaml"
        if not manifest.exists():
            continue
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if not data.get("override_builtin"):
            continue
        declared = [
            f"{item.get('kind')}:{item.get('id')}"
            for item in (data.get("contributions") or [])
        ]
        out.append((plugin_dir.name, declared))
    return out


def test_every_bundled_override_target_is_allowlisted():
    offenders: dict[str, list[str]] = {}
    for plugin_id, declared in _override_manifests():
        missing = [key for key in declared if key not in ALLOWED_BUILTIN_OVERRIDES]
        if missing:
            offenders[plugin_id] = missing
    assert offenders == {}, (
        "这些 bundled 覆盖插件声明的目标未进允许表（会导致整个插件加载失败）："
        f"{offenders}"
    )


def test_override_plugins_actually_load():
    """允许表对齐后，每个声明 override_builtin 的 bundled 插件都应能加载。

    conftest 会用 `tests_*` 测试替身占用部分 (kind,id)（如 llm_provider:anthropic）。
    这些冲突与本测试无关，按「已被测试替身占用」跳过；其余冲突仍算失败。
    """
    import tempfile

    from clipwright.plugins.loader import PluginLoader

    loader = PluginLoader(
        plugin_dir=REPO / "plugins",
        data_dir=Path(tempfile.mkdtemp()),
    )
    failures: dict[str, str] = {}
    skipped: dict[str, str] = {}
    try:
        for plugin_id, _declared in _override_manifests():
            try:
                handle = loader.load(plugin_id)
            except Exception as e:  # noqa: BLE001 — 汇总后统一断言
                message = f"{type(e).__name__}: {e}"
                if "tests_" in message:
                    skipped[plugin_id] = message
                else:
                    failures[plugin_id] = message
                continue
            if handle is None:
                failures[plugin_id] = "load() 返回 None"
            else:
                loader.unload(plugin_id)
    finally:
        loader.stop_all_hosts()
    assert failures == {}, f"这些覆盖插件加载失败：{failures}"
