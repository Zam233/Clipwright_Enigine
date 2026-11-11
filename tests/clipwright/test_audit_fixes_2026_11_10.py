"""2026-11-10 复核修复的回归用例。

覆盖：P1-14（entry_point 收敛）、P2-54（依赖启用状态）、P1-15（shot_params 秒→ms）、
P3-15（persona transition_weights 接线）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.category.dynamic import DynamicCategoryPlugin
from clipwright.plugins.loader import PluginLoader, PluginLoadError
from clipwright.schema.plugin import PluginManifest
from clipwright.services.visual_context import VisualContext, apply_rhythm_defaults


def test_dynamic_shot_params_are_ms() -> None:
    """P1-15：自定义类型的秒键必须换算成 EditAgent 消费的 ms 键。"""
    plugin = DynamicCategoryPlugin({
        "id": "t_ms",
        "name": "ms",
        "shot_params": {
            "min_shot_sec": 0.8,
            "max_shot_sec": 2.0,
            "transition_type": "fade",
            "cut_on_beat": False,
        },
    })
    params = plugin.get_shot_params({})
    assert params["min_shot_ms"] == 800
    assert params["max_shot_ms"] == 2000
    assert "min_shot_sec" not in params


def test_persona_transition_weights_injected() -> None:
    """P3-15：persona 的 visual.transition_weights 必须能进 extra_params。"""
    vc = VisualContext(persona_config={
        "visual": {"transition_weights": {"fade": 0.7, "hard_cut": 0.3}},
    })
    assert apply_rhythm_defaults({}, vc)["transition_weights"] == {
        "fade": 0.7, "hard_cut": 0.3,
    }
    # 显式值优先，不被 persona 覆盖
    explicit = apply_rhythm_defaults(
        {"transition_weights": {"wipe": 1.0}}, vc,
    )
    assert explicit["transition_weights"] == {"wipe": 1.0}


def test_disabled_dependency_counts_as_missing(tmp_path: Path) -> None:
    """P2-54：依赖被禁用后也算不可用（旧实现只看目录存在）。"""
    loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
    base = tmp_path / "plugins" / "base"
    base.mkdir(parents=True)
    (base / "plugin.yaml").write_text(
        "id: base\nname: base\nkind: capability\n", encoding="utf-8")
    loader.set_enabled("base", False)

    manifest = PluginManifest(
        id="dependent", name="dependent", kind="capability", dependencies=["base"],
    )
    assert loader._resolve_dependencies(manifest) == ["base"]


def test_entry_point_must_belong_to_plugin(tmp_path: Path) -> None:
    """P1-14：entry_point 不能指向顶层模块（防 shadow `requests` 之类）。"""
    loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
    evil = tmp_path / "plugins" / "evil"
    evil.mkdir(parents=True)
    (evil / "plugin.yaml").write_text(
        "id: evil\nname: evil\nversion: 1.0.0\nkind: capability\n"
        "entry_point: requests\n",
        encoding="utf-8",
    )
    # 不能靠 import requests 成功就放过：必须在导入前拒绝
    with pytest.raises(PluginLoadError, match="entry_point"):
        loader.load("evil")


def test_entry_point_with_own_package_still_loads(tmp_path: Path) -> None:
    """P1-14：合法入口（`<id>.main`）不受收敛影响。"""
    loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
    plugin_dir = tmp_path / "plugins" / "good"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: good\nname: good\nversion: 1.0.0\nkind: capability\n"
        "entry_point: good.main\n",
        encoding="utf-8",
    )
    (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
    (plugin_dir / "main.py").write_text(
        "from clipwright.plugins.base import CapabilityPlugin\n\n\n"
        "class GoodPlugin(CapabilityPlugin):\n"
        "    pass\n",
        encoding="utf-8",
    )
    # load 会实例化；只要不是 entry_point 相关的 PluginLoadError 即算通过
    try:
        loader.load("good")
    except PluginLoadError as e:  # pragma: no cover - 这里只排除入口拒绝
        assert "entry_point" not in str(e)


@pytest.mark.asyncio
async def test_circuit_open_refuses_dispatch() -> None:
    """P2-49：熔断后必须真的拒绝派发，而不是只记账。"""
    from types import SimpleNamespace

    from clipwright.schema.run import RunSpec
    from clipwright.services import agent_run, circuit_breaker

    key = circuit_breaker.key_of("structure")
    for _ in range(circuit_breaker.THRESHOLD):
        circuit_breaker.record_failure(key)

    result = await agent_run.run(
        RunSpec(kind="stage", stage="structure", brief="熔断用例"),
        SimpleNamespace(id="task_audit_breaker"),
    )
    assert result.status == "failed"
    assert "熔断" in (result.error or "")
    assert result.failure.get("category") == "circuit_open"

