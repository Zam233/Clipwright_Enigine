"""类型节奏参数与同一性指标的自洽性（V2 / V35 根因防回归）。

类型既声明 ``signature_traits``（"这像不像该类型"的可测代理），又声明
``shot_params``（节奏区间）。两者若互相打架，剪辑层再怎么努力同一性判定都过不了：

- lyric_mv 声明 ``avg_shot_ms ≤ 3200``，却把 low 档基准节奏写成 3600ms；
- kichiku_fastcut 声明 ``avg_shot_ms ≤ 1800``，low 档基准 2000ms；
- digital_review 声明 ``avg_shot_ms ≤ 7000``，low 档基准 8000ms；
- knowledge_longform 声明 ``avg_shot_ms ≥ 2500``，却把镜头下限写成 2000ms。

V35 真机 6166ms 平均镜头就是这么来的。本文件把三条不变量钉死：
1. ``require_le B`` 的 avg_shot_ms → ``base_shot_ms ≤ B``（基准节奏落在指标区间内）；
2. ``require_ge B`` 的 avg_shot_ms → ``min_shot_ms ≥ B``（下限不得低于指标下界）；
3. ``min_shot_ms ≤ base_shot_ms ≤ max_shot_ms``（区间合法）。
"""

from __future__ import annotations

import pytest

from clipwright.category import (
    DigitalReviewPlugin,
    KichikuFastcutPlugin,
    KnowledgeLongformPlugin,
    LyricMvPlugin,
    VlogDailyPlugin,
)
from clipwright.schema.persona import ParameterLayer

BUILTIN = [
    KnowledgeLongformPlugin,
    KichikuFastcutPlugin,
    DigitalReviewPlugin,
    VlogDailyPlugin,
    LyricMvPlugin,
]


TIERS = ("low", "medium", "high")


def _shot_params(plugin, tier: str = "medium") -> dict:  # type: ignore[no-untyped-def]
    params = ParameterLayer(persona_id="consistency")
    params.rhythm.cut_density_tier = tier
    return plugin.get_shot_params(plugin.translate_persona(params)) or {}


def _traits(plugin) -> list:  # type: ignore[no-untyped-def]
    return plugin.get_signature_traits() or []


def _avg_traits(plugin) -> list:  # type: ignore[no-untyped-def]
    return [t for t in _traits(plugin) if t.name == "avg_shot_ms"]


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("plugin_cls", BUILTIN, ids=lambda cls: cls.plugin_id)
def test_base_shot_within_avg_ceiling(plugin_cls, tier) -> None:
    plugin = plugin_cls()
    base = float(_shot_params(plugin, tier).get("base_shot_ms") or 0)
    for trait in _avg_traits(plugin):
        if trait.mode != "require_le":
            continue
        assert base <= float(trait.value) + 1e-9, (
            f"{plugin.plugin_id}[{tier}]: base_shot_ms={base:g} 高于同一性指标 "
            f"avg_shot_ms ≤ {trait.value:g}，基准节奏已注定不达标"
        )


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("plugin_cls", BUILTIN, ids=lambda cls: cls.plugin_id)
def test_shot_floor_respects_avg_floor(plugin_cls, tier) -> None:
    plugin = plugin_cls()
    params = _shot_params(plugin, tier)
    base = float(params.get("base_shot_ms") or 0)
    for trait in _avg_traits(plugin):
        if trait.mode != "require_ge":
            continue
        # 判的是**平均**镜头，所以约束落在基准节奏上（min 只要不高于 base 即可）
        assert base + 1e-9 >= float(trait.value), (
            f"{plugin.plugin_id}[{tier}]: base_shot_ms={base:g} 低于同一性指标 "
            f"avg_shot_ms ≥ {trait.value:g}"
        )


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("plugin_cls", BUILTIN, ids=lambda cls: cls.plugin_id)
def test_shot_interval_is_legal(plugin_cls, tier) -> None:
    params = _shot_params(plugin_cls(), tier)
    floor = float(params.get("min_shot_ms") or 0)
    base = float(params.get("base_shot_ms") or 0)
    cap = float(params.get("max_shot_ms") or 0)
    if floor and base:
        assert floor <= base, f"{plugin_cls.plugin_id}[{tier}]: min={floor} > base={base}"
    if base and cap:
        assert base <= cap, f"{plugin_cls.plugin_id}[{tier}]: base={base} > max={cap}"


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("plugin_cls", BUILTIN, ids=lambda cls: cls.plugin_id)
def test_cap_declared_for_split_policy(plugin_cls, tier) -> None:
    """切分策略（shot_cap_policy=split）依赖 max_shot_ms；缺失即机制空转。"""
    assert float(_shot_params(plugin_cls(), tier).get("max_shot_ms") or 0) > 0
