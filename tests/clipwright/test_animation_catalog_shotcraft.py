"""Shotcraft CSS 关键帧目录测试 — Todo 11 (C5c)。

覆盖：
1. get_css_keyframes_all() 注册 hf-spotlight / hf-row-stagger / hf-deal-in
2. build_full_keyframes("spotlight", ...) 的 scale 落定变体
3. 命名冲突守卫：三个新名字不得覆盖既有关键帧
"""

from __future__ import annotations

import re

from clipwright.animation.catalog import AnimationCatalog

# 本任务新增的 shotcraft 关键帧名（对应 todo 10 的 mg 模板映射）
_SHOTCRAFT_NAMES = ("hf-spotlight", "hf-row-stagger", "hf-deal-in")


def _keyframe_names(css: str) -> list[str]:
    """从 get_css_keyframes_all() 返回的 CSS 中提取 @keyframes 名称。"""
    return re.findall(r"@keyframes\s+([A-Za-z0-9_-]+)", css)


class TestShotcraftCssKeyframes:
    """hf-* shotcraft 关键帧注册测试。"""

    def test_css_keyframes_all_contains_shotcraft(self) -> None:
        """get_css_keyframes_all() 应包含三个 shotcraft @keyframes。"""
        css = AnimationCatalog.get_css_keyframes_all()
        names = _keyframe_names(css)
        for name in _SHOTCRAFT_NAMES:
            assert name in names, f"get_css_keyframes_all() 缺少 @keyframes {name}"

    def test_shotcraft_names_no_collision(self) -> None:
        """命名冲突守卫：三个新名字不是既有名字，且不得重复定义。"""
        css = AnimationCatalog.get_css_keyframes_all()
        names = _keyframe_names(css)
        # 每个名字在输出中必须恰好出现一次（重复 @keyframes 即覆盖/冲突）
        for name in _SHOTCRAFT_NAMES:
            assert names.count(name) == 1, f"@keyframes {name} 重复定义"
        # 三个新名字不得出现在既有关键帧集合中
        other_names = set(names) - set(_SHOTCRAFT_NAMES)
        assert other_names.isdisjoint(_SHOTCRAFT_NAMES)
        # 既有关键帧必须原样保留（回归守卫）
        assert "hf-fade-in" in names


class TestBuildFullKeyframesSpotlight:
    """build_full_keyframes("spotlight", ...) 变体。"""

    def test_spotlight_returns_at_least_two_keyframes_with_opacity(self) -> None:
        """应返回 ≥2 个关键帧，且包含 opacity 0 → 1。"""
        kfs = AnimationCatalog.build_full_keyframes("spotlight", 0, 4)
        assert len(kfs) >= 2
        assert kfs[0]["properties"]["opacity"] == 0
        assert any(k["properties"]["opacity"] == 1 for k in kfs)

    def test_spotlight_scale_settle_hold(self) -> None:
        """shotcraft 语义：scale 落定 1→1.05 并保持到出场前。"""
        kfs = AnimationCatalog.build_full_keyframes("spotlight", 0, 4)
        # 入场阶段出现 scale 1.05（落定值）
        assert any(k["properties"].get("scale_x") == 1.05 for k in kfs)
        # 保持阶段（出场前最后一个不透明帧）仍是 1.05
        opaque = [k for k in kfs if k["properties"]["opacity"] == 1]
        assert opaque and opaque[-1]["properties"]["scale_x"] == 1.05
